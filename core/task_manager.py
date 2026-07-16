"""Background task records, history, retry, and optional persistence."""
from __future__ import annotations

import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Callable


@dataclass
class TaskEvent:
    task_id: str
    type: str
    message: str = ""
    data: dict = field(default_factory=dict)


@dataclass
class TaskRecord:
    task_id: str
    name: str
    status: str = "pending"
    created_at: str = ""
    started_at: str = ""
    finished_at: str = ""
    duration_ms: int = 0
    progress: int = 0
    stage: str = ""
    message: str = ""
    error: str = ""
    result_preview: str = ""
    retryable: bool = False
    retry_of: str = ""
    metadata: dict = field(default_factory=dict)
    events: list[dict] = field(default_factory=list)


class TaskHistoryStore:
    """Optional persistence for task records."""

    def load(self) -> list[TaskRecord]:
        return []

    def save(self, records: list[TaskRecord]) -> None:
        return None


class WorkspaceTaskHistoryStore(TaskHistoryStore):
    """Persists book-scoped task history under .deepseekass/tasks.json."""

    schema_version = 1
    relative_path = ".deepseekass/tasks.json"

    def __init__(self, workspace) -> None:
        self.workspace = workspace

    def load(self) -> list[TaskRecord]:
        data = self.workspace.storage.read_json(self.relative_path, default={})
        items = data.get("records", []) if isinstance(data, dict) else []
        records: list[TaskRecord] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            values = {name: item.get(name) for name in TaskRecord.__dataclass_fields__}
            values["metadata"] = values.get("metadata") or {}
            values["events"] = values.get("events") or []
            records.append(TaskRecord(**values))
        return records

    def save(self, records: list[TaskRecord]) -> None:
        self.workspace.storage.write_json(self.relative_path, {
            "schema_version": self.schema_version,
            "records": [asdict(record) for record in records],
        })


class TaskHandle:
    def __init__(self, task_id: str, progress_sink: Callable[[TaskEvent], None] | None = None) -> None:
        self.task_id = task_id
        self._cancelled = threading.Event()
        self._progress_sink = progress_sink or (lambda _event: None)

    def cancel(self) -> None:
        self._cancelled.set()

    @property
    def cancelled(self) -> bool:
        return self._cancelled.is_set()

    def progress(
        self,
        message: str = "",
        *,
        percent: int | None = None,
        stage: str = "",
        data: dict | None = None,
    ) -> None:
        payload = dict(data or {})
        if percent is not None:
            payload["progress"] = max(0, min(100, int(percent)))
        if stage:
            payload["stage"] = stage
        self._progress_sink(TaskEvent(
            task_id=self.task_id,
            type="progress",
            message=message,
            data=payload,
        ))


class TaskRunner:
    """Runs background application tasks through one event and history contract."""

    def __init__(
        self,
        event_sink: Callable[[TaskEvent], None] | None = None,
        *,
        history_store: TaskHistoryStore | None = None,
        history_limit: int = 200,
    ) -> None:
        self.event_sink = event_sink or (lambda _event: None)
        self._handles: dict[str, TaskHandle] = {}
        self._targets: dict[str, tuple[str, Callable[[TaskHandle], object], dict, bool]] = {}
        self._records: dict[str, TaskRecord] = {}
        self._order: list[str] = []
        self._history_store = history_store
        self._history_limit = max(10, int(history_limit))
        self._lock = threading.Lock()
        if history_store is not None:
            for record in history_store.load():
                self._records[record.task_id] = record
                self._order.append(record.task_id)

    def start(
        self,
        name: str,
        target: Callable[[TaskHandle], object],
        *,
        retryable: bool = False,
        metadata: dict | None = None,
        retry_of: str = "",
    ) -> TaskHandle:
        task_id = f"task_{uuid.uuid4().hex[:12]}"
        handle = TaskHandle(task_id, self._handle_progress)
        record = TaskRecord(
            task_id=task_id,
            name=name,
            created_at=self._now(),
            retryable=retryable,
            retry_of=retry_of,
            metadata=dict(metadata or {}),
        )
        with self._lock:
            self._handles[task_id] = handle
            self._records[task_id] = record
            self._order.append(task_id)
            if retryable:
                self._targets[task_id] = (name, target, dict(metadata or {}), retryable)
            self._trim_locked()
            self._persist_locked()

        def run() -> None:
            started = time.time()
            self._emit(handle, "started", name)
            try:
                result = target(handle)
                if handle.cancelled:
                    self._emit(handle, "cancelled", name)
                else:
                    self._emit(handle, "completed", name, {"result": self._preview(result)})
            except Exception as exc:
                if handle.cancelled:
                    self._emit(handle, "cancelled", name)
                else:
                    self._emit(handle, "failed", str(exc), {"exception_type": type(exc).__name__})
            finally:
                self._emit(handle, "finished", name, {"duration_ms": int((time.time() - started) * 1000)})
                with self._lock:
                    self._handles.pop(task_id, None)

        threading.Thread(target=run, daemon=True, name=task_id).start()
        return handle

    def cancel(self, task_id: str) -> bool:
        with self._lock:
            handle = self._handles.get(task_id)
        if handle is None:
            return False
        handle.cancel()
        return True

    def active(self) -> list[str]:
        with self._lock:
            return list(self._handles)

    def active_records(self) -> list[TaskRecord]:
        with self._lock:
            return [
                self._copy_record(self._records[task_id])
                for task_id in self._handles
                if task_id in self._records
            ]

    def history(self, limit: int | None = None) -> list[TaskRecord]:
        with self._lock:
            ids = self._order[-limit:] if limit else self._order
            return [
                self._copy_record(self._records[task_id])
                for task_id in reversed(ids)
                if task_id in self._records
            ]

    def get_record(self, task_id: str) -> TaskRecord | None:
        with self._lock:
            record = self._records.get(task_id)
            return self._copy_record(record) if record is not None else None

    def retry(self, task_id: str) -> TaskHandle:
        with self._lock:
            target = self._targets.get(task_id)
            record = self._records.get(task_id)
        if target is None or record is None or not record.retryable:
            raise ValueError("Task is not retryable")
        name, callback, metadata, retryable = target
        return self.start(name, callback, retryable=retryable, metadata=metadata, retry_of=task_id)

    def _emit(self, handle: TaskHandle, event_type: str, message: str, data=None) -> None:
        event = TaskEvent(
            task_id=handle.task_id,
            type=event_type,
            message=message,
            data=data or {},
        )
        self._record_event(event)
        self.event_sink(event)

    def _handle_progress(self, event: TaskEvent) -> None:
        self._record_event(event)
        self.event_sink(event)

    def _record_event(self, event: TaskEvent) -> None:
        with self._lock:
            record = self._records.get(event.task_id)
            if record is None:
                return
            record.message = event.message or record.message
            record.events.append({
                "at": self._now(),
                "type": event.type,
                "message": event.message,
                "data": self._serializable(event.data),
            })
            if len(record.events) > 50:
                record.events = record.events[-50:]
            if event.type == "started":
                record.status = "running"
                record.started_at = self._now()
            elif event.type == "progress":
                record.status = "running"
                if "progress" in event.data:
                    record.progress = max(0, min(100, int(event.data.get("progress") or 0)))
                if event.data.get("stage"):
                    record.stage = str(event.data.get("stage"))
            elif event.type == "completed":
                record.status = "completed"
                record.progress = 100
                record.result_preview = str(event.data.get("result", ""))[:500]
            elif event.type == "cancelled":
                record.status = "cancelled"
                record.finished_at = self._now()
            elif event.type == "failed":
                record.status = "failed"
                record.error = event.message
                record.finished_at = self._now()
            elif event.type == "finished":
                if record.status == "running":
                    record.status = "completed"
                record.finished_at = self._now()
                record.duration_ms = int(event.data.get("duration_ms") or 0)
            self._persist_locked()

    def _trim_locked(self) -> None:
        while len(self._order) > self._history_limit:
            task_id = self._order.pop(0)
            if task_id in self._handles:
                self._order.insert(0, task_id)
                break
            self._records.pop(task_id, None)
            self._targets.pop(task_id, None)

    def _persist_locked(self) -> None:
        if self._history_store is None:
            return
        self._history_store.save([
            self._records[task_id]
            for task_id in self._order
            if task_id in self._records
        ])

    @staticmethod
    def _now() -> str:
        return datetime.now().isoformat(timespec="seconds")

    @staticmethod
    def _preview(value: object) -> str:
        text = "" if value is None else str(value)
        return text[:500]

    @staticmethod
    def _serializable(data: dict) -> dict:
        clean = {}
        for key, value in (data or {}).items():
            if isinstance(value, (str, int, float, bool)) or value is None:
                clean[key] = value
            else:
                clean[key] = str(value)
        return clean

    @staticmethod
    def _copy_record(record: TaskRecord) -> TaskRecord:
        return TaskRecord(**asdict(record))

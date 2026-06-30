"""Application services extracted from UI concerns."""
from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Callable


@dataclass
class TaskEvent:
    task_id: str
    type: str
    message: str = ""
    data: dict = field(default_factory=dict)


class TaskHandle:
    def __init__(self, task_id: str) -> None:
        self.task_id = task_id
        self._cancelled = threading.Event()

    def cancel(self) -> None:
        self._cancelled.set()

    @property
    def cancelled(self) -> bool:
        return self._cancelled.is_set()


class TaskRunner:
    """Runs background application tasks through one event contract."""

    def __init__(self, event_sink: Callable[[TaskEvent], None] | None = None) -> None:
        self.event_sink = event_sink or (lambda _event: None)
        self._handles: dict[str, TaskHandle] = {}
        self._lock = threading.Lock()

    def start(self, name: str, target: Callable[[TaskHandle], object]) -> TaskHandle:
        task_id = f"task_{uuid.uuid4().hex[:12]}"
        handle = TaskHandle(task_id)
        with self._lock:
            self._handles[task_id] = handle

        def run() -> None:
            started = time.time()
            self._emit(handle, "started", name)
            try:
                result = target(handle)
                if handle.cancelled:
                    self._emit(handle, "cancelled", name)
                else:
                    self._emit(handle, "completed", name, {"result": result})
            except Exception as exc:
                self._emit(handle, "failed", str(exc), {"exception": exc})
            finally:
                self._emit(handle, "finished", name, {
                    "duration_ms": int((time.time() - started) * 1000)
                })
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

    def _emit(self, handle: TaskHandle, event_type: str, message: str, data=None) -> None:
        self.event_sink(TaskEvent(
            task_id=handle.task_id,
            type=event_type,
            message=message,
            data=data or {},
        ))


class WorldBibleSyncService:
    def __init__(self, novel_manager) -> None:
        self.novel_manager = novel_manager

    def sync_chapter(
        self,
        client,
        title: str,
        chapter_num: int,
        version: int,
        content: str,
        *,
        model: str,
        global_user_prompt: str = "",
        xp_mode: bool = False,
    ):
        from core.world_bible import extract_and_merge_world_bible
        bible = self.novel_manager.load_world_bible(title)
        updated = extract_and_merge_world_bible(
            client,
            content,
            chapter_num,
            bible,
            model,
            chapter_version=version,
            global_user_prompt=global_user_prompt,
            xp_mode=xp_mode,
        )
        self.novel_manager.save_world_bible(title, updated)
        return updated


class ChapterGenerationService:
    """Coordinates context and persistence while model generation stays injectable."""

    def __init__(self, novel_manager) -> None:
        self.novel_manager = novel_manager
        self.contexts = novel_manager.context_assembler()
        self.world_bible = WorldBibleSyncService(novel_manager)

    def build_context(self, *args, **kwargs):
        return self.contexts.assemble_chapter(*args, **kwargs)

    def persist_chapter(
        self,
        *,
        title: str,
        chapter_num: int,
        chapter_title: str,
        content: str,
        version: int,
        parent_id: str | None,
        prompt: str,
        model: str,
        temperature: float,
        top_p: float,
        max_tokens: int,
        frequency_penalty: float,
        supervision_report: dict | None = None,
        requirement: str = "",
        plot: str = "",
        agent_data: dict | None = None,
        world_maintenance_report: dict | None = None,
        generation_mode: str = "classic",
        agent_run_id: str | None = None,
    ) -> tuple[str, int]:
        file_path, saved_version = self.novel_manager.save_chapter_version(
            title,
            chapter_num,
            chapter_title,
            content,
            version=version,
            parent_id=parent_id,
        )
        self.novel_manager.switch_active_node(
            title, self.novel_manager._node_id(chapter_num, saved_version)
        )
        self.novel_manager.save_generation_record(
            title=title,
            chapter_num=chapter_num,
            chapter_title=chapter_title,
            version=saved_version,
            prompt=prompt,
            model=model,
            temperature=temperature,
            top_p=top_p,
            max_tokens=max_tokens,
            frequency_penalty=frequency_penalty,
            content_preview=content.replace("\n", " "),
            requirement=requirement,
            plot=plot,
            supervision_report=supervision_report,
            agent_data=agent_data,
            world_maintenance_report=world_maintenance_report,
            generation_mode=generation_mode,
            agent_run_id=agent_run_id,
        )
        return file_path, saved_version

    def create_auto_snapshot(self, title: str, chapter_num: int, version: int):
        return self.novel_manager.snapshot_service(title).create(
            f"第{chapter_num}章 v{version} 生成完成",
            source="chapter",
        )


class ContinuationService(ChapterGenerationService):
    def build_context(self, *args, **kwargs):
        return self.contexts.assemble_continuation(*args, **kwargs)


class RoleplayService:
    def fork(self, state, message_id: str, title: str = ""):
        from core.chat_domain import fork_branch
        return fork_branch(state, message_id, title)

    def apply_memory(self, book, change_set) -> None:
        from core.chat_domain import apply_memory_change_set
        apply_memory_change_set(book, change_set)

    def revert_memory(self, book, change_set) -> None:
        from core.chat_domain import revert_memory_change_set
        revert_memory_change_set(book, change_set)


class ImportExportService:
    def export_chapter(self, manager, title: str, chapter: int, fmt: str, output: str):
        from utils.export import export_chapter
        return export_chapter(manager, title, chapter, fmt, output)

    def export_book(self, manager, title: str, fmt: str, output: str):
        from utils.export import export_book
        return export_book(manager, title, fmt, output)

# Compatibility exports: new code should import from core.task_manager directly.
from core.task_manager import (
    TaskEvent as TaskEvent,
    TaskHandle as TaskHandle,
    TaskHistoryStore as TaskHistoryStore,
    TaskRecord as TaskRecord,
    TaskRunner as TaskRunner,
    WorkspaceTaskHistoryStore as WorkspaceTaskHistoryStore,
)

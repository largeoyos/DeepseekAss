from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import asdict
from typing import Callable

from core.agent.memory import ContextCompactor
from core.agent.context import AgentContextAssembler
from core.agent.middleware import SafetyMiddleware
from core.agent.model import AgentModelAdapter
from core.agent.profiles import build_system_prompt, get_agent_profile
from core.agent.repository import AgentRepository
from core.agent.skills import SkillService
from core.agent.tools import ToolContext, ToolRegistry
from core.agent.types import (
    AgentEvent,
    AgentRun,
    AgentRunRequest,
    append_request_user_message,
    now_iso,
)


class AgentCancelled(RuntimeError):
    pass


class AgentControl:
    def __init__(self) -> None:
        self.cancelled = threading.Event()
        self.paused = threading.Event()

    def cancel(self) -> None:
        self.cancelled.set()

    def pause(self) -> None:
        self.paused.set()

    def resume(self) -> None:
        self.paused.clear()

    def wait_if_paused(self) -> None:
        while self.paused.is_set() and not self.cancelled.is_set():
            time.sleep(0.05)


class AgentRuntime:
    def __init__(self, *, novel_manager, client, tool_registry: ToolRegistry, event_sink: Callable[[AgentEvent], None] | None = None, services: dict | None = None, middleware: list | None = None, skills_enabled: bool = True) -> None:
        self.novel_manager = novel_manager
        self.client = client
        self.tool_registry = tool_registry
        self.event_sink = event_sink or (lambda _event: None)
        self.services = services or {}
        self.compactor = ContextCompactor()
        self.contexts = AgentContextAssembler(novel_manager)
        self.middleware = middleware or [SafetyMiddleware()]
        self.skills_enabled = skills_enabled
        self._controls: dict[str, AgentControl] = {}
        self._sequence: dict[str, int] = {}
        self._lock = threading.Lock()

    def create_session(self, book_title: str, agent_kind: str, title: str = ""):
        workspace = self.novel_manager.get_workspace(book_title)
        manifest = self.novel_manager.ensure_workspace(book_title)
        manifest.features["agent_runtime"] = True
        manifest.features["agent_schema_version"] = 1
        manifest.features["agent_skills"] = self.skills_enabled
        workspace.storage.write_json(workspace.manifest_path, asdict(manifest))
        return AgentRepository(workspace).create_session(manifest.book_id, book_title, agent_kind, title or get_agent_profile(agent_kind).display_name)

    def run(self, request: AgentRunRequest) -> AgentRun:
        profile = get_agent_profile(request.agent_kind)
        repository = AgentRepository(self.novel_manager.get_workspace(request.book_title))
        session = repository.load_session(request.session_id)
        if session is None:
            raise ValueError("Agent 会话不存在")
        run = AgentRun(f"run_{uuid.uuid4().hex}", request.session_id, request.book_id, request.book_title, request.agent_kind, request.model, status="running", mode=request.mode)
        control = AgentControl()
        with self._lock:
            self._controls[run.run_id] = control
        session.active_run_id = run.run_id
        session.run_ids.append(run.run_id)
        append_request_user_message(session, request)
        skills = SkillService(repository).render_for_agent(request.agent_kind) if self.skills_enabled else ""
        context_report = self.contexts.assemble(request.book_title, request.manual_references)
        run.messages = [
            {"role": "system", "content": build_system_prompt(profile, skills)},
            {"role": "system", "content": "当前书籍事实上下文：\n" + context_report.content},
            *[{"role": m["role"], "content": m["content"]} for m in session.messages],
        ]
        for item in self.middleware:
            item.before_run(run, request, context_report)
        repository.save_session(session)
        repository.save_run(run)
        self._emit(repository, run.run_id, "run_started", {"agent_kind": run.agent_kind})
        adapter = AgentModelAdapter(self.client, request.model)
        repeated: dict[str, int] = {}
        try:
            for iteration in range(1, min(50, profile.max_iterations) + 1):
                control.wait_if_paused()
                if control.cancelled.is_set():
                    raise AgentCancelled()
                run.iteration = iteration
                for item in self.middleware:
                    item.before_turn(run)
                if self.compactor.needs_compaction(run.messages, profile.context_budget):
                    run.messages, epoch = self.compactor.compact(run.messages)
                    if epoch:
                        session.epochs.append(epoch)
                        self._emit(repository, run.run_id, "context_compacted", epoch)
                turn = adapter.complete(
                    run.messages,
                    self.tool_registry.schemas_for(profile.allowed_tools),
                    require_tool=(run.agent_kind == "writing_advisor" and iteration == 1),
                )
                run.planning_only = run.planning_only or turn.planning_only
                run.usage = self._merge_usage(run.usage, turn.usage)
                self._emit(repository, run.run_id, "usage_updated", run.usage)
                assistant_message = {"role": "assistant", "content": turn.content}
                if turn.tool_calls:
                    assistant_message["tool_calls"] = [{"id": call.call_id, "type": "function", "function": {"name": call.tool_name, "arguments": json.dumps(call.arguments, ensure_ascii=False)}} for call in turn.tool_calls]
                run.messages.append(assistant_message)
                if turn.content:
                    self._emit(repository, run.run_id, "model_stream", {"text": turn.content})
                if not turn.tool_calls or run.planning_only:
                    run.status = "completed"
                    run.terminal_reason = "planning_only" if run.planning_only else "model_completed"
                    break
                for call in turn.tool_calls:
                    signature = f"{call.tool_name}:{json.dumps(call.arguments, sort_keys=True, ensure_ascii=False)}"
                    repeated[signature] = repeated.get(signature, 0) + 1
                    if repeated[signature] > 3:
                        raise RuntimeError(f"检测到重复无进展工具调用: {call.tool_name}")
                    self._emit(repository, run.run_id, "tool_requested", asdict(call))
                    context = ToolContext(run.run_id, run.book_id, run.book_title, run.agent_kind, profile.permission_level, repository, self.services)
                    self._emit(repository, run.run_id, "tool_started", {"tool_name": call.tool_name})
                    result = self.tool_registry.execute(call, context, profile.allowed_tools)
                    for item in self.middleware:
                        item.after_tool(run, call, result)
                    run.tool_calls.append({"request": asdict(call), "result": asdict(result), "at": now_iso()})
                    if result.artifact_id:
                        run.artifact_ids.append(result.artifact_id)
                    if result.structured_data.get("change_set_id"):
                        run.change_set_ids.append(result.structured_data["change_set_id"])
                    run.messages.append({"role": "tool", "tool_call_id": call.call_id, "content": result.content})
                    self._emit(repository, run.run_id, "tool_completed", asdict(result))
                    if result.structured_data.get("requires_approval"):
                        run.status = "waiting_approval"
                        run.terminal_reason = "approval_required"
                        self._emit(repository, run.run_id, "approval_required", result.structured_data)
                repository.save_run(run)
                checkpoint_id = repository.save_checkpoint(run, "iteration_completed")
                self._emit(repository, run.run_id, "checkpoint_saved", {"checkpoint_id": checkpoint_id})
                if run.status == "waiting_approval":
                    break
            else:
                run.status, run.terminal_reason, run.error = "failed", "max_iterations", "Agent 达到最大迭代次数"
        except AgentCancelled:
            run.status, run.terminal_reason = "cancelled", "user_cancelled"
            self._emit(repository, run.run_id, "run_cancelled", {})
        except Exception as exc:
            run.status, run.error, run.terminal_reason = "failed", str(exc), "runtime_error"
            self._emit(repository, run.run_id, "run_failed", {"error": str(exc)})
        finally:
            for item in self.middleware:
                item.after_run(run)
            repository.save_run(run)
            repository.save_checkpoint(run, "terminal")
            if session.active_run_id == run.run_id:
                session.active_run_id = ""
            assistants = [m for m in run.messages if m.get("role") == "assistant" and m.get("content")]
            if assistants:
                session.messages.append({"role": "assistant", "content": assistants[-1]["content"], "at": now_iso()})
            repository.save_session(session)
            with self._lock:
                self._controls.pop(run.run_id, None)
            if run.status == "completed":
                self._emit(repository, run.run_id, "run_completed", {"terminal_reason": run.terminal_reason, "planning_only": run.planning_only})
        return run

    def restore(self, book_title: str, run_id: str) -> AgentRun | None:
        return AgentRepository(self.novel_manager.get_workspace(book_title)).load_latest_checkpoint(run_id)

    def cancel(self, run_id: str) -> bool:
        control = self._controls.get(run_id)
        if not control:
            return False
        control.cancel()
        return True

    def pause(self, run_id: str) -> bool:
        control = self._controls.get(run_id)
        if not control:
            return False
        control.pause()
        return True

    def resume(self, run_id: str) -> bool:
        control = self._controls.get(run_id)
        if not control:
            return False
        control.resume()
        return True

    def _emit(self, repository: AgentRepository, run_id: str, event_type: str, payload: dict) -> None:
        sequence = self._sequence.get(run_id, 0) + 1
        self._sequence[run_id] = sequence
        event = AgentEvent(run_id, sequence, event_type, payload=payload)
        repository.append_event(run_id, asdict(event))
        self.event_sink(event)

    @staticmethod
    def _merge_usage(current: dict, new: dict) -> dict:
        result = dict(current or {})
        for key, value in (new or {}).items():
            if isinstance(value, (int, float)):
                result[key] = result.get(key, 0) + value
        return result

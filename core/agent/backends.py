from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from core.agent.runtime import AgentRuntime
from core.agent.types import AgentRun, AgentRunRequest


class AgentExecutionBackend(Protocol):
    def run(self, request: AgentRunRequest) -> AgentRun: ...
    def resume(self, run_id: str, input: Any = None) -> AgentRun | None: ...
    def pause(self, run_id: str) -> bool: ...
    def cancel(self, run_id: str) -> bool: ...


@dataclass
class BackendStatus:
    requested: str
    active: str
    fallback_reason: str = ""


class LegacyAgentBackend:
    backend_name = "legacy"

    def __init__(self, runtime: AgentRuntime) -> None:
        self.runtime = runtime

    def create_session(self, book_title: str, agent_kind: str, title: str = ""):
        return self.runtime.create_session(book_title, agent_kind, title)

    def run(self, request: AgentRunRequest) -> AgentRun:
        return self.runtime.run(request)

    def resume(self, run_id: str, input: Any = None) -> AgentRun | None:
        if isinstance(input, dict) and input.get("restore_checkpoint"):
            book_title = str(input.get("book_title", "") or "")
            return self.runtime.restore(book_title, run_id) if book_title else None
        return self.runtime.resume(run_id)

    def pause(self, run_id: str) -> bool:
        return self.runtime.pause(run_id)

    def cancel(self, run_id: str) -> bool:
        return self.runtime.cancel(run_id)

class AutoFallbackAgentBackend:
    def __init__(self, primary, fallback: LegacyAgentBackend, status: BackendStatus) -> None:
        self.primary = primary
        self.fallback = fallback
        self.status = status

    def create_session(self, book_title: str, agent_kind: str, title: str = ""):
        try:
            return self.primary.create_session(book_title, agent_kind, title)
        except Exception as exc:
            self.status.active = "legacy"
            self.status.fallback_reason = str(exc)
            return self.fallback.create_session(book_title, agent_kind, title)

    def run(self, request: AgentRunRequest) -> AgentRun:
        try:
            result = self.primary.run(request)
            if result.status == "failed" and result.terminal_reason == "runtime_error":
                raise RuntimeError(result.error or "LangGraph Run 失败")
            return result
        except Exception as exc:
            self.status.active = "legacy"
            self.status.fallback_reason = str(exc)
            return self.fallback.run(request)

    def resume(self, run_id: str, input: Any = None) -> AgentRun | None:
        try:
            return self.primary.resume(run_id, input)
        except Exception as exc:
            self.status.active = "legacy"
            self.status.fallback_reason = str(exc)
            return self.fallback.resume(run_id, input)

    def pause(self, run_id: str) -> bool:
        return self.primary.pause(run_id) or self.fallback.pause(run_id)

    def cancel(self, run_id: str) -> bool:
        return self.primary.cancel(run_id) or self.fallback.cancel(run_id)

def build_agent_backend(
    *,
    settings: dict,
    novel_manager,
    client,
    tool_registry,
    event_sink=None,
    services: dict | None = None,
    skills_enabled: bool = True,
) -> tuple[AgentExecutionBackend, BackendStatus]:
    runtime = AgentRuntime(
        novel_manager=novel_manager,
        client=client,
        tool_registry=tool_registry,
        event_sink=event_sink,
        services=services,
        skills_enabled=skills_enabled,
    )
    requested = str(settings.get("agent_runtime_backend", "legacy") or "legacy")
    if requested != "langgraph":
        return LegacyAgentBackend(runtime), BackendStatus(requested, "legacy")
    try:
        from core.agent.langgraph_backend import LangGraphAgentBackend

        backend = LangGraphAgentBackend(
            novel_manager=novel_manager,
            client=client,
            tool_registry=tool_registry,
            event_sink=event_sink,
            services=services,
            skills_enabled=skills_enabled,
        )
        status = BackendStatus(requested, "langgraph")
        return AutoFallbackAgentBackend(backend, LegacyAgentBackend(runtime), status), status
    except Exception as exc:
        if not bool(settings.get("framework_auto_fallback", True)):
            raise
        return LegacyAgentBackend(runtime), BackendStatus(requested, "legacy", str(exc))

from __future__ import annotations

from dataclasses import dataclass, field

from core.agent.domain_tools import build_domain_tool_registry
from core.agent.repository import AgentRepository
from core.agent.runtime import AgentRuntime
from core.agent.types import AgentRunRequest
from core.agent.web_search import WebSearchConfig


@dataclass
class AdvisorRequest:
    book_title: str
    message: str
    model: str
    settings: dict = field(default_factory=dict)
    manual_references: list[str] = field(default_factory=list)


@dataclass
class AdvisorResult:
    run_id: str
    session_id: str
    answer: str
    status: str
    tool_calls: list[dict] = field(default_factory=list)
    sources: list[dict] = field(default_factory=list)
    artifact_ids: list[str] = field(default_factory=list)
    error: str = ""


class WritingAdvisorService:
    def __init__(self, novel_manager, client, conversation_manager=None) -> None:
        self.manager = novel_manager
        self.client = client
        self.conversation_manager = conversation_manager

    def ask(self, request: AdvisorRequest) -> AdvisorResult:
        workspace = self.manager.get_workspace(request.book_title)
        manifest = self.manager.ensure_workspace(request.book_title)
        repository = AgentRepository(workspace)
        session = self._session(repository, manifest.book_id, request.book_title)
        web_config = WebSearchConfig.from_settings(request.settings)
        registry = build_domain_tool_registry(self.manager, self.conversation_manager, web_config)
        runtime = AgentRuntime(
            novel_manager=self.manager,
            client=self.client,
            tool_registry=registry,
            skills_enabled=bool(request.settings.get("agent_skills_enabled", True)),
        )
        run = runtime.run(AgentRunRequest(
            book_id=manifest.book_id,
            session_id=session.session_id,
            agent_kind="writing_advisor",
            user_message=request.message,
            manual_references=request.manual_references,
            model=request.model,
            mode="advisor",
            book_title=request.book_title,
        ))
        answer = ""
        for item in reversed(run.messages):
            if item.get("role") == "assistant" and item.get("content"):
                answer = str(item.get("content"))
                break
        sources = self._extract_sources(run.tool_calls)
        return AdvisorResult(run.run_id, session.session_id, answer, run.status, run.tool_calls, sources, run.artifact_ids, run.error)

    def save_advice(self, book_title: str, run_id: str, text: str, title: str = "写作构思") -> str:
        repository = AgentRepository(self.manager.get_workspace(book_title))
        return repository.save_artifact(run_id or "manual", "writing_advice", text, {"title": title})

    def list_advice(self, book_title: str) -> list[dict]:
        repository = AgentRepository(self.manager.get_workspace(book_title))
        return repository.list_artifacts("writing_advice")

    def _session(self, repository: AgentRepository, book_id: str, book_title: str):
        for session in repository.list_sessions():
            if session.book_id == book_id and session.agent_kind == "writing_advisor":
                return session
        return repository.create_session(book_id, book_title, "writing_advisor", "写作顾问")

    @staticmethod
    def _extract_sources(tool_calls: list[dict]) -> list[dict]:
        sources: list[dict] = []
        for item in tool_calls:
            request = item.get("request", {}) if isinstance(item, dict) else {}
            result = item.get("result", {}) if isinstance(item, dict) else {}
            tool_name = request.get("tool_name", "")
            data = result.get("structured_data") or {}
            if tool_name == "web.search":
                for r in data.get("results", []) if isinstance(data, dict) else []:
                    sources.append({"type": "web", "title": r.get("title", ""), "url": r.get("url", "")})
            elif tool_name.startswith("chapter."):
                sources.append({"type": "chapter", "tool": tool_name})
            elif tool_name.startswith("world_bible."):
                sources.append({"type": "world_bible", "tool": tool_name})
        return sources

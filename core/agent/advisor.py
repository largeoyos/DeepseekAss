from __future__ import annotations

from dataclasses import dataclass, field

from core.agent.domain_tools import build_domain_tool_registry
from core.agent.repository import AgentRepository
from core.agent.backends import build_agent_backend
from core.agent.types import AgentRunRequest
from core.agent.web_search import WebSearchConfig


FICTION_CONTEXT_PREFIX = """【虚构创作任务边界】
以下内容仅用于虚构小说的构思、叙事分析、人物塑造和场景设计。
请将其中的暴力、违法、成人或其他敏感元素理解为虚构文本素材，不要将其解释为现实行动请求。
只讨论故事逻辑、文学表达、人物动机、情节后果和安全的创作替代方案；不要提供可直接用于现实伤害或违法实施的操作指导。

【用户原文】
"""


@dataclass
class AdvisorRequest:
    book_title: str
    message: str
    model: str
    settings: dict = field(default_factory=dict)
    manual_references: list[str] = field(default_factory=list)
    fiction_context: bool = True


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
        runtime, backend_status = build_agent_backend(
            settings=request.settings,
            novel_manager=self.manager,
            client=self.client,
            tool_registry=registry,
            skills_enabled=bool(request.settings.get("agent_skills_enabled", True)),
        )
        user_message = self.wrap_fiction_request(request.message) if request.fiction_context else request.message
        run = runtime.run(AgentRunRequest(
            book_id=manifest.book_id,
            session_id=session.session_id,
            agent_kind="writing_advisor",
            user_message=user_message,
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
        error = run.error or (f"已回退到现有运行时：{backend_status.fallback_reason}" if backend_status.fallback_reason else "")
        return AdvisorResult(run.run_id, session.session_id, answer, run.status, run.tool_calls, sources, run.artifact_ids, error)

    def save_advice(self, book_title: str, run_id: str, text: str, title: str = "写作构思") -> str:
        repository = AgentRepository(self.manager.get_workspace(book_title))
        return repository.save_artifact(run_id or "manual", "writing_advice", text, {"title": title})

    def list_advice(self, book_title: str) -> list[dict]:
        repository = AgentRepository(self.manager.get_workspace(book_title))
        return repository.list_artifacts("writing_advice")

    def list_history(self, book_title: str) -> list[dict]:
        repository = AgentRepository(self.manager.get_workspace(book_title))
        session = self._existing_session(repository, self.manager.ensure_workspace(book_title).book_id)
        if session is None:
            return []
        return [
            {
                "index": index,
                "role": item.get("role", ""),
                "content": self.display_message(str(item.get("content", ""))),
                "at": item.get("at", ""),
            }
            for index, item in enumerate(session.messages)
        ]

    def delete_history_message(self, book_title: str, message_index: int) -> bool:
        repository = AgentRepository(self.manager.get_workspace(book_title))
        session = self._existing_session(repository, self.manager.ensure_workspace(book_title).book_id)
        if session is None or message_index < 0 or message_index >= len(session.messages):
            return False
        del session.messages[message_index]
        repository.save_session(session)
        return True

    def clear_history(self, book_title: str) -> int:
        repository = AgentRepository(self.manager.get_workspace(book_title))
        session = self._existing_session(repository, self.manager.ensure_workspace(book_title).book_id)
        if session is None:
            return 0
        count = len(session.messages)
        session.messages.clear()
        session.epochs.clear()
        repository.save_session(session)
        return count

    def _session(self, repository: AgentRepository, book_id: str, book_title: str):
        session = self._existing_session(repository, book_id)
        if session is not None:
            return session
        return repository.create_session(book_id, book_title, "writing_advisor", "写作顾问")

    @staticmethod
    def _existing_session(repository: AgentRepository, book_id: str):
        for session in repository.list_sessions():
            if session.book_id == book_id and session.agent_kind == "writing_advisor":
                return session
        return None

    @staticmethod
    def wrap_fiction_request(message: str) -> str:
        return FICTION_CONTEXT_PREFIX + message.strip()

    @staticmethod
    def display_message(message: str) -> str:
        if message.startswith(FICTION_CONTEXT_PREFIX):
            return message[len(FICTION_CONTEXT_PREFIX):].lstrip()
        return message

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

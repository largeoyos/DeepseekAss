"""Backward-compatible facade over the controlled Agent domain layer."""
from __future__ import annotations

from core.agent.changes import ChangeSetService
from core.agent.repository import AgentRepository

READ_ONLY = "read_only"
DRAFT_WRITE = "draft_write"
CONFIRM_WRITE = "confirm_write"


class AgentPermissionError(PermissionError):
    pass


class ControlledAgentTools:
    """Compatibility API. New Agent code should use ToolRegistry."""

    def __init__(self, novel_manager, title: str, permission: str = READ_ONLY) -> None:
        aliases = {CONFIRM_WRITE: "confirmed_write"}
        permission = aliases.get(permission, permission)
        if permission not in {READ_ONLY, DRAFT_WRITE, "confirmed_write"}:
            raise ValueError(f"未知 Agent 权限: {permission}")
        self.manager, self.title, self.permission = novel_manager, title, permission
        self.workspace = novel_manager.get_workspace(title)
        self.repository = AgentRepository(self.workspace)
        self._pending = []

    def read_chapter(self, chapter_num: int) -> str:
        return self.manager.read_active_chapter(self.title, chapter_num) or ""

    def search(self, query: str, limit: int = 20) -> list[dict]:
        from core.agent.domain_tools import build_domain_tool_registry
        from core.agent.tools import ToolContext
        from core.agent.types import ToolCallRequest
        result = build_domain_tool_registry(self.manager).execute(ToolCallRequest("compat", "chapter.search", {"query": query, "limit": limit}), ToolContext("compat", "", self.title, "writing_orchestrator", self.permission, self.repository), ["chapter.search"])
        return result.structured_data.get("results", [])

    def read_world_bible(self) -> dict:
        from core.world_bible import world_bible_to_dict
        return world_bible_to_dict(self.manager.load_world_bible(self.title))

    def context_report(self, chapter_num: int, chapter_title: str, plot: str = "") -> dict:
        report = self.manager.context_assembler().assemble_chapter(self.title, chapter_num, chapter_title, plot)
        return {"preview": report.preview(), "context": report.render()}

    def write_draft(self, name: str, content: str) -> str:
        if self.permission == READ_ONLY:
            raise AgentPermissionError("当前 Agent 仅允许读取")
        return self.repository.save_draft("compat", name, content)

    def propose_chapter(self, chapter_num: int, chapter_title: str, content: str, parent_id: str | None = None):
        if self.permission != "confirmed_write":
            raise AgentPermissionError("当前 Agent 无权提交正式章节变更")
        manifest = self.manager.ensure_workspace(self.title)
        change = ChangeSetService(self.manager, self.title, self.repository).propose_chapter("compat", manifest.book_id, chapter_num, chapter_title, content, parent_id or "")
        self._pending.append(change.change_set_id)
        return change.operations[0]

    def confirm(self, change_id: str):
        change_set_id = next((item for item in self._pending if self.repository.load_change_set(item) and any(op.operation_id == change_id for op in self.repository.load_change_set(item).operations)), "")
        if not change_set_id:
            raise ValueError("待确认变更不存在")
        applied = ChangeSetService(self.manager, self.title, self.repository).approve(change_set_id)
        operation = next(op for op in applied.operations if op.operation_id == change_id)
        chapter_num = int(operation.payload["chapter_num"])
        version = self.manager.get_next_version(self.title, chapter_num) - 1
        node = self.manager._node_id(chapter_num, version)
        meta = self.manager.load_meta(self.title)
        return meta.chapter_nodes.get(node, {}).get("file", ""), version

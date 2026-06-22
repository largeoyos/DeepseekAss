"""Permission-aware application tools for an optional future Agent."""
from __future__ import annotations

import re
from dataclasses import dataclass, field


READ_ONLY = "read_only"
DRAFT_WRITE = "draft_write"
CONFIRM_WRITE = "confirm_write"


class AgentPermissionError(PermissionError):
    pass


@dataclass
class AgentChange:
    change_id: str
    operation: str
    target: str
    content: str
    status: str = "pending"


@dataclass
class AgentChangeSet:
    changes: list[AgentChange] = field(default_factory=list)


class ControlledAgentTools:
    """Exposes domain operations, never unrestricted filesystem access."""

    def __init__(self, novel_manager, title: str, permission: str = READ_ONLY) -> None:
        if permission not in {READ_ONLY, DRAFT_WRITE, CONFIRM_WRITE}:
            raise ValueError(f"未知 Agent 权限: {permission}")
        self.manager = novel_manager
        self.title = title
        self.permission = permission
        self.workspace = novel_manager.get_workspace(title)
        self.changes = AgentChangeSet()

    def read_chapter(self, chapter_num: int) -> str:
        return self.manager.read_active_chapter(self.title, chapter_num) or ""

    def search(self, query: str, limit: int = 20) -> list[dict]:
        query = str(query or "").strip()
        if not query:
            return []
        pattern = re.compile(re.escape(query), re.IGNORECASE)
        results: list[dict] = []
        for node in self.manager.get_active_path_nodes(self.title):
            content = self.manager.read_chapter_node(self.title, node["id"]) or ""
            match = pattern.search(content)
            if match:
                results.append({
                    "node_id": node["id"],
                    "chapter_num": node["chapter_num"],
                    "title": node["title"],
                    "snippet": content[max(0, match.start() - 60):match.end() + 120],
                })
            if len(results) >= limit:
                break
        return results

    def read_world_bible(self) -> dict:
        from core.world_bible import world_bible_to_dict
        return world_bible_to_dict(self.manager.load_world_bible(self.title))

    def context_report(self, chapter_num: int, chapter_title: str, plot: str = "") -> dict:
        report = self.manager.context_assembler().assemble_chapter(
            self.title, chapter_num, chapter_title, plot
        )
        return {
            "preview": report.preview(),
            "context": report.render(),
        }

    def write_draft(self, name: str, content: str) -> str:
        if self.permission == READ_ONLY:
            raise AgentPermissionError("当前 Agent 仅允许读取")
        safe = re.sub(r'[\\/:*?"<>|]+', "-", str(name or "draft")).strip() or "draft"
        path = f"{self.workspace.drafts_dir}/{safe}.txt"
        self.workspace.storage.write_text(path, content)
        return path

    def propose_chapter(
        self,
        chapter_num: int,
        chapter_title: str,
        content: str,
        parent_id: str | None = None,
    ) -> AgentChange:
        if self.permission != CONFIRM_WRITE:
            raise AgentPermissionError("当前 Agent 无权提交正式章节变更")
        change = AgentChange(
            change_id=f"change_{len(self.changes.changes) + 1}",
            operation="save_chapter",
            target=f"{chapter_num}:{chapter_title}:{parent_id or ''}",
            content=content,
        )
        self.changes.changes.append(change)
        return change

    def confirm(self, change_id: str) -> tuple[str, int]:
        change = next(
            (item for item in self.changes.changes if item.change_id == change_id),
            None,
        )
        if change is None or change.status != "pending":
            raise ValueError("待确认变更不存在")
        chapter_raw, title, parent_id = change.target.split(":", 2)
        chapter_num = int(chapter_raw)
        version = self.manager.get_next_version(self.title, chapter_num)
        result = self.manager.save_chapter_version(
            self.title,
            chapter_num,
            title,
            change.content,
            version=version,
            parent_id=parent_id or None,
        )
        self.manager.snapshot_service(self.title).create(
            f"Agent 确认写入第{chapter_num}章 v{result[1]}",
            source="chapter",
        )
        change.status = "applied"
        return result

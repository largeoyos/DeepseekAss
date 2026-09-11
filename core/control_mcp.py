"""MCP stdio adapter for :mod:`core.control_service`."""
from __future__ import annotations

import logging
import sys
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from core.control_service import ControlService
from core.control_guide import AI_CONTROL_GUIDE


class WorldPatchOperation(BaseModel):
    operation: Literal["create", "patch", "archive", "supersede", "merge"]
    entity_type: Literal["character", "location", "timeline", "plot_thread", "world_rule", "foreshadowing"]
    entity_id: str = Field(min_length=1, max_length=200)
    payload: dict
    source_ids: list[str] = Field(default_factory=list, max_length=100)
    supersedes: list[str] = Field(default_factory=list, max_length=100)
    scope: Literal["global", "branch", "chapter"]
    anchor_node_id: str = ""
    reason: str = Field(default="", max_length=500)


class ControlResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: int


class BooksResult(ControlResult):
    books: list[dict]
    total: int


class ProjectResult(ControlResult):
    project: dict


class ChapterTreeResult(ControlResult):
    book: dict
    trees: list[dict]
    nodes: list[dict]
    active_path: list[str]
    active_tree_id: str
    target: dict
    page: dict


class ChapterResult(ControlResult):
    book: dict
    node: dict
    content: str
    page: dict


class ChapterSearchResult(ControlResult):
    book: dict
    query: str
    scope: str
    results: list[dict]


class WorldBibleResult(ControlResult):
    book: dict
    categories: dict[str, int]
    overview: dict | None = None
    category: str | None = None
    items: list | None = None
    page: dict | None = None


class WorldSearchResult(ControlResult):
    book: dict
    query: str
    results: list[dict]


class WorldEntitiesResult(ControlResult):
    book: dict
    entities: list[dict]
    missing_ids: list[str]


class WorldAuditResult(ControlResult):
    book: dict
    warning_count: int
    warnings: list[dict]


class PendingChangesResult(ControlResult):
    book: dict
    changes: list[dict]
    total: int


class ChangeSetResult(ControlResult):
    book: dict
    change_set: dict


class ProposalResult(ControlResult):
    book: dict
    change_set_id: str
    operation_ids: list[str]
    requires_human_approval: bool
    applied: bool
    operation_count: int | None = None


class ControlSettingsResult(ControlResult):
    defaults: dict
    capabilities: dict


class WritingAgentResult(ControlResult):
    book: dict
    session_id: str
    run_id: str
    status: str
    terminal_reason: str
    backend: dict
    model: str
    target: dict
    change_set_ids: list[str]
    artifact_ids: list[str]
    requires_human_approval: bool
    final_text: str
    warning: str


def _model_dict(value: BaseModel) -> dict:
    return value.model_dump() if hasattr(value, "model_dump") else value.dict()


def _result(model_type, value: dict):
    return model_type(**value)


def create_mcp_server(service: ControlService):
    try:
        from mcp.server import MCPServer
    except ImportError:  # MCP Python SDK 1.x compatibility for existing environments.
        from mcp.server.fastmcp import FastMCP as MCPServer

    server = MCPServer(
        "DeepseekAss",
        instructions=(
            "读取 DeepseekAss 本地小说项目、完整章节树和结构化世界书。"
            "先调用 get_control_settings 了解当前默认值和权限。"
            "写工具只创建待人工审批的提案，不会直接修改正式内容。"
            "如需正式创作，优先调用 run_writing_agent 让内置写作总管读取上下文并产生待审批提案。"
        ),
    )

    @server.resource("deepseekass://ai-control-guide")
    def ai_control_guide() -> str:
        """返回给 AI 阅读的完整工作流与安全边界。"""
        return AI_CONTROL_GUIDE

    @server.tool()
    def get_control_settings() -> ControlSettingsResult:
        """读取当前用户设置的分页、搜索、写作 Agent 默认值和授权能力。"""
        return _result(ControlSettingsResult, service.invoke("get_control_settings"))

    @server.tool()
    def list_books() -> BooksResult:
        """列出当前授权用户的小说及稳定 book_id。"""
        return _result(BooksResult, service.invoke("list_books"))

    @server.tool()
    def get_project(book: str) -> ProjectResult:
        """读取项目设定、作者规划、摘要和活跃章节路径。"""
        return _result(ProjectResult, service.invoke("get_project", {"book": book}))

    @server.tool()
    def get_chapter_tree(
        book: str,
        tree_id: str = "",
        offset: int = 0,
        limit: int | None = None,
        include_summaries: bool | None = None,
    ) -> ChapterTreeResult:
        """分页读取完整章节森林；正文需再用 read_chapter 读取。"""
        return _result(ChapterTreeResult, service.invoke("get_chapter_tree", {
            "book": book, "tree_id": tree_id, "offset": offset, "limit": limit,
            "include_summaries": include_summaries,
        }))

    @server.tool()
    def read_chapter(book: str, node_id: str, start: int = 0, max_chars: int | None = None) -> ChapterResult:
        """按稳定 node_id 分页读取具体章节版本正文。"""
        return _result(ChapterResult, service.invoke("read_chapter", {
            "book": book, "node_id": node_id, "start": start, "max_chars": max_chars,
        }))

    @server.tool()
    def search_chapters(
        book: str,
        query: str,
        scope: Literal["active", "all"] | None = None,
        limit: int | None = None,
    ) -> ChapterSearchResult:
        """搜索活跃路径或全部章节分支，返回节点 ID 和命中片段。"""
        return _result(ChapterSearchResult, service.invoke("search_chapters", {
            "book": book, "query": query, "scope": scope or "", "limit": limit,
        }))

    @server.tool()
    def get_world_bible(book: str, category: str = "", offset: int = 0, limit: int | None = None) -> WorldBibleResult:
        """读取世界书概览，或按 category 分页读取实体。"""
        return _result(WorldBibleResult, service.invoke("get_world_bible", {
            "book": book, "category": category, "offset": offset, "limit": limit,
        }))

    @server.tool()
    def search_world_bible(book: str, query: str, entity_type: str = "", limit: int | None = None) -> WorldSearchResult:
        """按关键词和可选实体类型检索世界书。"""
        return _result(WorldSearchResult, service.invoke("search_world_bible", {
            "book": book, "query": query, "entity_type": entity_type, "limit": limit,
        }))

    @server.tool()
    def read_world_entities(book: str, entity_ids: list[str]) -> WorldEntitiesResult:
        """按稳定实体 ID 批量读取完整世界书实体。"""
        return _result(WorldEntitiesResult, service.invoke("read_world_entities", {
            "book": book, "entity_ids": entity_ids,
        }))

    @server.tool()
    def audit_world_bible(book: str) -> WorldAuditResult:
        """运行世界书一致性检查并返回结构化警告。"""
        return _result(WorldAuditResult, service.invoke("audit_world_bible", {"book": book}))

    @server.tool()
    def list_pending_changes(book: str) -> PendingChangesResult:
        """列出待人类审批的章节或世界书提案。"""
        return _result(PendingChangesResult, service.invoke("list_pending_changes", {"book": book}))

    @server.tool()
    def get_change_set(book: str, change_set_id: str) -> ChangeSetResult:
        """读取变更提案摘要；不会批准或应用该变更。"""
        return _result(ChangeSetResult, service.invoke("get_change_set", {
            "book": book, "change_set_id": change_set_id,
        }))

    if "propose" in service.grant.scopes:
        @server.tool()
        def propose_chapter_revision(
            book: str,
            base_node_id: str,
            title: str,
            content: str,
            reason: str = "",
        ) -> ProposalResult:
            """基于现有节点提出章节新版本；批准后仍不会自动激活。"""
            return _result(ProposalResult, service.invoke("propose_chapter_revision", {
                "book": book, "base_node_id": base_node_id, "title": title,
                "content": content, "reason": reason,
            }))

        @server.tool()
        def propose_world_bible_patch(
            book: str,
            operations: list[WorldPatchOperation],
            reason: str = "",
        ) -> ProposalResult:
            """提出严格校验的世界书字段级补丁，等待人类审批。"""
            return _result(ProposalResult, service.invoke(
                "propose_world_bible_patch",
                {"book": book, "operations": [_model_dict(item) for item in operations], "reason": reason},
            ))

    if "generate" in service.grant.scopes:
        @server.tool()
        def run_writing_agent(
            book: str,
            instruction: str,
            chapter_title: str = "",
            target_words: int | None = None,
            manual_references: list[str] | None = None,
        ) -> WritingAgentResult:
            """调用 DeepseekAss 内置写作总管 Agent 读取上下文并生成待人工审批的章节提案。"""
            return _result(WritingAgentResult, service.invoke("run_writing_agent", {
                "book": book,
                "instruction": instruction,
                "chapter_title": chapter_title,
                "target_words": target_words,
                "manual_references": manual_references or [],
            }))

    return server


def run_mcp_stdio(service: ControlService) -> None:
    logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    create_mcp_server(service).run()

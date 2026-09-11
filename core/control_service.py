"""Headless, scoped application service shared by the CLI and MCP adapters."""
from __future__ import annotations

import difflib
import json
import os
import re
import uuid
from dataclasses import asdict
from datetime import datetime

from core.agent.changes import ChangeSetError, ChangeSetService
from core.agent.repository import AgentRepository
from core.auth_manager import AuthManager
from core.control_auth import ControlGrant
from core.novel_manager import NovelManager
from core.settings_manager import SettingsManager
from core.world_bible import (
    CharacterEntry,
    LocationEntry,
    PlotThread,
    TimelineEntry,
    WorldRule,
    audit_world_bible_consistency,
    world_bible_to_dict,
)


class ControlError(RuntimeError):
    code = "control_error"


class ControlPermissionError(ControlError):
    code = "permission_denied"


class ControlNotFoundError(ControlError):
    code = "not_found"


class ControlValidationError(ControlError):
    code = "validation_error"


class ControlConflictError(ControlError):
    code = "conflict"


WORLD_COLLECTIONS = {
    "character": "characters",
    "location": "locations",
    "timeline": "timeline",
    "plot_thread": "active_plot_threads",
    "world_rule": "world_rules",
    "foreshadowing": "global_foreshadowing",
    "setting": "key_worldbuilding_passages",
    "dialogue": "global_key_dialogues",
    "fact": "facts",
}
MUTABLE_WORLD_TYPES = {"character", "location", "timeline", "plot_thread", "world_rule", "foreshadowing"}
WORLD_ENTITY_CLASSES = {
    "character": CharacterEntry,
    "location": LocationEntry,
    "timeline": TimelineEntry,
    "plot_thread": PlotThread,
    "world_rule": WorldRule,
}
WORLD_ACTIONS = {"create", "patch", "archive", "supersede", "merge"}
WORLD_SCOPES = {"global", "branch", "chapter"}


def _clip(value: object, limit: int = 240) -> str:
    text = str(value or "")
    return text if len(text) <= limit else text[:limit] + "…"


class ControlService:
    SCHEMA_VERSION = 1

    def __init__(self, grant: ControlGrant) -> None:
        self.grant = grant
        self.user_dir = AuthManager.get_user_dir(grant.username)
        self.manager = NovelManager(
            os.path.join(self.user_dir, "bookshelf"),
            crypto=AuthManager,
            enc_key=grant.enc_key,
        )
        self.settings_manager = SettingsManager(self.user_dir, AuthManager, grant.enc_key)
        self.settings = self.settings_manager.load()

    def _setting_int(self, key: str, fallback: int, *, minimum: int, maximum: int) -> int:
        try:
            value = int(self.settings.get(key, fallback))
        except (TypeError, ValueError):
            value = fallback
        return max(minimum, min(maximum, value))

    def _require(self, scope: str) -> None:
        if scope not in self.grant.scopes:
            raise ControlPermissionError(f"当前授权缺少 {scope} 权限")

    def _book(self, reference: str) -> tuple[str, object]:
        reference = str(reference or "").strip()
        if not reference:
            raise ControlValidationError("book 不能为空")
        title_match = None
        for title in self.manager.list_books():
            meta = self.manager.load_meta(title)
            if str(getattr(meta, "book_id", "")) == reference:
                return title, meta
            if title == reference:
                title_match = (title, meta)
        if title_match:
            return title_match
        raise ControlNotFoundError(f"书籍不存在: {reference}")

    @staticmethod
    def _book_identity(title: str, meta) -> dict:
        return {"book_id": str(getattr(meta, "book_id", "")), "title": title}

    @staticmethod
    def _node(node: dict, *, include_summary: bool = True) -> dict:
        hidden = {"file"}
        result = {key: value for key, value in dict(node or {}).items() if key not in hidden}
        if not include_summary:
            result.pop("summary", None)
        return result

    def invoke(self, tool_name: str, arguments: dict | None = None) -> dict:
        arguments = dict(arguments or {})
        handlers = {
            "list_books": self.list_books,
            "get_project": self.get_project,
            "get_chapter_tree": self.get_chapter_tree,
            "read_chapter": self.read_chapter,
            "search_chapters": self.search_chapters,
            "get_world_bible": self.get_world_bible,
            "search_world_bible": self.search_world_bible,
            "read_world_entities": self.read_world_entities,
            "audit_world_bible": self.audit_world_bible,
            "propose_chapter_revision": self.propose_chapter_revision,
            "propose_world_bible_patch": self.propose_world_bible_patch,
            "list_pending_changes": self.list_pending_changes,
            "get_change_set": self.get_change_set,
            "get_control_settings": self.get_control_settings,
            "run_writing_agent": self.run_writing_agent,
        }
        handler = handlers.get(tool_name)
        if handler is None:
            raise ControlValidationError(f"未知控制工具: {tool_name}")
        book = str(arguments.get("book") or "")
        try:
            result = handler(**arguments)
            self._audit(tool_name, book, True, result=result, argument_keys=sorted(arguments))
            return result
        except Exception as exc:
            self._audit(tool_name, book, False, error=str(exc), argument_keys=sorted(arguments))
            raise

    def get_control_settings(self) -> dict:
        self._require("read")
        defaults = {
            "tree_page_size": self._setting_int("control_tree_page_size", 500, minimum=1, maximum=500),
            "chapter_page_chars": self._setting_int("control_chapter_page_chars", 20000, minimum=1, maximum=100000),
            "search_scope": str(self.settings.get("control_search_scope") or "all"),
            "search_limit": self._setting_int("control_search_limit", 20, minimum=1, maximum=50),
            "world_page_size": self._setting_int("control_world_page_size", 100, minimum=1, maximum=500),
            "include_summaries": bool(self.settings.get("control_include_summaries", False)),
            "agent_target_words": self._setting_int("control_agent_target_words", 3000, minimum=500, maximum=50000),
        }
        return {
            "schema_version": self.SCHEMA_VERSION,
            "defaults": defaults,
            "capabilities": {
                "read": "read" in self.grant.scopes,
                "propose": "propose" in self.grant.scopes,
                "writing_agent": "generate" in self.grant.scopes and bool(self.settings.get("control_agent_enabled", False)),
                "approve": False,
            },
        }

    def _audit(
        self,
        tool_name: str,
        book: str,
        success: bool,
        *,
        result: dict | None = None,
        error: str = "",
        argument_keys: list[str] | None = None,
    ) -> None:
        audit_dir = os.path.join(self.user_dir, ".deepseekass", "control_audit")
        os.makedirs(audit_dir, exist_ok=True)
        change_set_id = ""
        if isinstance(result, dict):
            change_set_id = str(result.get("change_set_id") or "")
        event_id = f"audit_{datetime.now().strftime('%Y%m%dT%H%M%S%f')}_{uuid.uuid4().hex[:8]}"
        payload = {
            "schema_version": self.SCHEMA_VERSION,
            "event_id": event_id,
            "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
            "grant_id": self.grant.grant_id,
            "grant_name": self.grant.name,
            "tool": tool_name,
            "book": book,
            "argument_keys": list(argument_keys or []),
            "success": bool(success),
            "change_set_id": change_set_id,
            "error": _clip(error, 500),
        }
        AuthManager.encrypt_json(self.grant.enc_key, os.path.join(audit_dir, event_id + ".json.enc"), payload)

    def list_books(self) -> dict:
        self._require("read")
        books = []
        for title in self.manager.list_books():
            meta = self.manager.load_meta(title)
            books.append({
                **self._book_identity(title, meta),
                "total_chapters": int(getattr(meta, "total_chapters", 0) or 0),
                "updated_at": str(getattr(meta, "updated_at", "") or ""),
            })
        return {"schema_version": self.SCHEMA_VERSION, "books": books, "total": len(books)}

    def get_project(self, book: str) -> dict:
        self._require("read")
        title, meta = self._book(book)
        fields = (
            "author", "protagonist_bio", "background_story", "writing_demand", "author_plan",
            "genre", "style_tone", "xp_mode", "total_chapters", "created_at", "updated_at",
        )
        project = {key: getattr(meta, key, "") for key in fields}
        project.update(self._book_identity(title, meta))
        project["active_tree_id"] = str(getattr(meta, "active_tree_id", "") or "")
        project["active_path"] = [self._node(item) for item in self.manager.get_active_path_nodes(title)]
        project["plot_summary"] = self.manager.load_summary(title)
        return {"schema_version": self.SCHEMA_VERSION, "project": project}

    def get_chapter_tree(
        self,
        book: str,
        tree_id: str = "",
        offset: int = 0,
        limit: int | None = None,
        include_summaries: bool | None = None,
    ) -> dict:
        self._require("read")
        title, meta = self._book(book)
        offset = max(0, int(offset))
        limit = self._setting_int("control_tree_page_size", 500, minimum=1, maximum=500) if limit is None else max(1, min(500, int(limit)))
        if include_summaries is None:
            include_summaries = bool(self.settings.get("control_include_summaries", False))
        tree_meta = self.manager.ensure_chapter_tree(title)
        nodes = self.manager.list_chapter_tree_nodes(title)
        if tree_id:
            known = {str(item.get("tree_id")) for item in self.manager.list_chapter_trees(title)}
            if tree_id not in known:
                raise ControlNotFoundError(f"章节树不存在: {tree_id}")
            nodes = [item for item in nodes if str(item.get("tree_id") or "primary_tree") == tree_id]
        total = len(nodes)
        page = [self._node(item, include_summary=include_summaries) for item in nodes[offset:offset + limit]]
        end = offset + len(page)
        return {
            "schema_version": self.SCHEMA_VERSION,
            "book": self._book_identity(title, meta),
            "trees": self.manager.list_chapter_trees(title),
            "nodes": page,
            "active_path": list(tree_meta.active_path),
            "active_tree_id": str(tree_meta.active_tree_id),
            "target": self.manager.get_active_generation_target(title),
            "page": {"offset": offset, "limit": limit, "returned": len(page), "total": total, "next_offset": end if end < total else None},
        }

    def read_chapter(self, book: str, node_id: str, start: int = 0, max_chars: int | None = None) -> dict:
        self._require("read")
        title, meta = self._book(book)
        node = self.manager.ensure_chapter_tree(title).chapter_nodes.get(str(node_id))
        if not node or node.get("virtual"):
            raise ControlNotFoundError(f"章节节点不存在: {node_id}")
        content = self.manager.read_chapter_node(title, str(node_id))
        if content is None:
            raise ControlNotFoundError(f"章节正文不存在: {node_id}")
        start = max(0, int(start))
        max_chars = self._setting_int("control_chapter_page_chars", 20000, minimum=1, maximum=100000) if max_chars is None else max(1, min(100000, int(max_chars)))
        chunk = content[start:start + max_chars]
        end = start + len(chunk)
        return {
            "schema_version": self.SCHEMA_VERSION,
            "book": self._book_identity(title, meta),
            "node": self._node(node),
            "content": chunk,
            "page": {"start": start, "end": end, "total_chars": len(content), "next_start": end if end < len(content) else None},
        }

    def search_chapters(self, book: str, query: str, scope: str = "", limit: int | None = None) -> dict:
        self._require("read")
        title, meta = self._book(book)
        query = str(query or "").strip()
        if not query:
            raise ControlValidationError("query 不能为空")
        scope = str(scope or self.settings.get("control_search_scope") or "all")
        if scope not in {"active", "all"}:
            raise ControlValidationError("scope 必须是 active 或 all")
        limit = self._setting_int("control_search_limit", 20, minimum=1, maximum=50) if limit is None else max(1, min(50, int(limit)))
        nodes = self.manager.get_active_path_nodes(title) if scope == "active" else self.manager.list_chapter_tree_nodes(title)
        pattern = re.compile(re.escape(query), re.IGNORECASE)
        results = []
        for node in nodes:
            if node.get("virtual"):
                continue
            content = self.manager.read_chapter_node(title, str(node.get("id") or "")) or ""
            haystack = f"{node.get('title', '')}\n{node.get('summary', '')}\n{content}"
            match = pattern.search(haystack)
            if not match:
                continue
            results.append({
                "node_id": node.get("id"),
                "chapter_num": node.get("chapter_num"),
                "version": node.get("version"),
                "tree_id": node.get("tree_id"),
                "title": node.get("title", ""),
                "snippet": haystack[max(0, match.start() - 100):match.end() + 220],
            })
            if len(results) >= limit:
                break
        return {"schema_version": self.SCHEMA_VERSION, "book": self._book_identity(title, meta), "query": query, "scope": scope, "results": results}

    def _world(self, title: str) -> dict:
        return world_bible_to_dict(self.manager.load_world_bible(title))

    def get_world_bible(self, book: str, category: str = "", offset: int = 0, limit: int | None = None) -> dict:
        self._require("read")
        title, meta = self._book(book)
        world = self._world(title)
        counts = {kind: len(world.get(collection) or []) for kind, collection in WORLD_COLLECTIONS.items()}
        base = {"schema_version": self.SCHEMA_VERSION, "book": self._book_identity(title, meta), "categories": counts}
        if not category:
            base["overview"] = {
                "world_schema_version": world.get("schema_version"),
                "story_clock": world.get("story_clock") or {},
                "last_updated_chapter": world.get("last_updated_chapter", 0),
                "consistency_warning_count": len(world.get("consistency_warnings") or []),
            }
            return base
        collection = WORLD_COLLECTIONS.get(category)
        if collection is None:
            raise ControlValidationError(f"未知世界书分类: {category}")
        items = list(world.get(collection) or [])
        offset = max(0, int(offset))
        limit = self._setting_int("control_world_page_size", 100, minimum=1, maximum=500) if limit is None else max(1, min(500, int(limit)))
        page = items[offset:offset + limit]
        end = offset + len(page)
        base.update({
            "category": category,
            "items": page,
            "page": {"offset": offset, "limit": limit, "returned": len(page), "total": len(items), "next_offset": end if end < len(items) else None},
        })
        return base

    def search_world_bible(self, book: str, query: str, entity_type: str = "", limit: int | None = None) -> dict:
        self._require("read")
        title, meta = self._book(book)
        query = str(query or "").strip().casefold()
        if not query:
            raise ControlValidationError("query 不能为空")
        if entity_type and entity_type not in WORLD_COLLECTIONS:
            raise ControlValidationError(f"未知世界书实体类型: {entity_type}")
        limit = self._setting_int("control_search_limit", 20, minimum=1, maximum=50) if limit is None else max(1, min(50, int(limit)))
        world = self._world(title)
        results = []
        for kind, collection in WORLD_COLLECTIONS.items():
            if entity_type and kind != entity_type:
                continue
            for item in world.get(collection) or []:
                payload = item if isinstance(item, dict) else {"value": item}
                text = json.dumps(payload, ensure_ascii=False, default=str)
                if query not in text.casefold():
                    continue
                entity_id = str(payload.get("id") or payload.get("name") or payload.get("topic") or payload.get("hint") or "")
                results.append({"id": entity_id, "type": kind, "name": payload.get("name") or payload.get("topic") or payload.get("hint") or entity_id, "snippet": _clip(text, 360)})
                if len(results) >= limit:
                    return {"schema_version": self.SCHEMA_VERSION, "book": self._book_identity(title, meta), "query": query, "results": results}
        return {"schema_version": self.SCHEMA_VERSION, "book": self._book_identity(title, meta), "query": query, "results": results}

    def read_world_entities(self, book: str, entity_ids: list[str]) -> dict:
        self._require("read")
        title, meta = self._book(book)
        wanted = {str(item) for item in entity_ids if str(item)}
        if not wanted or len(wanted) > 100:
            raise ControlValidationError("entity_ids 必须包含 1 到 100 个实体 ID")
        world = self._world(title)
        entities = []
        for kind, collection in WORLD_COLLECTIONS.items():
            for item in world.get(collection) or []:
                if not isinstance(item, dict):
                    continue
                entity_id = str(item.get("id") or "")
                if entity_id in wanted:
                    entities.append({"id": entity_id, "type": kind, "data": item})
        found = {item["id"] for item in entities}
        return {"schema_version": self.SCHEMA_VERSION, "book": self._book_identity(title, meta), "entities": entities, "missing_ids": sorted(wanted - found)}

    def audit_world_bible(self, book: str) -> dict:
        self._require("read")
        title, meta = self._book(book)
        warnings = audit_world_bible_consistency(self.manager.load_world_bible(title))
        return {"schema_version": self.SCHEMA_VERSION, "book": self._book_identity(title, meta), "warning_count": len(warnings), "warnings": warnings}

    def run_writing_agent(
        self,
        book: str,
        instruction: str,
        chapter_title: str = "",
        target_words: int | None = None,
        manual_references: list[str] | None = None,
    ) -> dict:
        """Run the built-in writing orchestrator and keep formal writes behind approval."""
        self._require("generate")
        self._require("propose")
        if not bool(self.settings.get("control_agent_enabled", False)):
            raise ControlPermissionError("设置中未允许外部 AI 调用内置写作 Agent")
        book_title, meta = self._book(book)
        instruction = str(instruction or "").strip()
        if not instruction:
            raise ControlValidationError("instruction 不能为空")
        if len(instruction) > 30000:
            raise ControlValidationError("instruction 超过 30000 字符限制")
        references = [str(item) for item in (manual_references or []) if str(item).strip()]
        if len(references) > 100:
            raise ControlValidationError("manual_references 最多 100 项")
        words = (
            self._setting_int("control_agent_target_words", 3000, minimum=500, maximum=50000)
            if target_words is None
            else max(500, min(50000, int(target_words)))
        )

        from core.agent.backends import build_agent_backend
        from core.agent.domain_tools import build_domain_tool_registry
        from core.agent.repository import AgentRepository
        from core.agent.types import AgentRunRequest
        from core.conversation_manager import ConversationManager
        from core.model_config import ModelConfig
        from core.model_gateway import ModelGateway
        from core.model_types import TaskStage

        raw_config = AuthManager.decrypt_json(self.grant.enc_key, os.path.join(self.user_dir, "config.enc")) or {}
        model_config = ModelConfig.from_dict(raw_config, self.settings)

        def load_book_routes(title: str) -> dict:
            data = self.manager.get_workspace(title).storage.read_json(".deepseekass/model_routes.json", default={}) or {}
            return dict(data.get("routes") or data)

        route = model_config.effective_route(TaskStage.AGENT, load_book_routes(book_title))
        profile = model_config.models.get(route.primary_model_id)
        if profile is None:
            raise ControlValidationError("Agent 阶段没有可用模型路由")
        gateway = ModelGateway(model_config, book_route_loader=load_book_routes)
        client = gateway.compat_client("agent_runtime", stage=TaskStage.AGENT, book_title=book_title)
        conversations = ConversationManager(
            os.path.join(self.user_dir, "conversations"), crypto=AuthManager, enc_key=self.grant.enc_key
        )
        backend, backend_status = build_agent_backend(
            settings=self.settings,
            novel_manager=self.manager,
            client=client,
            tool_registry=build_domain_tool_registry(self.manager, conversations),
            skills_enabled=bool(self.settings.get("agent_skills_enabled", True)),
        )
        session = backend.create_session(book_title, "writing_orchestrator", "外部 AI 写作任务")
        target = self.manager.get_active_generation_target(book_title)
        resolved_title = str(chapter_title or f"第{int(target.get('chapter_num') or 1)}章")
        prefix = str(self.settings.get("control_agent_instruction_prefix") or "").strip()
        message = "\n\n".join(filter(None, [
            prefix,
            instruction,
            (
                f"请使用内置工具读取项目、章节树和世界书，创作「{resolved_title}」，目标约 {words} 字。"
                "完成后必须调用 chapter.propose 提交待人工审批的章节变更；"
                "不要越过审批，不要只返回写作建议。"
            ),
        ]))
        run = backend.run(AgentRunRequest(
            str(meta.book_id), session.session_id, "writing_orchestrator", message,
            references, model=profile.model, book_title=book_title,
        ))
        if run.status == "failed":
            raise ControlError(run.error or "写作 Agent 运行失败")
        repository = AgentRepository(self.manager.get_workspace(book_title))
        for change_id in run.change_set_ids:
            change = repository.load_change_set(change_id)
            if change is None:
                continue
            change.validation_result.update({
                "origin": "external_control",
                "via": "writing_agent",
                "grant_id": self.grant.grant_id,
                "requires_human_approval": True,
            })
            repository.save_change_set(change)
        assistants = [item for item in run.messages if item.get("role") == "assistant" and item.get("content")]
        final_text = str(assistants[-1].get("content") or "") if assistants else ""
        return {
            "schema_version": self.SCHEMA_VERSION,
            "book": self._book_identity(book_title, meta),
            "session_id": session.session_id,
            "run_id": run.run_id,
            "status": run.status,
            "terminal_reason": run.terminal_reason,
            "backend": asdict(backend_status),
            "model": profile.model,
            "target": {"chapter_title": resolved_title, "target_words": words},
            "change_set_ids": list(run.change_set_ids),
            "artifact_ids": list(run.artifact_ids),
            "requires_human_approval": bool(run.change_set_ids),
            "final_text": _clip(final_text, 5000),
            "warning": "" if run.change_set_ids else "Agent 未创建变更提案，请检查 final_text 并重试。",
        }

    def propose_chapter_revision(self, book: str, base_node_id: str, title: str, content: str, reason: str = "") -> dict:
        self._require("propose")
        book_title, meta = self._book(book)
        content = str(content or "")
        if not content.strip():
            raise ControlValidationError("章节正文不能为空")
        if len(content) > 2_000_000:
            raise ControlValidationError("章节正文超过 200 万字符限制")
        repository = AgentRepository(self.manager.get_workspace(book_title))
        try:
            change = ChangeSetService(self.manager, book_title, repository).propose_chapter_revision(
                f"external_{self.grant.grant_id}_{uuid.uuid4().hex[:12]}",
                str(meta.book_id),
                str(base_node_id),
                str(title or ""),
                content,
                str(reason or "外部 AI 章节修订提案"),
            )
        except ChangeSetError as exc:
            raise ControlValidationError(str(exc)) from exc
        change.validation_result.update({"origin": "external_control", "grant_id": self.grant.grant_id, "requires_human_approval": True})
        repository.save_change_set(change)
        return {"schema_version": self.SCHEMA_VERSION, "book": self._book_identity(book_title, meta), "change_set_id": change.change_set_id, "operation_ids": [item.operation_id for item in change.operations], "requires_human_approval": True, "applied": False}

    def propose_world_bible_patch(self, book: str, operations: list[dict], reason: str = "") -> dict:
        self._require("propose")
        book_title, meta = self._book(book)
        if not isinstance(operations, list) or not operations or len(operations) > 100:
            raise ControlValidationError("operations 必须包含 1 到 100 项")
        active_ids = {str(item.get("id") or "") for item in self.manager.get_active_path_nodes(book_title)}
        world = self._world(book_title)
        normalized = []
        for index, raw in enumerate(operations):
            if not isinstance(raw, dict):
                raise ControlValidationError(f"第 {index + 1} 项变更必须是对象")
            action = str(raw.get("operation") or "").removeprefix("entity.")
            entity_type = str(raw.get("entity_type") or "")
            entity_id = str(raw.get("entity_id") or "")
            scope = str(raw.get("scope") or "")
            anchor = str(raw.get("anchor_node_id") or "")
            payload = raw.get("payload")
            if action not in WORLD_ACTIONS:
                raise ControlValidationError(f"第 {index + 1} 项 operation 无效")
            if entity_type not in MUTABLE_WORLD_TYPES:
                raise ControlValidationError(f"第 {index + 1} 项 entity_type 无效")
            if not entity_id:
                raise ControlValidationError(f"第 {index + 1} 项缺少 entity_id")
            if not isinstance(payload, dict):
                raise ControlValidationError(f"第 {index + 1} 项 payload 必须是对象")
            if "id" in payload and str(payload.get("id") or "") != entity_id:
                raise ControlValidationError(f"第 {index + 1} 项 payload.id 不得改变实体 ID")
            if scope not in WORLD_SCOPES:
                raise ControlValidationError(f"第 {index + 1} 项 scope 必须是 global、branch 或 chapter")
            if scope != "global" and anchor not in active_ids:
                raise ControlValidationError(f"第 {index + 1} 项作用域锚点不在活跃路径")
            source_ids = raw.get("source_ids") or []
            if action == "merge" and (not isinstance(source_ids, list) or not source_ids):
                raise ControlValidationError(f"第 {index + 1} 项 merge 缺少 source_ids")
            if action == "merge" and entity_type not in {"character", "location"}:
                raise ControlValidationError(f"第 {index + 1} 项 merge 仅支持角色或地点")
            if action == "merge" and entity_id in {str(item) for item in source_ids}:
                raise ControlValidationError(f"第 {index + 1} 项 merge 的 source_ids 不能包含目标实体")
            if action == "supersede" and entity_type != "world_rule":
                raise ControlValidationError(f"第 {index + 1} 项 supersede 仅支持世界规则")
            supersedes = raw.get("supersedes") or []
            if action == "supersede" and (not isinstance(supersedes, list) or not supersedes):
                raise ControlValidationError(f"第 {index + 1} 项 supersede 缺少 supersedes")
            collection_name = WORLD_COLLECTIONS[entity_type]
            collection = world.get(collection_name) or []
            target = next((item for item in collection if isinstance(item, dict) and str(item.get("id") or "") == entity_id), None)
            if action == "create" and target is not None:
                raise ControlValidationError(f"第 {index + 1} 项实体已存在: {entity_id}")
            if action != "create" and target is None:
                raise ControlValidationError(f"第 {index + 1} 项实体不存在: {entity_id}")
            entity_class = WORLD_ENTITY_CLASSES.get(entity_type)
            if entity_class is not None:
                unknown = sorted(set(payload) - set(entity_class.__dataclass_fields__))
                if unknown:
                    raise ControlValidationError(f"第 {index + 1} 项包含未知字段: {', '.join(unknown)}")
            if action == "merge":
                existing_ids = {str(item.get("id") or "") for item in collection if isinstance(item, dict)}
                missing_sources = sorted({str(item) for item in source_ids} - existing_ids)
                if missing_sources:
                    raise ControlValidationError(f"第 {index + 1} 项合并来源不存在: {', '.join(missing_sources)}")
            normalized.append({
                "operation": f"entity.{action}",
                "entity_type": entity_type,
                "entity_id": entity_id,
                "payload": payload,
                "source_ids": [str(item) for item in source_ids],
                "supersedes": supersedes,
                "scope": scope,
                "anchor_node_id": "" if scope == "global" else anchor,
                "reason": str(raw.get("reason") or reason or "外部 AI 世界书提案")[:500],
                "source": "external_control",
            })
            try:
                ChangeSetService._apply_world_patch(world, [normalized[-1]])
            except ChangeSetError as exc:
                raise ControlValidationError(f"第 {index + 1} 项无法应用: {exc}") from exc
        repository = AgentRepository(self.manager.get_workspace(book_title))
        change = ChangeSetService(self.manager, book_title, repository).propose_world_patch(
            f"external_{self.grant.grant_id}_{uuid.uuid4().hex[:12]}",
            str(meta.book_id),
            normalized,
            str(reason or "外部 AI 世界书字段变更提案"),
        )
        change.validation_result.update({"origin": "external_control", "grant_id": self.grant.grant_id, "requires_human_approval": True})
        repository.save_change_set(change)
        return {"schema_version": self.SCHEMA_VERSION, "book": self._book_identity(book_title, meta), "change_set_id": change.change_set_id, "operation_ids": [item.operation_id for item in change.operations], "operation_count": len(normalized), "requires_human_approval": True, "applied": False}

    def list_pending_changes(self, book: str) -> dict:
        self._require("read")
        title, meta = self._book(book)
        repository = AgentRepository(self.manager.get_workspace(title))
        changes = [self._change_summary(item) for item in repository.list_pending_change_sets()]
        return {"schema_version": self.SCHEMA_VERSION, "book": self._book_identity(title, meta), "changes": changes, "total": len(changes)}

    def get_change_set(self, book: str, change_set_id: str) -> dict:
        self._require("read")
        title, meta = self._book(book)
        change = AgentRepository(self.manager.get_workspace(title)).load_change_set(str(change_set_id))
        if change is None:
            raise ControlNotFoundError(f"变更不存在: {change_set_id}")
        summary = self._change_summary(change, detailed=True)
        summary["review"] = self._change_review(change, title)
        return {"schema_version": self.SCHEMA_VERSION, "book": self._book_identity(title, meta), "change_set": summary}

    def _change_review(self, change, title: str) -> dict:
        review = []
        for operation in change.operations:
            item = {"operation_id": operation.operation_id, "operation": operation.operation, "target_id": operation.target_id}
            if operation.operation in {"chapter.save_version", "chapter.create_revision"}:
                if operation.operation == "chapter.create_revision":
                    before = self.manager.read_chapter_node(title, operation.target_id) or ""
                else:
                    before = self.manager.read_active_chapter(title, int(operation.target_id)) or ""
                after = str(operation.payload.get("content") or "")
                item.update({
                    "kind": "chapter",
                    "before_chars": len(before),
                    "after_chars": len(after),
                    "will_activate": bool(operation.payload.get("activate", operation.operation == "chapter.save_version")),
                    "diff": "".join(difflib.unified_diff(before.splitlines(True), after.splitlines(True), fromfile="当前章节", tofile="AI 提议"))[:30000],
                })
            elif operation.operation == "world_bible.patch":
                item.update({"kind": "world_bible", "patches": list(operation.payload.get("operations") or [])})
            else:
                item.update({"kind": "other"})
            review.append(item)
        return {"origin": str((change.validation_result or {}).get("origin") or "internal_agent"), "operations": review}

    @staticmethod
    def _change_summary(change, detailed: bool = False) -> dict:
        result = {
            "change_set_id": change.change_set_id,
            "run_id": change.run_id,
            "book_id": change.book_id,
            "status": change.status,
            "reason": change.reason,
            "created_at": change.created_at,
            "validation_result": dict(change.validation_result or {}),
            "operation_count": len(change.operations),
        }
        operations = []
        for operation in change.operations:
            payload = dict(operation.payload or {})
            content = payload.pop("content", None)
            if content is not None:
                payload["content_chars"] = len(str(content))
                payload["content_preview"] = _clip(content, 500 if detailed else 160)
            operations.append({
                "operation_id": operation.operation_id,
                "operation": operation.operation,
                "target_type": operation.target_type,
                "target_id": operation.target_id,
                "status": operation.status,
                "payload": payload if detailed else {key: payload.get(key) for key in ("base_node_id", "chapter_num", "chapter_title", "content_chars") if key in payload},
            })
        result["operations"] = operations
        return result


def approve_change_with_password(username: str, password: str, book: str, change_set_id: str, operation_ids: list[str] | None = None) -> dict:
    ok, enc_key = AuthManager.authenticate(username, password)
    if not ok or enc_key is None:
        raise ControlPermissionError("用户名或密码错误")
    helper = ControlService(ControlGrant("human", username, "human", ("read", "propose"), "", "", enc_key))
    manager = helper.manager
    title, meta = helper._book(book)
    repository = AgentRepository(manager.get_workspace(title))
    try:
        change = ChangeSetService(manager, title, repository).approve(change_set_id, operation_ids)
    except ChangeSetError as exc:
        if "已变化" in str(exc) or "过期" in str(exc):
            raise ControlConflictError(str(exc)) from exc
        raise ControlValidationError(str(exc)) from exc
    return {"schema_version": 1, "book": {"book_id": str(meta.book_id), "title": title}, "change_set": asdict(change)}


def reject_change_with_password(username: str, password: str, book: str, change_set_id: str) -> dict:
    ok, enc_key = AuthManager.authenticate(username, password)
    if not ok or enc_key is None:
        raise ControlPermissionError("用户名或密码错误")
    helper = ControlService(ControlGrant("human", username, "human", ("read", "propose"), "", "", enc_key))
    manager = helper.manager
    title, meta = helper._book(book)
    repository = AgentRepository(manager.get_workspace(title))
    try:
        change = ChangeSetService(manager, title, repository).reject(change_set_id)
    except ChangeSetError as exc:
        raise ControlValidationError(str(exc)) from exc
    return {"schema_version": 1, "book": {"book_id": str(meta.book_id), "title": title}, "change_set": asdict(change)}

from __future__ import annotations

import re

from core.agent.changes import ChangeSetService
from core.agent.tools import ToolRegistry, ToolSpec


def _object_schema(properties: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": properties, "required": required or [], "additionalProperties": False}


def build_domain_tool_registry(novel_manager, conversation_manager=None) -> ToolRegistry:
    registry = ToolRegistry()

    def chapter_read(ctx, args):
        content = novel_manager.read_active_chapter(ctx.book_title, int(args["chapter_num"])) or ""
        return {"chapter_num": int(args["chapter_num"]), "content": content}

    def chapter_search(ctx, args):
        query = str(args["query"]).strip()
        limit = max(1, min(50, int(args.get("limit", 20))))
        if not query:
            return {"results": []}
        pattern, results = re.compile(re.escape(query), re.IGNORECASE), []
        for node in novel_manager.get_active_path_nodes(ctx.book_title):
            content = novel_manager.read_chapter_node(ctx.book_title, node["id"]) or ""
            match = pattern.search(content)
            if match:
                results.append({"node_id": node["id"], "chapter_num": node["chapter_num"], "title": node["title"], "snippet": content[max(0, match.start() - 80):match.end() + 160]})
            if len(results) >= limit:
                break
        return {"results": results}

    def world_read(ctx, _args):
        from core.world_bible import world_bible_to_dict
        return world_bible_to_dict(novel_manager.load_world_bible(ctx.book_title))

    def context_report(ctx, args):
        report = novel_manager.context_assembler().assemble_chapter(ctx.book_title, int(args["chapter_num"]), args.get("chapter_title", ""), args.get("plot", ""), manual_entity_ids=args.get("manual_entity_ids", []))
        return {"preview": report.preview(), "context": report.render()}

    def project_summary(ctx, _args):
        meta = novel_manager.load_meta(ctx.book_title)
        return {"title": meta.title, "total_chapters": meta.total_chapters, "author_plan": meta.author_plan, "summary": novel_manager.load_summary(ctx.book_title), "active_path": novel_manager.get_active_path_nodes(ctx.book_title)}

    def project_integrity(ctx, _args):
        meta = novel_manager.load_meta(ctx.book_title)
        nodes = novel_manager.list_chapter_tree_nodes(ctx.book_title)
        active = {item["id"] for item in novel_manager.get_active_path_nodes(ctx.book_title)}
        missing = [item["id"] for item in nodes if not novel_manager.read_chapter_node(ctx.book_title, item["id"])]
        orphaned = [item["id"] for item in nodes if item["id"] not in active]
        return {"book_id": meta.book_id, "node_count": len(nodes), "missing_content_nodes": missing, "non_active_nodes": orphaned, "workspace_error": novel_manager.workspace_error(ctx.book_title), "world_bible_error": novel_manager.world_bible_load_error(ctx.book_title)}

    def write_draft(ctx, args):
        draft_id = ctx.repository.save_draft(ctx.run_id, args["name"], args["content"])
        return {"draft_id": draft_id, "name": args["name"]}

    def propose_chapter(ctx, args):
        service = ChangeSetService(novel_manager, ctx.book_title, ctx.repository)
        change = service.propose_chapter(ctx.run_id, ctx.book_id, int(args["chapter_num"]), args["chapter_title"], args["content"], args.get("parent_id", ""), args.get("reason", ""))
        return {"change_set_id": change.change_set_id, "requires_approval": True, "operation_count": len(change.operations)}

    def propose_world(ctx, args):
        service = ChangeSetService(novel_manager, ctx.book_title, ctx.repository)
        change = service.propose_world_bible(ctx.run_id, ctx.book_id, args["world_bible"], args.get("reason", ""))
        return {"change_set_id": change.change_set_id, "requires_approval": True, "operation_count": len(change.operations)}

    def todo(ctx, args):
        return {"todo": args["items"]}

    def conversation_read(_ctx, args):
        if conversation_manager is None:
            return {"available": False, "message": "未配置角色扮演会话服务"}
        record = conversation_manager.load_conversation(args["conversation_id"])
        return record or {"available": False}

    registry.register(ToolSpec("chapter.read", "读取指定活跃章节。", _object_schema({"chapter_num": {"type": "integer"}}, ["chapter_num"]), chapter_read))
    registry.register(ToolSpec("chapter.search", "在活跃章节路径中搜索文本。", _object_schema({"query": {"type": "string"}, "limit": {"type": "integer"}}, ["query"]), chapter_search))
    registry.register(ToolSpec("world_bible.read", "读取结构化世界书。", _object_schema({}), world_read, max_result_chars=16000))
    registry.register(ToolSpec("agent.context_report", "构建并解释渐进式章节上下文。", _object_schema({"chapter_num": {"type": "integer"}, "chapter_title": {"type": "string"}, "plot": {"type": "string"}, "manual_entity_ids": {"type": "array", "items": {"type": "string"}}}, ["chapter_num"]), context_report, max_result_chars=18000))
    registry.register(ToolSpec("project.summary", "读取书籍元数据、摘要和活跃路径。", _object_schema({}), project_summary))
    registry.register(ToolSpec("project.integrity", "检查项目结构和数据完整性。", _object_schema({}), project_integrity, allowed_agents=["project_maintainer"]))
    registry.register(ToolSpec("chapter.write_draft", "将内容写入加密 Agent 草稿区。", _object_schema({"name": {"type": "string"}, "content": {"type": "string"}}, ["name", "content"]), write_draft, required_permission="draft_write", read_only=False))
    registry.register(ToolSpec("chapter.propose", "提出正式章节版本变更，等待用户审批。", _object_schema({"chapter_num": {"type": "integer"}, "chapter_title": {"type": "string"}, "content": {"type": "string"}, "parent_id": {"type": "string"}, "reason": {"type": "string"}}, ["chapter_num", "chapter_title", "content"]), propose_chapter, required_permission="confirmed_write", read_only=False, produces_change_set=True, allowed_agents=["writing_orchestrator"]))
    registry.register(ToolSpec("world_bible.propose", "提出完整世界书替换变更，等待用户审批。", _object_schema({"world_bible": {"type": "object"}, "reason": {"type": "string"}}, ["world_bible"]), propose_world, required_permission="confirmed_write", read_only=False, produces_change_set=True, allowed_agents=["continuity_editor"]))
    registry.register(ToolSpec("agent.todo", "记录当前任务的结构化待办。", _object_schema({"items": {"type": "array", "items": {"type": "object"}}}, ["items"]), todo))
    registry.register(ToolSpec("conversation.read", "读取现有角色扮演会话。", _object_schema({"conversation_id": {"type": "string"}}, ["conversation_id"]), conversation_read, allowed_agents=["roleplay_director"]))
    return registry

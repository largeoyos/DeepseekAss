from __future__ import annotations

import re
from dataclasses import asdict

from core.agent.changes import ChangeSetService
from core.agent.tools import ToolRegistry, ToolSpec
from core.agent.web_search import WebSearchClient, WebSearchConfig


def _object_schema(properties: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": properties, "required": required or [], "additionalProperties": False}


def _clip(text: str, limit: int = 500) -> str:
    text = str(text or "")
    return text if len(text) <= limit else text[:limit] + "…"


def _entity_collections(world: dict) -> list[tuple[str, str, list]]:
    return [
        ("character", "characters", world.get("characters") or []),
        ("location", "locations", world.get("locations") or []),
        ("rule", "rules", world.get("rules") or []),
        ("timeline", "timeline", world.get("timeline") or []),
        ("plot_thread", "active_plot_threads", world.get("active_plot_threads") or []),
        ("world_rule", "world_rules", world.get("world_rules") or []),
        ("foreshadowing", "global_foreshadowing", world.get("global_foreshadowing") or []),
    ]


def _entity_id(kind: str, item) -> str:
    if isinstance(item, dict):
        return str(item.get("id") or item.get("name") or item.get("topic") or item.get("hint") or item.get("event") or "")
    return str(item)


def _entity_text(item) -> str:
    if isinstance(item, dict):
        return " ".join(str(v) for v in item.values() if isinstance(v, (str, int, float)))
    return str(item)


def build_domain_tool_registry(novel_manager, conversation_manager=None, web_search_config: WebSearchConfig | dict | None = None) -> ToolRegistry:
    registry = ToolRegistry()
    if isinstance(web_search_config, dict):
        web_search_config = WebSearchConfig.from_settings(web_search_config)

    def chapter_read(ctx, args):
        content = novel_manager.read_active_chapter(ctx.book_title, int(args["chapter_num"])) or ""
        return {"chapter_num": int(args["chapter_num"]), "content": content}

    def chapter_read_node(ctx, args):
        node_id = str(args["node_id"])
        content = novel_manager.read_chapter_node(ctx.book_title, node_id) or ""
        node = next((n for n in novel_manager.list_chapter_tree_nodes(ctx.book_title) if n.get("id") == node_id), {})
        return {"node_id": node_id, "chapter_num": node.get("chapter_num"), "title": node.get("title", ""), "version": node.get("version"), "content": content}

    def chapter_read_range(ctx, args):
        center = int(args.get("center_chapter", 0) or 0)
        before = max(0, min(10, int(args.get("before", 1) or 1)))
        after = max(0, min(10, int(args.get("after", 1) or 1)))
        nodes = novel_manager.get_active_path_nodes(ctx.book_title)
        selected = []
        for node in nodes:
            num = int(node.get("chapter_num", 0) or 0)
            if center - before <= num <= center + after:
                selected.append({"node_id": node.get("id"), "chapter_num": num, "title": node.get("title", ""), "summary": node.get("summary", ""), "content": novel_manager.read_chapter_node(ctx.book_title, node.get("id")) or ""})
        return {"chapters": selected}

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

    def chapter_summary_search(ctx, args):
        query = str(args.get("query", "")).strip()
        limit = max(1, min(50, int(args.get("limit", 20) or 20)))
        pattern = re.compile(re.escape(query), re.IGNORECASE) if query else None
        results = []
        for node in novel_manager.get_active_path_nodes(ctx.book_title):
            summary = str(node.get("summary", "") or "")
            hay = f"{node.get('title', '')}\n{summary}"
            if not pattern or pattern.search(hay):
                results.append({"node_id": node.get("id"), "chapter_num": node.get("chapter_num"), "title": node.get("title", ""), "summary": summary})
            if len(results) >= limit:
                break
        return {"results": results}

    def world_read(ctx, _args):
        from core.world_bible import world_bible_to_dict
        return world_bible_to_dict(novel_manager.load_world_bible(ctx.book_title))

    def world_search(ctx, args):
        from core.world_bible import world_bible_to_dict
        world = world_bible_to_dict(novel_manager.load_world_bible(ctx.book_title))
        query = str(args.get("query", "")).strip().lower()
        entity_type = str(args.get("entity_type", "") or "").strip()
        limit = max(1, min(50, int(args.get("limit", 20) or 20)))
        results = []
        for kind, _collection, items in _entity_collections(world):
            if entity_type and entity_type != kind:
                continue
            for item in items:
                text = _entity_text(item).lower()
                if query and query not in text:
                    continue
                item_id = _entity_id(kind, item)
                name = item.get("name") if isinstance(item, dict) else _clip(str(item), 80)
                if isinstance(item, dict):
                    name = name or item.get("topic") or item.get("hint") or item.get("event") or item_id
                results.append({"id": item_id, "type": kind, "name": name, "snippet": _clip(_entity_text(item), 240)})
                if len(results) >= limit:
                    return {"results": results}
        return {"results": results}

    def world_read_entities(ctx, args):
        from core.world_bible import world_bible_to_dict
        world = world_bible_to_dict(novel_manager.load_world_bible(ctx.book_title))
        ids = {str(item) for item in args.get("entity_ids", [])}
        results = []
        for kind, _collection, items in _entity_collections(world):
            for item in items:
                if _entity_id(kind, item) in ids:
                    results.append({"id": _entity_id(kind, item), "type": kind, "data": item})
        return {"entities": results, "missing_ids": sorted(ids - {item["id"] for item in results})}

    def world_consistency(ctx, _args):
        from core.world_bible import audit_world_bible_consistency
        bible = novel_manager.load_world_bible(ctx.book_title)
        warnings = audit_world_bible_consistency(bible)
        return {"warning_count": len(warnings), "warnings": warnings}

    def context_report(ctx, args):
        report = novel_manager.context_assembler().assemble_chapter(ctx.book_title, int(args["chapter_num"]), args.get("chapter_title", ""), args.get("plot", ""), manual_entity_ids=args.get("manual_entity_ids", []))
        return {"preview": report.preview(), "context": report.render()}

    def project_summary(ctx, _args):
        meta = novel_manager.load_meta(ctx.book_title)
        return {"title": meta.title, "total_chapters": meta.total_chapters, "author_plan": meta.author_plan, "summary": novel_manager.load_summary(ctx.book_title), "active_path": novel_manager.get_active_path_nodes(ctx.book_title)}

    def project_author_plan(ctx, _args):
        meta = novel_manager.load_meta(ctx.book_title)
        return {"author_plan": meta.author_plan, "writing_demand": meta.writing_demand, "protagonist_bio": meta.protagonist_bio, "background_story": meta.background_story, "genre": meta.genre, "style_tone": meta.style_tone}

    def project_active_state(ctx, _args):
        meta = novel_manager.load_meta(ctx.book_title)
        try:
            smart_summary = novel_manager.load_smart_summary(ctx.book_title)
        except Exception:
            smart_summary = ""
        return {"book_id": meta.book_id, "active_path": novel_manager.get_active_path_nodes(ctx.book_title), "summary": novel_manager.load_summary(ctx.book_title), "smart_summary": smart_summary}

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

    def save_advice(ctx, args):
        artifact_id = ctx.repository.save_artifact(ctx.run_id, "writing_advice", args["content"], {"title": args.get("title", "写作构思")})
        return {"artifact_id": artifact_id, "saved": True}

    def propose_chapter(ctx, args):
        service = ChangeSetService(novel_manager, ctx.book_title, ctx.repository)
        change = service.propose_chapter(ctx.run_id, ctx.book_id, int(args["chapter_num"]), args["chapter_title"], args["content"], args.get("parent_id", ""), args.get("reason", ""))
        return {"change_set_id": change.change_set_id, "requires_approval": True, "operation_count": len(change.operations)}

    def propose_world(ctx, args):
        service = ChangeSetService(novel_manager, ctx.book_title, ctx.repository)
        change = service.propose_world_bible(ctx.run_id, ctx.book_id, args["world_bible"], args.get("reason", ""))
        return {"change_set_id": change.change_set_id, "requires_approval": True, "operation_count": len(change.operations)}

    def propose_world_patch(ctx, args):
        service = ChangeSetService(novel_manager, ctx.book_title, ctx.repository)
        change = service.propose_world_patch(ctx.run_id, ctx.book_id, args.get("operations", []), args.get("reason", ""))
        return {"change_set_id": change.change_set_id, "requires_approval": True, "operation_count": len(change.operations)}

    def todo(ctx, args):
        return {"todo": args["items"]}

    def conversation_read(_ctx, args):
        if conversation_manager is None:
            return {"available": False, "message": "未配置角色扮演会话服务"}
        record = conversation_manager.load_conversation(args["conversation_id"])
        return record or {"available": False}

    def web_search(ctx, args):
        if web_search_config is None or not web_search_config.is_available():
            return {"available": False, "error": "网页搜索未启用或未配置"}
        return WebSearchClient(web_search_config).search(args["query"], args.get("max_results"))

    registry.register(ToolSpec("chapter.read", "读取指定活跃章节。", _object_schema({"chapter_num": {"type": "integer"}}, ["chapter_num"]), chapter_read))
    registry.register(ToolSpec("chapter.read_node", "按章节树节点 ID 读取具体版本。", _object_schema({"node_id": {"type": "string"}}, ["node_id"]), chapter_read_node, max_result_chars=20000))
    registry.register(ToolSpec("chapter.read_range", "读取活跃路径中相邻若干章。", _object_schema({"center_chapter": {"type": "integer"}, "before": {"type": "integer"}, "after": {"type": "integer"}}, ["center_chapter"]), chapter_read_range, max_result_chars=24000))
    registry.register(ToolSpec("chapter.search", "在活跃章节路径中搜索文本。", _object_schema({"query": {"type": "string"}, "limit": {"type": "integer"}}, ["query"]), chapter_search))
    registry.register(ToolSpec("chapter.summary_search", "搜索活跃路径章节摘要。", _object_schema({"query": {"type": "string"}, "limit": {"type": "integer"}}, []), chapter_summary_search))
    registry.register(ToolSpec("world_bible.read", "读取结构化世界书。", _object_schema({}), world_read, max_result_chars=16000))
    registry.register(ToolSpec("world_bible.search", "按名称、类型和关键词检索世界书实体。", _object_schema({"query": {"type": "string"}, "entity_type": {"type": "string"}, "limit": {"type": "integer"}}, []), world_search))
    registry.register(ToolSpec("world_bible.read_entities", "按实体 ID 读取完整世界书条目。", _object_schema({"entity_ids": {"type": "array", "items": {"type": "string"}}}, ["entity_ids"]), world_read_entities, max_result_chars=16000))
    registry.register(ToolSpec("world_bible.consistency", "运行世界书一致性检查。", _object_schema({}), world_consistency, allowed_agents=["world_bible_manager", "continuity_editor", "project_maintainer", "chapter_supervisor"]))
    registry.register(ToolSpec("agent.context_report", "构建并解释渐进式章节上下文。", _object_schema({"chapter_num": {"type": "integer"}, "chapter_title": {"type": "string"}, "plot": {"type": "string"}, "manual_entity_ids": {"type": "array", "items": {"type": "string"}}}, ["chapter_num"]), context_report, max_result_chars=18000))
    registry.register(ToolSpec("project.summary", "读取书籍元数据、摘要和活跃路径。", _object_schema({}), project_summary))
    registry.register(ToolSpec("project.author_plan", "读取作者规划、主角设定、世界观和写作约束。", _object_schema({}), project_author_plan))
    registry.register(ToolSpec("project.active_state", "读取活跃路径、剧情摘要和当前项目状态。", _object_schema({}), project_active_state, max_result_chars=16000))
    registry.register(ToolSpec("project.integrity", "检查项目结构和数据完整性。", _object_schema({}), project_integrity, allowed_agents=["project_maintainer"]))
    registry.register(ToolSpec("chapter.write_draft", "将内容写入加密 Agent 草稿区。", _object_schema({"name": {"type": "string"}, "content": {"type": "string"}}, ["name", "content"]), write_draft, required_permission="draft_write", read_only=False))
    registry.register(ToolSpec("agent.save_advice", "将顾问构思保存为加密 Artifact。", _object_schema({"title": {"type": "string"}, "content": {"type": "string"}}, ["content"]), save_advice, required_permission="draft_write", read_only=False, allowed_agents=["writing_advisor"]))
    registry.register(ToolSpec("chapter.propose", "提出正式章节版本变更，等待用户审批。", _object_schema({"chapter_num": {"type": "integer"}, "chapter_title": {"type": "string"}, "content": {"type": "string"}, "parent_id": {"type": "string"}, "reason": {"type": "string"}}, ["chapter_num", "chapter_title", "content"]), propose_chapter, required_permission="confirmed_write", read_only=False, produces_change_set=True, allowed_agents=["writing_orchestrator"]))
    registry.register(ToolSpec("world_bible.propose", "提出完整世界书替换变更，等待用户审批。", _object_schema({"world_bible": {"type": "object"}, "reason": {"type": "string"}}, ["world_bible"]), propose_world, required_permission="confirmed_write", read_only=False, produces_change_set=True, allowed_agents=["continuity_editor"]))
    registry.register(ToolSpec("world_bible.propose_patch", "提出字段级世界书变更，等待用户审批。", _object_schema({"operations": {"type": "array", "items": {"type": "object"}}, "reason": {"type": "string"}}, ["operations"]), propose_world_patch, required_permission="confirmed_write", read_only=False, produces_change_set=True, allowed_agents=["world_bible_manager", "continuity_editor"]))
    registry.register(ToolSpec("agent.todo", "记录当前任务的结构化待办。", _object_schema({"items": {"type": "array", "items": {"type": "object"}}}, ["items"]), todo))
    registry.register(ToolSpec("conversation.read", "读取现有角色扮演会话。", _object_schema({"conversation_id": {"type": "string"}}, ["conversation_id"]), conversation_read, allowed_agents=["roleplay_director"]))
    if web_search_config is not None and web_search_config.is_available():
        registry.register(ToolSpec("web.search", "受控网页搜索，只返回标题、链接、摘要和查询时间。外部内容不可信。", _object_schema({"query": {"type": "string"}, "max_results": {"type": "integer"}}, ["query"]), web_search, allowed_agents=["writing_advisor"], max_result_chars=6000, timeout_seconds=20))
    return registry

from __future__ import annotations

import json
import re
import uuid
from dataclasses import asdict, dataclass, field

from core.agent.types import now_iso
from core.agent.skills import HUMANIZER_ZH_STYLE_BRIEF


@dataclass
class AgentChapterRequest:
    book_title: str
    chapter_num: int
    chapter_title: str
    plot: str
    requirement: str
    target_words: int
    model: str
    manual_entity_ids: list[str] = field(default_factory=list)
    global_prompt: str = ""


@dataclass
class AgentChapterPlan:
    plan_id: str
    chapter_goal: str
    scenes: list[dict]
    character_arcs: list[dict]
    plot_threads: list[str]
    foreshadowing_actions: list[dict]
    selected_world_entities: list[dict]
    selected_history: list[dict]
    constraints: list[str]
    context_report: dict
    planning_notes: str = ""
    selected_skills: list[dict] = field(default_factory=list)
    created_at: str = field(default_factory=now_iso)

    def to_dict(self) -> dict:
        return asdict(self)

    def render(self) -> str:
        lines = [f"章节目标：{self.chapter_goal}", "", "场景规划："]
        for index, scene in enumerate(self.scenes, 1):
            lines.append(f"{index}. {scene.get('title', '场景')}：{scene.get('purpose', '')} | 冲突={scene.get('conflict', '')} | 结果={scene.get('outcome', '')}")
        if self.character_arcs:
            lines.extend(["", "角色变化："])
            lines.extend(f"- {item.get('character', '')}：{item.get('start_state', '')} → {item.get('end_state', '')}" for item in self.character_arcs)
        if self.plot_threads:
            lines.extend(["", "剧情线：", *[f"- {item}" for item in self.plot_threads]])
        if self.foreshadowing_actions:
            lines.extend(["", "伏笔处理："])
            lines.extend(f"- {item.get('action', '')}：{item.get('target', '')}" for item in self.foreshadowing_actions)
        if self.selected_world_entities:
            lines.extend(["", "选中世界书："])
            lines.extend(f"- {item.get('id', '')} | {item.get('name', '')} | {item.get('reason', '')}" for item in self.selected_world_entities)
        if self.selected_history:
            lines.extend(["", "选中历史剧情："])
            lines.extend(f"- 第{item.get('chapter_num', 0)}章「{item.get('title', '')}」：{item.get('reason', '')}" for item in self.selected_history)
        if self.constraints:
            lines.extend(["", "硬约束：", *[f"- {item}" for item in self.constraints]])
        if self.selected_skills:
            lines.extend(["", "本次使用 Skills："])
            lines.extend(f"- {item.get('name', item.get('id', ''))} v{item.get('version', '1')}：{item.get('reason', '')}" for item in self.selected_skills)
        if self.context_report.get("preview"):
            lines.extend(["", "上下文统计：", self.context_report["preview"]])
        return "\n".join(lines)


@dataclass
class AgentChapterResult:
    plan_id: str
    prompt: str
    context_report: dict


class AgentPlanError(RuntimeError):
    pass


class AgentChapterGenerationService:
    """Deterministic chapter planning and two-stage context selection."""

    def __init__(self, novel_manager, client, *, skills_enabled: bool = True) -> None:
        self.manager = novel_manager
        self.client = getattr(client, "raw_client", client)
        self.skills_enabled = skills_enabled

    def prepare(self, request: AgentChapterRequest) -> AgentChapterPlan:
        meta = self.manager.load_meta(request.book_title)
        bible = self.manager.load_world_bible(request.book_title)
        index = self._world_index(bible, request.book_title)
        history = self._history_candidates(request.book_title)
        continuity = self.manager.build_continuity_contract(request.book_title, request.chapter_num, request.chapter_title, request.plot)
        skills = self._select_skills(request)
        planning_input = {
            "book": request.book_title, "chapter_num": request.chapter_num,
            "chapter_title": request.chapter_title, "target_words": request.target_words,
            "protagonist": meta.protagonist_bio, "world_background": meta.background_story,
            "writing_requirement": request.requirement or meta.writing_demand,
            "author_plan": meta.author_plan, "user_plot": request.plot,
            "user_preferences": request.global_prompt, "continuity_contract": continuity,
            "world_index": index, "history_index": history,
            "skills": skills.text,
        }
        raw_plan = self._call_planner(planning_input, request.model)
        try:
            data = self._validate_plan(raw_plan, index, history)
        except Exception as validation_error:
            repair_input = {**planning_input, "invalid_plan": raw_plan, "validation_error": str(validation_error), "instruction": "修复 invalid_plan，只返回符合既定字段和类型的完整 JSON。"}
            data = self._validate_plan(self._call_planner(repair_input, request.model, attempts=1), index, history)
        selected_ids = list(dict.fromkeys([*request.manual_entity_ids, *[item["id"] for item in data["selected_world_entities"]]]))
        context = self.manager.context_assembler().assemble_chapter(
            request.book_title, request.chapter_num, request.chapter_title, request.plot,
            global_prompt=request.global_prompt, manual_entity_ids=selected_ids,
            max_recent=5, client=self.client, model=request.model,
        )
        selected_history = self._resolve_history(data.get("selected_history_chapters", []), history)
        history_text = self._selected_history_text(selected_history)
        rendered_context = context.render() + (("\n\n" + history_text) if history_text else "")
        sources = [asdict(item) for item in context.sections]
        if history_text:
            sources.append({"source": "agent_history", "title": "Agent 精选历史剧情", "content": history_text, "reason": "近期承接或 Agent 语义命中", "budget": len(history_text), "original_chars": len(history_text), "omitted_chars": 0})
        injected_chars = context.total_chars + len(history_text)
        report = {
            "preview": context.preview() + (f"\n- Agent 精选历史剧情: {len(history_text)} 字；来源=agent_history；原因=近期承接或 Agent 语义命中" if history_text else ""),
            "content": rendered_context,
            "candidate_chars": injected_chars + context.omitted_chars,
            "injected_chars": injected_chars, "omitted_chars": context.omitted_chars,
            "sources": sources,
            "skills": skills.summaries,
            "skills_text": skills.text,
        }
        plan = AgentChapterPlan(
            f"chapter_plan_{uuid.uuid4().hex}", data["chapter_goal"], data["scenes"],
            data["character_arcs"], data["plot_threads"], data["foreshadowing_actions"],
            data["selected_world_entities"], selected_history,
            data["constraints"], report, data.get("planning_notes", ""),
            skills.summaries,
        )
        workspace = self.manager.get_workspace(request.book_title)
        workspace.storage.write_json(
            f"{workspace.agent_root}/chapter_runs/{plan.plan_id}.json",
            {"schema_version": 1, "run_id": plan.plan_id, "status": "prepared", "request": asdict(request), "plan": plan.to_dict(), "created_at": now_iso()},
        )
        return plan

    def generate(self, request: AgentChapterRequest, approved_plan: AgentChapterPlan) -> AgentChapterResult:
        meta = self.manager.load_meta(request.book_title)
        prompt = "\n\n".join(filter(None, [
            f"【Agent 已确认章节计划】\n{approved_plan.render()}",
            f"【Agent 精选上下文】\n{approved_plan.context_report.get('content', '')}",
            f"【本次启用 Skills】\n{approved_plan.context_report.get('skills_text', '')}" if approved_plan.context_report.get("skills_text") else "",
            f"【风格硬约束】\n{HUMANIZER_ZH_STYLE_BRIEF}",
            f"【主角设定】\n{meta.protagonist_bio}" if meta.protagonist_bio else "",
            f"【世界观】\n{meta.background_story}" if meta.background_story else "",
            f"【作者规划】\n{meta.author_plan}" if meta.author_plan else "",
            f"【本章用户剧情】\n{request.plot}" if request.plot else "",
            f"【本章写作要求】\n{request.requirement}" if request.requirement else "",
            f"请严格依据以上计划创作第{request.chapter_num}章「{request.chapter_title}」，正文不少于{request.target_words}字。只输出小说正文。",
        ]))
        workspace = self.manager.get_workspace(request.book_title)
        workspace.storage.write_json(
            f"{workspace.agent_root}/chapter_runs/{approved_plan.plan_id}.json",
            {"schema_version": 1, "run_id": approved_plan.plan_id, "status": "approved", "request": asdict(request), "plan": approved_plan.to_dict(), "prompt_chars": len(prompt), "updated_at": now_iso()},
        )
        return AgentChapterResult(approved_plan.plan_id, prompt, approved_plan.context_report)

    def _select_skills(self, request: AgentChapterRequest):
        from core.agent.repository import AgentRepository
        from core.agent.skills import SkillSelection, SkillService
        if not self.skills_enabled:
            return SkillSelection()
        repository = AgentRepository(self.manager.get_workspace(request.book_title))
        return SkillService(repository).select_for_task(
            "chapter_generation", "writing_orchestrator",
            "\n".join([request.chapter_title, request.plot, request.requirement, request.global_prompt]),
        )
    def _call_planner(self, payload: dict, model: str, attempts: int = 2) -> dict:
        prompt = """你是长篇小说章节规划 Agent。根据设定、连续性契约、世界书轻量索引和章节摘要索引输出严格 JSON。不得把作者规划当成已发生事实。只选择本章真正有用的实体和历史章节。字段：chapter_goal:string, scenes:[{title,purpose,conflict,outcome}], character_arcs:[{character,start_state,end_state}], plot_threads:[string], foreshadowing_actions:[{action,target}], selected_world_entities:[{id,name,reason}], selected_history_chapters:[integer], constraints:[string], planning_notes:string。至少一个场景，不得输出 Markdown。\n\n输入：""" + json.dumps(payload, ensure_ascii=False)
        last_error = None
        for _attempt in range(max(1, attempts)):
            try:
                response = self.client.chat.completions.create(model=model, messages=[{"role": "user", "content": prompt}], temperature=0.2, max_tokens=8192)
                return self._parse_json(response.choices[0].message.content or "")
            except Exception as exc:
                last_error = exc
                prompt += "\n\n上一次输出无效。只输出完整合法 JSON，字段和类型必须严格匹配。"
        raise AgentPlanError(f"Agent 章节规划失败: {last_error}")

    @staticmethod
    def _parse_json(text: str) -> dict:
        value = str(text or "").strip()
        blocks = re.findall(r"```(?:json)?\s*(.*?)```", value, re.DOTALL | re.IGNORECASE)
        if blocks:
            value = blocks[0].strip()
        start, end = value.find("{"), value.rfind("}")
        if start >= 0 and end >= start:
            value = value[start:end + 1]
        data = json.loads(value)
        if not isinstance(data, dict):
            raise AgentPlanError("章节规划不是 JSON 对象")
        return data

    @staticmethod
    def _validate_plan(data: dict, index: list[dict], history: list[dict]) -> dict:
        list_fields = ("scenes", "character_arcs", "plot_threads", "foreshadowing_actions", "selected_world_entities", "selected_history_chapters", "constraints")
        if not isinstance(data.get("chapter_goal"), str) or not data["chapter_goal"].strip():
            raise AgentPlanError("章节规划缺少 chapter_goal")
        for key in list_fields:
            if not isinstance(data.get(key), list):
                raise AgentPlanError(f"章节规划字段 {key} 必须是数组")
        if not data["scenes"] or not all(isinstance(item, dict) for item in data["scenes"]):
            raise AgentPlanError("章节规划至少需要一个有效场景")
        valid_entities = {item["id"]: item for item in index}
        selected = []
        for item in data["selected_world_entities"]:
            if not isinstance(item, dict) or str(item.get("id", "")) not in valid_entities:
                continue
            entity = valid_entities[str(item["id"])]
            selected.append({"id": entity["id"], "name": entity["name"], "kind": entity["kind"], "reason": str(item.get("reason", "Agent 命中"))[:300]})
        data["selected_world_entities"] = selected
        valid_chapters = {item["chapter_num"] for item in history}
        data["selected_history_chapters"] = [int(item) for item in data["selected_history_chapters"] if str(item).isdigit() and int(item) in valid_chapters]
        data["character_arcs"] = [item for item in data["character_arcs"] if isinstance(item, dict)]
        data["foreshadowing_actions"] = [item for item in data["foreshadowing_actions"] if isinstance(item, dict)]
        data["plot_threads"] = [str(item) for item in data["plot_threads"] if str(item).strip()]
        data["constraints"] = [str(item) for item in data["constraints"] if str(item).strip()]
        return data

    def _world_index(self, bible, book_title: str) -> list[dict]:
        from core.context_assembler import _world_entities
        policies = self.manager.get_workspace(book_title).load_context_policies()
        result = []
        for entity_id, kind, name, item in _world_entities(bible):
            policy = dict(policies.get(entity_id) or {})
            if policy.get("enabled", True) is False:
                continue
            data = asdict(item) if hasattr(item, "__dataclass_fields__") else dict(item)
            result.append({"id": entity_id, "kind": kind, "name": name, "brief": str(policy.get("brief_description") or data.get("description") or data.get("current_goal") or data.get("hint") or "")[:240], "keywords": policy.get("keywords", []), "load_mode": policy.get("load_mode", "auto"), "priority": int(policy.get("priority", 50) or 50)})
        return sorted(result, key=lambda item: (item["load_mode"] == "resident", item["priority"]), reverse=True)

    def _history_candidates(self, title: str) -> list[dict]:
        return [{"chapter_num": int(item["chapter_num"]), "title": item.get("title", ""), "summary": str(item.get("summary", ""))[:1000], "node_id": item.get("node_id", "")} for item in self.manager.list_active_summary_entries(title) if item.get("summary")]

    @staticmethod
    def _resolve_history(selected: list[int], history: list[dict]) -> list[dict]:
        by_num = {item["chapter_num"]: item for item in history}
        recent = [item["chapter_num"] for item in history[-3:]]
        ordered = list(dict.fromkeys([*recent, *selected]))
        return [{**by_num[number], "reason": "近期承接" if number in recent else "Agent 语义命中"} for number in ordered if number in by_num]

    @staticmethod
    def _selected_history_text(history: list[dict]) -> str:
        if not history:
            return ""
        return "【Agent 精选历史剧情】\n" + "\n".join(
            f"第{item.get('chapter_num', 0)}章「{item.get('title', '')}」：{item.get('summary', '')}"
            for item in history
        )

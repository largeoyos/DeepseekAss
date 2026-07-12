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
    must_happen: list[str] = field(default_factory=list)
    may_happen: list[str] = field(default_factory=list)
    must_not_happen: list[str] = field(default_factory=list)
    withheld_reveals: list[str] = field(default_factory=list)
    end_state_requirements: list[str] = field(default_factory=list)
    planning_notes: str = ""
    selected_skills: list[dict] = field(default_factory=list)
    created_at: str = field(default_factory=now_iso)

    def to_dict(self) -> dict:
        return asdict(self)

    def render(self) -> str:
        lines = [f"章节目标：{self.chapter_goal}"]
        contract_sections = (
            ("必须发生", self.must_happen),
            ("可以发生", self.may_happen),
            ("禁止发生", self.must_not_happen),
            ("本章不得揭示", self.withheld_reveals),
            ("章节结束状态", self.end_state_requirements),
        )
        for title, items in contract_sections:
            if items:
                lines.extend([f"\n{title}：", *[f"- {item}" for item in items]])
        lines.extend(["", "场景契约："])
        for index, scene in enumerate(self.scenes, 1):
            lines.extend([
                f"{index}. [{scene.get('scene_id', f'scene_{index}')}] {scene.get('title', '场景')}（约 {scene.get('target_words', 0)} 字）",
                f"   - 功能：{scene.get('purpose', '')}",
                f"   - POV/时空：{scene.get('pov_character', '')}｜{scene.get('time', '')}｜{scene.get('location', '')}",
                f"   - 入场状态：{scene.get('entry_state', '')}",
                f"   - 目标与阻力：{scene.get('goal', '')}｜{scene.get('conflict', '')}",
            ])
            if scene.get("key_actions"):
                lines.append("   - 关键行动：" + "；".join(scene["key_actions"]))
            if scene.get("information_released"):
                lines.append("   - 信息释放：" + "；".join(scene["information_released"]))
            lines.extend([
                f"   - 转折：{scene.get('turning_point', '')}",
                f"   - 选择与代价：{scene.get('choice', '')}｜{scene.get('cost', '')}",
                f"   - 结果与离场状态：{scene.get('outcome', '')}｜{scene.get('exit_state', '')}",
                f"   - 不可逆变化：{scene.get('irreversible_change', '')}",
            ])
            if scene.get("forbidden"):
                lines.append("   - 场景禁区：" + "；".join(scene["forbidden"]))
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
            data = self._validate_plan(raw_plan, index, history, request.target_words)
        except Exception as validation_error:
            repair_input = {**planning_input, "invalid_plan": raw_plan, "validation_error": str(validation_error), "instruction": "修复 invalid_plan，只返回符合既定字段和类型的完整 JSON。"}
            data = self._validate_plan(
                self._call_planner(repair_input, request.model, attempts=1),
                index, history,
                request.target_words,
            )
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
            plan_id=f"chapter_plan_{uuid.uuid4().hex}", chapter_goal=data["chapter_goal"], scenes=data["scenes"],
            character_arcs=data["character_arcs"], plot_threads=data["plot_threads"],
            foreshadowing_actions=data["foreshadowing_actions"],
            selected_world_entities=data["selected_world_entities"], selected_history=selected_history,
            constraints=data["constraints"], context_report=report,
            must_happen=data["must_happen"], may_happen=data["may_happen"],
            must_not_happen=data["must_not_happen"], withheld_reveals=data["withheld_reveals"],
            end_state_requirements=data["end_state_requirements"],
            planning_notes=data.get("planning_notes", ""), selected_skills=skills.summaries,
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
        schema = (
            "你是长篇小说章节规划 Agent。生成一份可被正文生成器与监督器逐项验证的章节契约。"
            "不得把作者规划当成已发生事实，只选择本章真正有用的实体和历史章节。只输出严格 JSON，不得输出 Markdown。\n\n"
            "字段必须完整：chapter_goal:string, must_happen:[string], may_happen:[string], "
            "must_not_happen:[string], withheld_reveals:[string], end_state_requirements:[string], "
            "scenes:[{scene_id:string,title:string,purpose:string,pov_character:string,time:string,location:string,"
            "entry_state:string,goal:string,conflict:string,key_actions:[string],information_released:[string],"
            "turning_point:string,choice:string,cost:string,outcome:string,exit_state:string,"
            "irreversible_change:string,target_words:integer,forbidden:[string]}], "
            "character_arcs:[{character:string,start_state:string,end_state:string,trigger:string,choice:string,cost:string}], "
            "plot_threads:[string], foreshadowing_actions:[{action:string,target:string}], selected_world_entities:[{id:string,name:string,reason:string}], "
            "selected_history_chapters:[integer], constraints:[string], planning_notes:string。\n\n"
            "每个场景必须产生至少一种可验证变化并写入 irreversible_change；角色变化必须有触发、选择和代价；must_happen 与结束状态不得空泛；不得用无功能场景凑字数。\n\n输入："
        )
        prompt = schema + json.dumps(payload, ensure_ascii=False)
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
    def _validate_plan(data: dict, index: list[dict], history: list[dict], target_words: int = 0) -> dict:
        list_fields = (
            "must_happen", "may_happen", "must_not_happen", "withheld_reveals", "end_state_requirements",
            "scenes", "character_arcs", "plot_threads", "foreshadowing_actions",
            "selected_world_entities", "selected_history_chapters", "constraints",
        )
        if not isinstance(data.get("chapter_goal"), str) or not data["chapter_goal"].strip():
            raise AgentPlanError("章节规划缺少 chapter_goal")
        for key in list_fields:
            if not isinstance(data.get(key), list):
                raise AgentPlanError(f"章节规划字段 {key} 必须是数组")
        if not data["scenes"] or not all(isinstance(item, dict) for item in data["scenes"]):
            raise AgentPlanError("章节规划至少需要一个有效场景")
        if not data["must_happen"]:
            raise AgentPlanError("章节契约至少需要一项 must_happen")
        if not data["end_state_requirements"]:
            raise AgentPlanError("章节契约至少需要一项 end_state_requirements")
        scene_string_fields = (
            "scene_id", "title", "purpose", "pov_character", "time", "location",
            "entry_state", "goal", "conflict", "turning_point", "choice", "cost",
            "outcome", "exit_state", "irreversible_change",
        )
        scene_list_fields = ("key_actions", "information_released", "forbidden")
        normalized_scenes = []
        seen_scene_ids = set()
        for scene_number, scene in enumerate(data["scenes"], 1):
            missing = [key for key in scene_string_fields if not isinstance(scene.get(key), str) or not scene[key].strip()]
            if missing:
                raise AgentPlanError(f"场景 {scene_number} 缺少可验证字段: {', '.join(missing)}")
            for key in scene_list_fields:
                if not isinstance(scene.get(key), list):
                    raise AgentPlanError(f"场景 {scene_number} 字段 {key} 必须是数组")
            scene_id = str(scene["scene_id"]).strip()
            if scene_id in seen_scene_ids:
                raise AgentPlanError(f"场景 ID 重复: {scene_id}")
            seen_scene_ids.add(scene_id)
            normalized = {key: str(scene[key]).strip() for key in scene_string_fields}
            for key in scene_list_fields:
                normalized[key] = [str(item).strip() for item in scene[key] if str(item).strip()]
            try:
                normalized["target_words"] = max(0, int(scene.get("target_words", 0) or 0))
            except (TypeError, ValueError):
                raise AgentPlanError(f"场景 {scene_number} 的 target_words 必须是整数")
            normalized_scenes.append(normalized)
        data["scenes"] = AgentChapterGenerationService._rebalance_scene_words(normalized_scenes, target_words)
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
        arc_fields = ("character", "start_state", "end_state", "trigger", "choice", "cost")
        data["character_arcs"] = [
            {key: str(item.get(key, "")).strip() for key in arc_fields}
            for item in data["character_arcs"] if isinstance(item, dict)
        ]
        data["foreshadowing_actions"] = [item for item in data["foreshadowing_actions"] if isinstance(item, dict)]
        data["plot_threads"] = [str(item) for item in data["plot_threads"] if str(item).strip()]
        data["constraints"] = [str(item) for item in data["constraints"] if str(item).strip()]
        for key in ("must_happen", "may_happen", "must_not_happen", "withheld_reveals", "end_state_requirements"):
            data[key] = [str(item).strip() for item in data[key] if str(item).strip()]
        return data


    @staticmethod
    def _rebalance_scene_words(scenes: list[dict], target_words: int) -> list[dict]:
        """Make scene budgets add up exactly without trusting model arithmetic."""
        target = max(0, int(target_words or 0))
        if not scenes or target <= 0:
            return scenes
        weights = [max(1, int(scene.get("target_words", 0) or 0)) for scene in scenes]
        total_weight = sum(weights)
        allocated = []
        used = 0
        for index, weight in enumerate(weights):
            if index == len(weights) - 1:
                value = target - used
            else:
                value = max(1, round(target * weight / total_weight))
                used += value
            allocated.append(max(0, value))
        for scene, value in zip(scenes, allocated):
            scene["target_words"] = value
        return scenes

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

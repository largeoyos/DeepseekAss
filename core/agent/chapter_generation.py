from __future__ import annotations

import json
import re
import uuid
from dataclasses import asdict, dataclass, field

from core.agent.types import now_iso
from core.agent.skills import HUMANIZER_ZH_STYLE_BRIEF
from core.style_profiles import render_style_prompt, resolve_style


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
    style_profile_id: str = "follow_book"
    style_strength: str = "follow_book"


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
    candidate_id: str = ""
    strategy: str = ""
    strategy_summary: str = ""
    selection_reason: str = ""
    critic: dict = field(default_factory=dict)
    candidate_plans: list[dict] = field(default_factory=list)
    recommended_candidate_id: str = ""
    director_state: dict = field(default_factory=dict)
    created_at: str = field(default_factory=now_iso)

    def to_dict(self, *, include_candidates: bool = True) -> dict:
        data = asdict(self)
        if not include_candidates:
            data["candidate_plans"] = []
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "AgentChapterPlan":
        fields = cls.__dataclass_fields__
        return cls(**{name: data[name] for name in fields if name in data})

    def render(self) -> str:
        lines = [f"章节目标：{self.chapter_goal}"]
        if self.strategy:
            lines.insert(0, f"方案策略：{self.strategy}")
        if self.strategy_summary:
            lines.insert(1, f"策略说明：{self.strategy_summary}")
        if self.selection_reason:
            lines.insert(2, f"推荐理由：{self.selection_reason}")
        if self.director_state:
            director_lines = [
                f"卷目标：{self.director_state.get('current_volume_goal', '')}",
                f"主角阶段目标：{self.director_state.get('protagonist_stage_goal', '')}",
                f"核心矛盾压力：{self.director_state.get('core_conflict_pressure', '')}",
                f"下一转折距离：{self.director_state.get('next_turn_distance', '')}章",
                f"主线停滞：{self.director_state.get('chapters_without_main_progress', 0)}章",
            ]
            lines.extend(["", "卷级导演约束：", *[f"- {item}" for item in director_lines if item]])
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

    def __init__(
        self, novel_manager, client, *, skills_enabled: bool = True,
        multi_plan_enabled: bool = False,
    ) -> None:
        self.manager = novel_manager
        self.client = getattr(client, "raw_client", client)
        self.skills_enabled = skills_enabled
        self.multi_plan_enabled = multi_plan_enabled

    def prepare(self, request: AgentChapterRequest) -> AgentChapterPlan:
        meta = self.manager.load_meta(request.book_title)
        bible = self.manager.load_world_bible(request.book_title)
        index = self._world_index(bible, request.book_title)
        history = self._history_candidates(request.book_title)
        continuity = self.manager.build_continuity_contract(
            request.book_title, request.chapter_num, request.chapter_title, request.plot
        )
        skills = self._select_skills(request)
        resolved_style = resolve_style(self.manager, request.book_title, profile_id=request.style_profile_id, strength=request.style_strength)
        style_prompt = render_style_prompt(resolved_style, task_context="\n".join([request.chapter_title, request.plot, request.requirement]))
        director_state = self._load_director_state(request.book_title)
        planning_input = {
            "book": request.book_title, "chapter_num": request.chapter_num,
            "chapter_title": request.chapter_title, "target_words": request.target_words,
            "protagonist": meta.protagonist_bio, "world_background": meta.background_story,
            "writing_requirement": request.requirement or meta.writing_demand,
            "author_plan": meta.author_plan, "user_plot": request.plot,
            "user_preferences": request.global_prompt, "continuity_contract": continuity,
            "world_index": index, "history_index": history, "skills": skills.text,
            "story_director": director_state,
            "style_profile": style_prompt,
        }
        if self.multi_plan_enabled:
            raw_options = self._call_plan_options(planning_input, request.model)
            validated_options = []
            validation_errors = []
            for option in raw_options:
                try:
                    data = self._validate_plan(option.get("plan", {}), index, history, request.target_words)
                    validated_options.append({**option, "plan": data})
                except Exception as exc:
                    validation_errors.append(str(exc))
            if not validated_options:
                repaired = self._call_planner(
                    {**planning_input, "invalid_options": raw_options, "validation_errors": validation_errors,
                     "instruction": "修复为一份完整、可验证的章节 JSON 契约。"},
                    request.model, attempts=1,
                )
                validated_options = [{
                    "option_id": "option_1", "strategy": "稳健推进",
                    "summary": "修复后的可执行章节方案。",
                    "plan": self._validate_plan(repaired, index, history, request.target_words),
                }]
        else:
            raw_plan = self._call_planner(planning_input, request.model)
            try:
                data = self._validate_plan(raw_plan, index, history, request.target_words)
            except Exception as validation_error:
                repair_input = {
                    **planning_input, "invalid_plan": raw_plan,
                    "validation_error": str(validation_error),
                    "instruction": "修复 invalid_plan，只返回符合既定字段和类型的完整 JSON。",
                }
                data = self._validate_plan(
                    self._call_planner(repair_input, request.model, attempts=1),
                    index, history, request.target_words,
                )
            validated_options = [{
                "option_id": "option_1", "strategy": "单方案规划",
                "summary": "默认单方案章节规划。",
                "plan": data,
            }]

        plan_id = f"chapter_plan_{uuid.uuid4().hex}"
        candidates = [
            self._build_plan(
                request, option["plan"], index, history, skills, plan_id, director_state,
                option.get("option_id", f"option_{position}"),
                option.get("strategy", "剧情推进"),
                option.get("summary", ""),
            )
            for position, option in enumerate(validated_options, 1)
        ]
        critique = self._compare_candidates(request, candidates) if len(candidates) > 1 else {}
        evaluations = {
            str(item.get("option_id", "")): item
            for item in critique.get("evaluations", []) if isinstance(item, dict)
        }
        for candidate in candidates:
            candidate.critic = evaluations.get(candidate.candidate_id, {})
        recommended_id = str(critique.get("recommended_option_id", "") or candidates[0].candidate_id)
        selected = next((item for item in candidates if item.candidate_id == recommended_id), candidates[0])
        selected.selection_reason = str(
            critique.get("recommendation_reason", "")
            or selected.critic.get("reason", "")
            or "该方案最贴合当前章节约束。"
        )
        selected.recommended_candidate_id = selected.candidate_id
        selected.candidate_plans = [item.to_dict(include_candidates=False) for item in candidates]
        workspace = self.manager.get_workspace(request.book_title)
        workspace.storage.write_json(
            f"{workspace.agent_root}/chapter_runs/{plan_id}.json",
            {
                "schema_version": 2, "run_id": plan_id, "status": "prepared",
                "request": asdict(request), "plan": selected.to_dict(),
                "critic": critique, "story_director": director_state, "created_at": now_iso(),
            },
        )
        return selected

    def generate(self, request: AgentChapterRequest, approved_plan: AgentChapterPlan) -> AgentChapterResult:
        meta = self.manager.load_meta(request.book_title)
        resolved_style = resolve_style(self.manager, request.book_title, profile_id=request.style_profile_id, strength=request.style_strength)
        style_prompt = render_style_prompt(resolved_style, task_context="\n".join([request.chapter_title, request.plot, request.requirement]))
        prompt = "\n\n".join(filter(None, [
            f"【Agent 已确认章节计划】\n{approved_plan.render()}",
            f"【Agent 精选上下文】\n{approved_plan.context_report.get('content', '')}",
            f"【本次启用 Skills】\n{approved_plan.context_report.get('skills_text', '')}" if approved_plan.context_report.get("skills_text") else "",
            f"【风格硬约束】\n{HUMANIZER_ZH_STYLE_BRIEF}",
            style_prompt,
            f"【主角设定】\n{meta.protagonist_bio}" if meta.protagonist_bio else "",
            f"【世界观】\n{meta.background_story}" if meta.background_story else "",
            f"【作者规划】\n{meta.author_plan}" if meta.author_plan else "",
            f"【本章用户剧情】\n{request.plot}" if request.plot else "",
            f"【本章写作要求】\n{request.requirement}" if request.requirement else "",
            f"请严格依据以上计划创作第{request.chapter_num}章「{request.chapter_title}」，正文不少于{request.target_words}字。只输出小说正文。",
        ]))
        workspace = self.manager.get_workspace(request.book_title)
        path = f"{workspace.agent_root}/chapter_runs/{approved_plan.plan_id}.json"
        record = workspace.storage.read_json(path, default={}) or {}
        record.update({
            "schema_version": 2,
            "run_id": approved_plan.plan_id,
            "status": "approved",
            "request": asdict(request),
            "plan": approved_plan.to_dict(),
            "prompt_chars": len(prompt),
            "updated_at": now_iso(),
        })
        workspace.storage.write_json(path, record)
        return AgentChapterResult(approved_plan.plan_id, prompt, approved_plan.context_report)

    def _build_plan(
        self, request: AgentChapterRequest, data: dict, index: list[dict], history: list[dict],
        skills, plan_id: str, director_state: dict, candidate_id: str, strategy: str,
        strategy_summary: str,
    ) -> AgentChapterPlan:
        selected_ids = list(dict.fromkeys([
            *request.manual_entity_ids,
            *[item["id"] for item in data["selected_world_entities"]],
        ]))
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
            sources.append({
                "source": "agent_history", "title": "Agent 精选历史剧情", "content": history_text,
                "reason": "近期承接或 Agent 语义命中", "budget": len(history_text),
                "original_chars": len(history_text), "omitted_chars": 0,
            })
        injected_chars = context.total_chars + len(history_text)
        report = {
            "preview": context.preview() + (
                f"\n- Agent 精选历史剧情: {len(history_text)} 字；来源=agent_history；原因=近期承接或 Agent 语义命中"
                if history_text else ""
            ),
            "content": rendered_context,
            "candidate_chars": injected_chars + context.omitted_chars,
            "injected_chars": injected_chars, "omitted_chars": context.omitted_chars,
            "sources": sources, "skills": skills.summaries, "skills_text": skills.text,
        }
        return AgentChapterPlan(
            plan_id=plan_id, chapter_goal=data["chapter_goal"], scenes=data["scenes"],
            character_arcs=data["character_arcs"], plot_threads=data["plot_threads"],
            foreshadowing_actions=data["foreshadowing_actions"],
            selected_world_entities=data["selected_world_entities"], selected_history=selected_history,
            constraints=data["constraints"], context_report=report,
            must_happen=data["must_happen"], may_happen=data["may_happen"],
            must_not_happen=data["must_not_happen"], withheld_reveals=data["withheld_reveals"],
            end_state_requirements=data["end_state_requirements"],
            planning_notes=data.get("planning_notes", ""), selected_skills=skills.summaries,
            candidate_id=candidate_id, strategy=strategy, strategy_summary=strategy_summary,
            director_state=director_state,
        )

    def _call_plan_options(self, payload: dict, model: str, attempts: int = 2) -> list[dict]:
        schema = (
            "你是长篇小说章节规划 Agent。请先用卷级导演状态控制节奏，再给出三个可执行章节方案。"
            "三个方案必须分别是“人物选择驱动”“外部事件驱动”“信息揭示驱动”，不得只换措辞；"
            "每个方案的主角选择、不可逆变化和结尾状态必须不同。只输出严格 JSON，不得输出 Markdown。\n\n"
            "输出：{options:[{option_id:string,strategy:string,summary:string,plan:{"
            "chapter_goal:string,must_happen:[string],may_happen:[string],must_not_happen:[string],"
            "withheld_reveals:[string],end_state_requirements:[string],"
            "scenes:[{scene_id:string,title:string,purpose:string,pov_character:string,time:string,location:string,"
            "entry_state:string,goal:string,conflict:string,key_actions:[string],information_released:[string],"
            "turning_point:string,choice:string,cost:string,outcome:string,exit_state:string,"
            "irreversible_change:string,target_words:integer,forbidden:[string]}],"
            "character_arcs:[{character:string,start_state:string,end_state:string,trigger:string,choice:string,cost:string}],"
            "plot_threads:[string],foreshadowing_actions:[{action:string,target:string}],"
            "selected_world_entities:[{id:string,name:string,reason:string}],selected_history_chapters:[integer],"
            "constraints:[string],planning_notes:string}}]}.\n\n输入："
        )
        prompt = schema + json.dumps(payload, ensure_ascii=False)
        last_error = None
        for _attempt in range(max(1, attempts)):
            try:
                response = self.client.chat.completions.create(
                    model=model, messages=[{"role": "user", "content": prompt}],
                    temperature=0.35, max_tokens=12000,
                )
                raw = self._parse_json(response.choices[0].message.content or "")
                options = raw.get("options") if isinstance(raw, dict) else None
                if not isinstance(options, list):
                    return [{"option_id": "option_1", "strategy": "单方案兼容模式", "summary": "", "plan": raw}]
                result = []
                for index, option in enumerate(options[:3], 1):
                    if isinstance(option, dict) and isinstance(option.get("plan"), dict):
                        result.append({
                            "option_id": str(option.get("option_id") or f"option_{index}"),
                            "strategy": str(option.get("strategy") or "剧情推进"),
                            "summary": str(option.get("summary") or ""),
                            "plan": option["plan"],
                        })
                if result:
                    return result
                raise AgentPlanError("候选方案为空")
            except Exception as exc:
                last_error = exc
                prompt += "\n\n上一次输出无效。仅返回含三个 options 的完整合法 JSON。"
        raise AgentPlanError(f"Agent 多方案章节规划失败: {last_error}")

    def _compare_candidates(self, request: AgentChapterRequest, candidates: list[AgentChapterPlan]) -> dict:
        payload = {
            "chapter": request.chapter_num,
            "director": candidates[0].director_state if candidates else {},
            "options": [{
                "option_id": item.candidate_id, "strategy": item.strategy,
                "summary": item.strategy_summary, "goal": item.chapter_goal,
                "must_happen": item.must_happen, "end_state": item.end_state_requirements,
                "choice_cost": [
                    {"choice": scene.get("choice", ""), "cost": scene.get("cost", ""),
                     "change": scene.get("irreversible_change", "")}
                    for scene in item.scenes
                ],
            } for item in candidates],
        }
        prompt = (
            "你是长篇小说章节方案 Critic。比较候选方案，只返回严格 JSON："
            "recommended_option_id:string,recommendation_reason:string,"
            "evaluations:[{option_id:string,causality:integer,character_agency:integer,"
            "surprise:integer,main_plot_value:integer,reason:string,risk:string}]。"
            "每项评分 1-10；优先选择能兑现卷级导演约束、让主角主动付出代价并造成不可逆主线变化的方案。\n\n"
            + json.dumps(payload, ensure_ascii=False)
        )
        try:
            response = self.client.chat.completions.create(
                model=request.model, messages=[{"role": "user", "content": prompt}],
                temperature=0.15, max_tokens=3000,
            )
            result = self._parse_json(response.choices[0].message.content or "")
            valid_ids = {item.candidate_id for item in candidates}
            if str(result.get("recommended_option_id", "")) not in valid_ids:
                result["recommended_option_id"] = candidates[0].candidate_id
            if not isinstance(result.get("evaluations"), list):
                result["evaluations"] = []
            return result
        except Exception:
            return {
                "recommended_option_id": candidates[0].candidate_id,
                "recommendation_reason": "比较器暂不可用，采用第一个有效方案。",
                "evaluations": [],
            }

    @staticmethod
    def _director_defaults() -> dict:
        return {
            "current_volume_goal": "建立本卷的核心推进目标。",
            "protagonist_stage_goal": "明确主角在当前阶段要主动达成的目标。",
            "core_conflict_pressure": "低",
            "recent_major_choice": "",
            "recent_failure_or_cost": "",
            "unredeemed_promises": [],
            "next_turn_distance": 3,
            "foreshadowing_density_risks": [],
            "chapters_without_main_progress": 0,
            "last_chapter": 0,
            "last_review_chapter": 0,
        }

    def _load_director_state(self, book_title: str) -> dict:
        workspace = self.manager.get_workspace(book_title)
        saved = workspace.storage.read_json(
            f"{workspace.agent_root}/story_director.json", default={}
        ) or {}
        state = self._director_defaults()
        if isinstance(saved, dict):
            for key in state:
                if key in saved:
                    state[key] = saved[key]
        return state

    def update_director_state(
        self, request: AgentChapterRequest, approved_plan: AgentChapterPlan,
        chapter_summary: str, model: str,
    ) -> dict:
        """Persist lightweight progress every chapter and refresh the director every 3 chapters."""
        state = self._load_director_state(request.book_title)
        state["last_chapter"] = request.chapter_num
        state["recent_major_choice"] = self._last_scene_value(approved_plan, "choice")
        state["recent_failure_or_cost"] = self._last_scene_value(approved_plan, "cost")
        old_promises = [str(item).strip() for item in state.get("unredeemed_promises", []) if str(item).strip()]
        new_promises = [
            *approved_plan.withheld_reveals,
            *[str(item.get("target", "")).strip() for item in approved_plan.foreshadowing_actions],
        ]
        state["unredeemed_promises"] = list(dict.fromkeys([
            *old_promises, *[item for item in new_promises if item],
        ]))[-12:]
        state["chapters_without_main_progress"] = (
            0 if approved_plan.plot_threads
            else int(state.get("chapters_without_main_progress", 0) or 0) + 1
        )
        state["next_turn_distance"] = max(
            0, int(state.get("next_turn_distance", 3) or 3) - 1
        )
        review_due = (
            request.chapter_num <= 1
            or request.chapter_num - int(state.get("last_review_chapter", 0) or 0) >= 3
        )
        review_error = ""
        if review_due:
            try:
                reviewed = self._call_story_director(
                    state, request, approved_plan, chapter_summary, model
                )
                state.update(reviewed)
                state["last_review_chapter"] = request.chapter_num
                state["next_turn_distance"] = max(
                    0, int(state.get("next_turn_distance", 0) or 0)
                )
            except Exception as exc:
                review_error = str(exc)
        state["updated_at"] = now_iso()
        workspace = self.manager.get_workspace(request.book_title)
        workspace.storage.write_json(
            f"{workspace.agent_root}/story_director.json", state
        )
        return {"state": state, "reviewed": review_due and not review_error, "error": review_error}

    @staticmethod
    def _last_scene_value(plan: AgentChapterPlan, key: str) -> str:
        for scene in reversed(plan.scenes):
            value = str(scene.get(key, "")).strip()
            if value:
                return value
        return ""

    def _call_story_director(
        self, state: dict, request: AgentChapterRequest, plan: AgentChapterPlan,
        chapter_summary: str, model: str,
    ) -> dict:
        payload = {
            "previous_state": state,
            "chapter": {
                "number": request.chapter_num, "title": request.chapter_title,
                "summary": chapter_summary, "plan_goal": plan.chapter_goal,
                "strategy": plan.strategy, "main_threads": plan.plot_threads,
                "choices_and_costs": [
                    {"choice": scene.get("choice", ""), "cost": scene.get("cost", ""),
                     "change": scene.get("irreversible_change", "")}
                    for scene in plan.scenes
                ],
            },
        }
        prompt = (
            "你是长篇小说的卷级导演。根据已完成章节更新跨章节节奏状态；只写可由摘要和计划支持的事实，"
            "不编造正文不存在的事件。只返回严格 JSON：current_volume_goal:string,"
            "protagonist_stage_goal:string,core_conflict_pressure:string,recent_major_choice:string,"
            "recent_failure_or_cost:string,unredeemed_promises:[string],next_turn_distance:integer,"
            "foreshadowing_density_risks:[string],chapters_without_main_progress:integer。\n\n"
            + json.dumps(payload, ensure_ascii=False)
        )
        response = self.client.chat.completions.create(
            model=model, messages=[{"role": "user", "content": prompt}],
            temperature=0.15, max_tokens=2500,
        )
        raw = self._parse_json(response.choices[0].message.content or "")
        defaults = self._director_defaults()
        result = {}
        for key, fallback in defaults.items():
            if key in ("last_chapter", "last_review_chapter"):
                continue
            value = raw.get(key, fallback)
            if isinstance(fallback, list):
                result[key] = [str(item).strip() for item in value if str(item).strip()] if isinstance(value, list) else []
            elif isinstance(fallback, int):
                try:
                    result[key] = max(0, int(value))
                except (TypeError, ValueError):
                    result[key] = fallback
            else:
                result[key] = str(value).strip()
        return result

    def revise_plan(
        self, request: AgentChapterRequest, current_plan: AgentChapterPlan,
        instruction: str,
    ) -> AgentChapterPlan:
        """Apply a targeted change to the current structured plan without replanning candidates."""
        instruction = str(instruction or "").strip()
        if not instruction:
            raise AgentPlanError("请填写具体的章节计划修改要求。")
        bible = self.manager.load_world_bible(request.book_title)
        index = self._world_index(bible, request.book_title)
        history = self._history_candidates(request.book_title)
        skills = self._select_skills(request)
        contract = {
            "chapter_goal": current_plan.chapter_goal,
            "must_happen": current_plan.must_happen,
            "may_happen": current_plan.may_happen,
            "must_not_happen": current_plan.must_not_happen,
            "withheld_reveals": current_plan.withheld_reveals,
            "end_state_requirements": current_plan.end_state_requirements,
            "scenes": current_plan.scenes,
            "character_arcs": current_plan.character_arcs,
            "plot_threads": current_plan.plot_threads,
            "foreshadowing_actions": current_plan.foreshadowing_actions,
            "selected_world_entities": current_plan.selected_world_entities,
            "selected_history_chapters": [
                int(item.get("chapter_num", 0) or 0) for item in current_plan.selected_history
            ],
            "constraints": current_plan.constraints,
            "planning_notes": current_plan.planning_notes,
        }
        prompt = self._plan_revision_prompt(
            request, current_plan, contract, instruction, index, history
        )
        last_error = None
        data = None
        for attempt in range(2):
            try:
                response = self.client.chat.completions.create(
                    model=request.model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.15,
                    max_tokens=8192,
                )
                raw = self._parse_json(response.choices[0].message.content or "")
                data = self._validate_plan(raw, index, history, request.target_words)
                break
            except Exception as exc:
                last_error = exc
                prompt += (
                    "\n\n上一次修改结果无效。请保留未被要求修改的内容，"
                    f"并修复以下格式问题：{exc}。只输出完整合法 JSON。"
                )
        if data is None:
            raise AgentPlanError(f"章节计划修改失败: {last_error}")

        revised = self._build_plan(
            request, data, index, history, skills, current_plan.plan_id,
            current_plan.director_state, current_plan.candidate_id,
            current_plan.strategy, current_plan.strategy_summary,
        )
        revised.recommended_candidate_id = current_plan.recommended_candidate_id
        revised.selection_reason = f"已按用户要求修改当前计划：{instruction}"
        revised.critic = {}
        candidates = []
        source_candidates = current_plan.candidate_plans or [
            current_plan.to_dict(include_candidates=False)
        ]
        for candidate in source_candidates:
            if str(candidate.get("candidate_id", "")) == current_plan.candidate_id:
                candidates.append(revised.to_dict(include_candidates=False))
            else:
                candidates.append(candidate)
        revised.candidate_plans = candidates

        workspace = self.manager.get_workspace(request.book_title)
        path = f"{workspace.agent_root}/chapter_runs/{current_plan.plan_id}.json"
        record = workspace.storage.read_json(path, default={}) or {}
        history_records = list(record.get("plan_revision_history", []) or [])
        history_records.append({
            "instruction": instruction,
            "candidate_id": current_plan.candidate_id,
            "updated_at": now_iso(),
        })
        record.update({
            "schema_version": 2,
            "status": "plan_revised",
            "plan": revised.to_dict(),
            "plan_revision_history": history_records,
            "updated_at": now_iso(),
        })
        workspace.storage.write_json(path, record)
        return revised

    @staticmethod
    def _plan_revision_prompt(
        request: AgentChapterRequest, current_plan: AgentChapterPlan,
        contract: dict, instruction: str, index: list[dict], history: list[dict],
    ) -> str:
        schema = (
            "chapter_goal:string,must_happen:[string],may_happen:[string],"
            "must_not_happen:[string],withheld_reveals:[string],end_state_requirements:[string],"
            "scenes:[{scene_id:string,title:string,purpose:string,pov_character:string,time:string,"
            "location:string,entry_state:string,goal:string,conflict:string,key_actions:[string],"
            "information_released:[string],turning_point:string,choice:string,cost:string,"
            "outcome:string,exit_state:string,irreversible_change:string,target_words:integer,"
            "forbidden:[string]}],character_arcs:[{character:string,start_state:string,"
            "end_state:string,trigger:string,choice:string,cost:string}],plot_threads:[string],"
            "foreshadowing_actions:[{action:string,target:string}],"
            "selected_world_entities:[{id:string,name:string,reason:string}],"
            "selected_history_chapters:[integer],constraints:[string],planning_notes:string"
        )
        payload = {
            "chapter": {
                "number": request.chapter_num,
                "title": request.chapter_title,
                "target_words": request.target_words,
            },
            "strategy": current_plan.strategy,
            "current_plan": contract,
            "modification_instruction": instruction,
            "valid_world_entities": index,
            "valid_history_chapters": history,
            "story_director": current_plan.director_state,
        }
        return (
            "你是章节计划修订编辑。必须基于 current_plan 做最小范围修改，不得重新构思整章，"
            "不得改动用户未要求修改的场景、事实、选择、代价和结束状态。"
            "修改后仍须保持因果完整、人物主动性和卷级导演约束。"
            "只输出修改后的完整 JSON，不输出解释或 Markdown。\n\n"
            f"字段：{schema}\n\n输入：{json.dumps(payload, ensure_ascii=False)}"
        )

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

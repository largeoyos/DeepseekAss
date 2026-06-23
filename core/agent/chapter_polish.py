"""Deterministic Agent-assisted chapter polishing."""
from __future__ import annotations

import json
import re
import uuid
from dataclasses import asdict, dataclass, field

from core.agent.repository import AgentRepository
from core.agent.types import now_iso
from core.agent.skills import HUMANIZER_ZH_STYLE_BRIEF


class AgentPolishError(RuntimeError):
    pass


@dataclass
class AgentPolishRequest:
    book_title: str
    node_id: str
    chapter_num: int
    chapter_title: str
    requirement: str
    model: str
    global_prompt: str = ""


@dataclass
class AgentPolishPlan:
    plan_id: str
    detected_issues: list[dict]
    polish_actions: list[dict]
    preserved_facts: list[str]
    preserved_dialogue_intents: list[str]
    selected_world_entities: list[dict]
    selected_history: list[dict]
    constraints: list[str]
    context_report: dict
    rewrite_required: bool = False
    rewrite_reasons: list[str] = field(default_factory=list)
    selected_skills: list[dict] = field(default_factory=list)
    created_at: str = field(default_factory=now_iso)

    def to_dict(self) -> dict:
        return asdict(self)

    def render(self) -> str:
        lines = ["检测到的问题："]
        lines.extend(f"- {item.get('category', '表达')}：{item.get('description', '')}" for item in self.detected_issues)
        lines.append("\n润色策略：")
        lines.extend(f"- {item.get('target', '')}：{item.get('action', '')}" for item in self.polish_actions)
        if self.preserved_facts:
            lines.extend(["\n必须保留的事实：", *[f"- {item}" for item in self.preserved_facts]])
        if self.preserved_dialogue_intents:
            lines.extend(["\n必须保留的对白意图：", *[f"- {item}" for item in self.preserved_dialogue_intents]])
        if self.selected_world_entities:
            lines.append("\n相关世界书：")
            lines.extend(f"- {item.get('id', '')} | {item.get('name', '')} | {item.get('reason', '')}" for item in self.selected_world_entities)
        if self.selected_history:
            lines.append("\n相邻剧情：")
            lines.extend(f"- 第{item.get('chapter_num', 0)}章「{item.get('title', '')}」：{item.get('reason', '')}" for item in self.selected_history)
        if self.constraints:
            lines.extend(["\n润色约束：", *[f"- {item}" for item in self.constraints]])
        if self.rewrite_required:
            lines.extend(["\n需要改用重写：", *[f"- {item}" for item in self.rewrite_reasons]])
        if self.selected_skills:
            lines.extend(["\n本次使用 Skills：", *[f"- {item.get('name', item.get('id', ''))} v{item.get('version', '1')}：{item.get('reason', '')}" for item in self.selected_skills]])
        if self.context_report.get("preview"):
            lines.extend(["\n上下文统计：", self.context_report["preview"]])
        return "\n".join(lines)


@dataclass
class AgentPolishValidation:
    passed: bool
    content: str
    report: dict
    artifact_id: str = ""


class AgentChapterPolishService:
    """Plan, prompt and fidelity-check an Agent chapter polish run."""

    _REWRITE_PATTERNS = (
        r"(新增|增加|加入|补充).{0,8}(剧情|情节|事件|角色|人物|冲突|伏笔)",
        r"(改变|修改|调整|替换|重构).{0,8}(剧情|结局|事实|时间线|人物关系|角色行为|事件顺序)",
        r"(让|使).{0,12}(死亡|复活|相爱|分手|背叛|获胜|失败|离开|出现)",
    )

    def __init__(self, novel_manager, client, *, skills_enabled: bool = True) -> None:
        self.manager = novel_manager
        self.client = getattr(client, "raw_client", client)
        self.skills_enabled = skills_enabled

    def prepare(self, request: AgentPolishRequest) -> AgentPolishPlan:
        original = self.manager.read_chapter_node(request.book_title, request.node_id) or ""
        if not original.strip():
            raise AgentPolishError("原章节正文为空，无法润色。")

        from core.agent.chapter_generation import AgentChapterGenerationService
        helper = AgentChapterGenerationService(self.manager, self.client)
        bible = self.manager.load_world_bible(request.book_title)
        world_index = helper._world_index(bible, request.book_title)
        history = helper._history_candidates(request.book_title)
        continuity = self.manager.build_continuity_contract(request.book_title, request.chapter_num, request.chapter_title, request.requirement)
        skills = self._select_skills(request)
        payload = {
            "book_title": request.book_title,
            "chapter_num": request.chapter_num,
            "chapter_title": request.chapter_title,
            "polish_requirement": request.requirement,
            "original_chapter": original,
            "continuity_contract": continuity,
            "world_index": world_index,
            "history_index": history,
            "instruction": "只改善表达，不得新增剧情、改变事实、事件顺序、人物动机或对白意图。",
            "skills": skills.text,
        }
        raw = None
        try:
            raw = self._call_json(
                self._planner_prompt(payload), request.model, max_tokens=8192
            )
            data = self._validate_plan(raw, world_index)
        except Exception as exc:
            repair = {
                **payload,
                "invalid_plan": raw or {},
                "validation_error": str(exc),
                "instruction": "修复 invalid_plan，只返回字段和类型完全正确的 JSON。",
            }
            data = self._validate_plan(
                self._call_json(
                    self._planner_prompt(repair), request.model, max_tokens=8192
                ),
                world_index,
            )

        deterministic_reasons = self._rewrite_requirement_reasons(request.requirement)
        rewrite_reasons = list(dict.fromkeys([*[str(item) for item in data.get("rewrite_reasons", []) if str(item).strip()], *deterministic_reasons]))
        selected_ids = [item["id"] for item in data["selected_world_entities"]]
        context = self.manager.context_assembler().assemble_chapter(
            request.book_title, request.chapter_num, request.chapter_title,
            request.requirement + "\n" + original[:2000], global_prompt=request.global_prompt,
            manual_entity_ids=selected_ids, max_recent=2, client=self.client, model=request.model,
        )
        allowed_sources = {"preferences", "recent_summary", "continuity", "world_resident", "world_auto", "manual"}
        sections = [item for item in context.sections if item.source in allowed_sources]
        selected_history = self._resolve_history(request.chapter_num, data.get("selected_history_chapters", []), history)
        history_text = self._history_text(selected_history)
        context_text = "\n\n".join(f"【{item.title}】\n{item.content}" for item in sections if item.content)
        if history_text:
            context_text += ("\n\n" if context_text else "") + history_text
        sources = [asdict(item) for item in sections]
        if history_text:
            sources.append({"source": "agent_history", "title": "相邻章节摘要", "content": history_text, "reason": "润色连续性保护", "omitted_chars": 0})
        injected = sum(len(item.content) for item in sections) + len(history_text)
        omitted = sum(int(item.omitted_chars) for item in context.sections)
        preview = "\n".join(f"- {item['title']}: {len(item.get('content', ''))} 字；来源={item['source']}；原因={item.get('reason', '')}；省略={item.get('omitted_chars', 0)}" for item in sources)
        report = {"preview": preview, "content": context_text, "candidate_chars": injected + omitted, "injected_chars": injected, "omitted_chars": omitted, "sources": sources, "skills": skills.summaries, "skills_text": skills.text}
        plan = AgentPolishPlan(
            plan_id=f"polish_plan_{uuid.uuid4().hex}", detected_issues=data["detected_issues"],
            polish_actions=data["polish_actions"], preserved_facts=data["preserved_facts"],
            preserved_dialogue_intents=data["preserved_dialogue_intents"],
            selected_world_entities=data["selected_world_entities"], selected_history=selected_history,
            constraints=data["constraints"], context_report=report,
            rewrite_required=bool(data.get("rewrite_required")) or bool(rewrite_reasons), rewrite_reasons=rewrite_reasons,
            selected_skills=skills.summaries,
        )
        self._write_run(request.book_title, plan.plan_id, {"schema_version": 1, "run_id": plan.plan_id, "operation": "chapter_polish", "status": "prepared", "request": asdict(request), "plan": plan.to_dict(), "created_at": now_iso()})
        return plan

    def build_prompt(self, request: AgentPolishRequest, plan: AgentPolishPlan) -> tuple[str, str]:
        original = self.manager.read_chapter_node(request.book_title, request.node_id) or ""
        if plan.rewrite_required:
            raise AgentPolishError("该要求涉及剧情或事实修改，请使用“重写”。")
        prompt = "\n\n".join([
            "请按已确认方案润色章节全文。只输出润色后的完整正文，不要解释、标题、摘要或修改说明。",
            f"【润色要求】\n{request.requirement}", f"【已确认润色方案】\n{plan.render()}",
            f"【连续性保护上下文】\n{plan.context_report.get('content', '')}",
            f"【本次启用 Skills】\n{plan.context_report.get('skills_text', '')}" if plan.context_report.get("skills_text") else "",
            f"【去 AI 腔风格约束】\n{HUMANIZER_ZH_STYLE_BRIEF}",
            "【硬性边界】\n不得新增、删除或调换剧情事件；不得改变人物行为、动机、关系、时间线、地点、能力、关键事实和对白意图；不得把作者规划写成已发生事实。",
            f"【原章节全文】\n{original}",
        ])
        self._update_run(request.book_title, plan.plan_id, status="approved", prompt_chars=len(prompt), updated_at=now_iso())
        return prompt, original

    def validate_and_repair(self, request: AgentPolishRequest, plan: AgentPolishPlan, original: str, candidate: str) -> AgentPolishValidation:
        report = self._audit(request, plan, original, candidate)
        final = candidate
        if not report["passed"]:
            final = self._repair(request, plan, original, candidate, report)
            report = self._audit(request, plan, original, final)
            report["repair_rounds"] = 1
        else:
            report["repair_rounds"] = 0
        if report["passed"]:
            self._update_run(request.book_title, plan.plan_id, status="validated", fidelity_report=report, updated_at=now_iso())
            return AgentPolishValidation(True, final, report)
        repository = AgentRepository(self.manager.get_workspace(request.book_title))
        artifact_id = repository.save_artifact(plan.plan_id, "failed_chapter_polish", final, {"fidelity_report": report, "chapter_num": request.chapter_num})
        self._update_run(request.book_title, plan.plan_id, status="fidelity_failed", fidelity_report=report, artifact_id=artifact_id, updated_at=now_iso())
        return AgentPolishValidation(False, final, report, artifact_id)

    def mark_cancelled(self, request: AgentPolishRequest, plan: AgentPolishPlan) -> None:
        self._update_run(request.book_title, plan.plan_id, status="cancelled", updated_at=now_iso())

    def mark_completed(self, request: AgentPolishRequest, plan: AgentPolishPlan, version: int, snapshot_id: str = "") -> None:
        self._update_run(request.book_title, plan.plan_id, status="completed", version=version, snapshot_id=snapshot_id, updated_at=now_iso())

    def _select_skills(self, request: AgentPolishRequest):
        from core.agent.repository import AgentRepository
        from core.agent.skills import SkillSelection, SkillService
        if not self.skills_enabled:
            return SkillSelection()
        repository = AgentRepository(self.manager.get_workspace(request.book_title))
        return SkillService(repository).select_for_task(
            "chapter_polish", "writing_orchestrator",
            "\n".join([request.chapter_title, request.requirement, request.global_prompt]),
        )
    def _audit(self, request, plan, original, candidate) -> dict:
        payload = {"requirement": request.requirement, "preserved_facts": plan.preserved_facts, "preserved_dialogue_intents": plan.preserved_dialogue_intents, "skills": plan.context_report.get("skills_text", ""), "original": original, "polished": candidate}
        prompt = "你是小说润色保真审稿器。比较原文和润色稿，只返回严格 JSON：passed:boolean, plot_drift:[string], fact_drift:[string], character_drift:[string], dialogue_intent_drift:[string], new_facts:[string], requirement_issues:[string], format_issues:[string], style_issues:[string], repair_instruction:string。任何剧情事件、事实、人物行为/动机、时间线、关系或对白意图改变都必须令 passed=false；若出现明显 AI 腔、否定式排比或描写重复，写入 style_issues。\n\n" + json.dumps(payload, ensure_ascii=False)
        data = self._call_json(prompt, request.model, max_tokens=8192)
        fields = ("plot_drift", "fact_drift", "character_drift", "dialogue_intent_drift", "new_facts", "requirement_issues", "format_issues", "style_issues")
        for key in fields:
            if not isinstance(data.get(key), list):
                data[key] = [str(data.get(key))] if data.get(key) else []
        data["passed"] = bool(data.get("passed")) and not any(data[key] for key in fields)
        data["repair_instruction"] = str(data.get("repair_instruction", ""))
        return data

    def _repair(self, request, plan, original, candidate, report) -> str:
        prompt = "\n\n".join(["你是小说润色修复编辑。根据审查报告做最小修复，只输出完整正文。", "必须恢复原文剧情、事实、人物行为、事件顺序和对白意图；不得新增内容。", f"【去 AI 腔风格约束】\n{HUMANIZER_ZH_STYLE_BRIEF}", f"【润色要求】\n{request.requirement}", f"【保留事实】\n{json.dumps(plan.preserved_facts, ensure_ascii=False)}", f"【审查报告】\n{json.dumps(report, ensure_ascii=False)}", f"【本次启用 Skills】\n{plan.context_report.get('skills_text', '')}", f"【原文】\n{original}", f"【待修复润色稿】\n{candidate}"])
        response = self.client.chat.completions.create(model=request.model, messages=[{"role": "user", "content": prompt}], temperature=0.2, max_tokens=32768)
        return str(response.choices[0].message.content or "").strip()

    def _call_json(self, prompt: str, model: str, *, max_tokens: int) -> dict:
        response = self.client.chat.completions.create(model=model, messages=[{"role": "user", "content": prompt}], temperature=0.1, max_tokens=max_tokens)
        return self._parse_json(response.choices[0].message.content or "")

    @staticmethod
    def _planner_prompt(payload: dict) -> str:
        return "你是小说章节润色规划 Agent。只分析表达层问题，不得设计新剧情。只返回严格 JSON：detected_issues:[{category,description,evidence}], polish_actions:[{target,action}], preserved_facts:[string], preserved_dialogue_intents:[string], selected_world_entities:[{id,name,reason}], selected_history_chapters:[integer], constraints:[string], rewrite_required:boolean, rewrite_reasons:[string]。若用户要求新增/删除/改变剧情事实、事件顺序、人物行为、动机、关系、时间线或结局，rewrite_required 必须为 true。\n\n输入：" + json.dumps(payload, ensure_ascii=False)

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
            raise AgentPolishError("Agent 润色规划不是 JSON 对象。")
        return data

    @staticmethod
    def _validate_plan(data: dict, world_index: list[dict]) -> dict:
        fields = ("detected_issues", "polish_actions", "preserved_facts", "preserved_dialogue_intents", "selected_world_entities", "selected_history_chapters", "constraints", "rewrite_reasons")
        for key in fields:
            if not isinstance(data.get(key), list):
                raise AgentPolishError(f"Agent 润色规划字段 {key} 必须是数组。")
        if not all(isinstance(item, dict) for item in data["detected_issues"]):
            raise AgentPolishError("detected_issues 包含无效项。")
        if not all(isinstance(item, dict) for item in data["polish_actions"]):
            raise AgentPolishError("polish_actions 包含无效项。")
        valid = {item["id"]: item for item in world_index}
        selected = []
        for item in data["selected_world_entities"]:
            entity_id = str(item.get("id", "")) if isinstance(item, dict) else ""
            if entity_id in valid:
                entity = valid[entity_id]
                selected.append({"id": entity_id, "name": entity["name"], "kind": entity["kind"], "reason": str(item.get("reason", "润色连续性保护"))[:300]})
        data["selected_world_entities"] = selected
        for key in ("preserved_facts", "preserved_dialogue_intents", "constraints"):
            data[key] = [str(item) for item in data[key] if str(item).strip()]
        data["rewrite_required"] = bool(data.get("rewrite_required"))
        return data

    def _rewrite_requirement_reasons(self, requirement: str) -> list[str]:
        reasons = []
        for pattern in self._REWRITE_PATTERNS:
            for match in re.finditer(pattern, str(requirement or "")):
                prefix = requirement[max(0, match.start() - 3):match.start()]
                if any(word in prefix for word in ("不要", "不得", "不能", "避免", "禁止")):
                    continue
                reasons.append(f"润色要求包含剧情/事实修改指令：{match.group(0)}")
        return reasons

    @staticmethod
    def _resolve_history(chapter_num: int, selected: list[int], history: list[dict]) -> list[dict]:
        by_num = {int(item["chapter_num"]): item for item in history}
        adjacent = [chapter_num - 1, chapter_num + 1]
        requested = [int(item) for item in selected if str(item).isdigit() and int(item) != chapter_num]
        return [{**by_num[number], "reason": "相邻章节" if number in adjacent else "Agent 语义命中"} for number in dict.fromkeys([*adjacent, *requested]) if number in by_num]

    @staticmethod
    def _history_text(history: list[dict]) -> str:
        if not history:
            return ""
        return "【相邻章节摘要】\n" + "\n".join(f"第{item['chapter_num']}章「{item.get('title', '')}」：{item.get('summary', '')}" for item in history)

    def _run_path(self, book_title: str, run_id: str) -> str:
        workspace = self.manager.get_workspace(book_title)
        return f"{workspace.agent_root}/chapter_polish_runs/{run_id}.json"

    def _write_run(self, book_title: str, run_id: str, data: dict) -> None:
        workspace = self.manager.get_workspace(book_title)
        workspace.storage.write_json(self._run_path(book_title, run_id), data)

    def _update_run(self, book_title: str, run_id: str, **updates) -> None:
        workspace = self.manager.get_workspace(book_title)
        path = self._run_path(book_title, run_id)
        data = workspace.storage.read_json(path, default={}) or {}
        data.update(updates)
        workspace.storage.write_json(path, data)

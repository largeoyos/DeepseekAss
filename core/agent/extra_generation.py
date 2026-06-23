from __future__ import annotations

import json
import re
import uuid
from dataclasses import asdict, dataclass, field

from core.agent.types import now_iso
from core.agent.skills import HUMANIZER_ZH_STYLE_BRIEF


@dataclass
class AgentExtraRequest:
    book_title: str
    extra_type: str
    start_node_id: str = ""
    end_node_id: str = ""
    reference_node_id: str = ""
    title: str = ""
    plot: str = ""
    requirement: str = ""
    target_words: int = 5000
    model: str = ""
    manual_entity_ids: list[str] = field(default_factory=list)
    global_prompt: str = ""


@dataclass
class AgentExtraPlan:
    plan_id: str
    extra_type: str
    chapter_goal: str
    scenes: list[dict]
    character_states: list[dict]
    foreshadowing_actions: list[dict]
    selected_world_entities: list[dict]
    selected_history: list[dict]
    constraints: list[str]
    insertion_report: dict
    context_report: dict
    selected_skills: list[dict] = field(default_factory=list)
    created_at: str = field(default_factory=now_iso)

    def to_dict(self) -> dict:
        return asdict(self)

    def render(self) -> str:
        labels = {"enrichment": "丰富内容", "if_line": "IF线", "prequel": "前传", "sequel": "后传"}
        lines = [f"番外类型：{labels.get(self.extra_type, self.extra_type)}", f"目标：{self.chapter_goal}"]
        lines.append(f"结构：{self.insertion_report.get('description', '')}")
        lines.append("\n场景规划：")
        for index, item in enumerate(self.scenes, 1):
            lines.append(f"{index}. {item.get('title', '场景')}：{item.get('purpose', '')}；结果={item.get('outcome', '')}")
        if self.character_states:
            lines.append("\n角色状态：")
            lines.extend(f"- {item.get('character', '')}：{item.get('start_state', '')} → {item.get('end_state', '')}" for item in self.character_states)
        if self.selected_world_entities:
            lines.append("\n世界书：")
            lines.extend(f"- {item.get('name', '')} ({item.get('id', '')})：{item.get('reason', '')}" for item in self.selected_world_entities)
        if self.selected_history:
            lines.append("\n历史剧情：")
            lines.extend(f"- {item.get('label', '')}：{item.get('reason', '')}" for item in self.selected_history)
        if self.constraints:
            lines.append("\n硬约束：")
            lines.extend(f"- {item}" for item in self.constraints)
        lines.append("\n上下文统计：")
        lines.append(str(self.context_report.get("preview", "")))
        return "\n".join(lines)


@dataclass
class AgentExtraResult:
    plan_id: str
    prompt: str
    context_report: dict


class AgentExtraGenerationService:
    def __init__(self, novel_manager, client, *, skills_enabled: bool = True) -> None:
        self.manager = novel_manager
        self.client = getattr(client, "raw_client", client)
        self.skills_enabled = skills_enabled

    def prepare(self, request: AgentExtraRequest) -> AgentExtraPlan:
        self._validate_request(request)
        meta = self.manager.load_meta(request.book_title)
        anchor_id = request.start_node_id or request.reference_node_id
        path = self.manager.get_path_to_node(request.book_title, anchor_id)
        start = self._node(request.book_title, request.start_node_id)
        end = self._node(request.book_title, request.end_node_id)
        reference = self._node(request.book_title, request.reference_node_id)
        world_index, world_data = self._world_index(request.book_title)
        history = self._history(path)
        skills = self._select_skills(request)
        payload = {
            "extra_type": request.extra_type,
            "title": request.title,
            "plot": request.plot,
            "requirement": request.requirement,
            "target_words": request.target_words,
            "protagonist": meta.protagonist_bio,
            "world_background": meta.background_story,
            "author_plan": meta.author_plan,
            "writing_demand": meta.writing_demand,
            "start_node": self._node_summary(start),
            "end_node": self._node_summary(end),
            "reference_node": self._node_summary(reference),
            "history_index": history,
            "world_index": world_index,
            "skills": skills.text,
            "type_contract": self._type_contract(request.extra_type),
        }
        data = self._call_planner(payload, request.model)
        data = self._validate_plan(data, world_index, history)
        selected_ids = list(dict.fromkeys([*request.manual_entity_ids, *[item["id"] for item in data["selected_world_entities"]]]))
        selected_world = [world_data[item] for item in selected_ids if item in world_data]
        selected_history = self._resolve_history(data.get("selected_history_node_ids", []), history)
        context_parts = [
            f"【主角设定】\n{meta.protagonist_bio}",
            f"【世界观】\n{meta.background_story}",
            f"【作者规划】\n{meta.author_plan}",
            self._boundary_context(request, start, end, reference, path),
            "【Agent 精选世界书】\n" + "\n".join(json.dumps(item, ensure_ascii=False) for item in selected_world),
            "【Agent 精选历史】\n" + "\n".join(f"{item['label']}：{item['summary']}" for item in selected_history),
        ]
        content = "\n\n".join(item for item in context_parts if item.strip())
        candidate_chars = sum(len(json.dumps(item, ensure_ascii=False)) for item in world_data.values()) + sum(len(item.get("summary", "")) for item in history)
        report = {
            "content": content,
            "candidate_chars": candidate_chars,
            "injected_chars": len(content),
            "omitted_chars": max(0, candidate_chars - len(content)),
            "preview": f"候选 {candidate_chars} 字；实际注入 {len(content)} 字；省略 {max(0, candidate_chars - len(content))} 字",
            "sources": [
                {"source": "world_bible", "id": item.get("id", ""), "reason": "Agent 选择"} for item in selected_world
            ] + [
                {"source": "chapter_history", "node_id": item.get("node_id", ""), "reason": item.get("reason", "")} for item in selected_history
            ],
            "skills": skills.summaries,
            "skills_text": skills.text,
        }
        plan = AgentExtraPlan(
            plan_id=f"extra_plan_{uuid.uuid4().hex}",
            extra_type=request.extra_type,
            chapter_goal=data["chapter_goal"],
            scenes=data["scenes"],
            character_states=data["character_states"],
            foreshadowing_actions=data["foreshadowing_actions"],
            selected_world_entities=data["selected_world_entities"],
            selected_history=selected_history,
            constraints=data["constraints"],
            insertion_report=self._insertion_report(request, start, end, reference),
            context_report=report,
            selected_skills=skills.summaries,
        )
        workspace = self.manager.get_workspace(request.book_title)
        workspace.storage.write_json(
            f"{workspace.agent_root}/extra_runs/{plan.plan_id}.json",
            {"schema_version": 1, "status": "prepared", "request": asdict(request), "plan": plan.to_dict(), "created_at": now_iso()},
        )
        return plan

    def generate(self, request: AgentExtraRequest, plan: AgentExtraPlan) -> AgentExtraResult:
        labels = {"enrichment": "丰富内容番外", "if_line": "IF线番外", "prequel": "前传", "sequel": "后传"}
        prompt = "\n\n".join(filter(None, [
            f"【已确认的{labels.get(request.extra_type, '番外')}计划】\n{plan.render()}",
            f"【实际注入上下文】\n{plan.context_report.get('content', '')}",
            f"【风格硬约束】\n{HUMANIZER_ZH_STYLE_BRIEF}",
            f"【用户剧情】\n{request.plot}",
            f"【写作要求】\n{request.requirement}",
            f"请创作「{request.title}」，不少于 {request.target_words} 字。只输出小说正文，不输出解释、标题或计划。",
        ]))
        workspace = self.manager.get_workspace(request.book_title)
        record = workspace.storage.read_json(f"{workspace.agent_root}/extra_runs/{plan.plan_id}.json", default={}) or {}
        record.update({"status": "approved", "prompt_chars": len(prompt), "updated_at": now_iso()})
        workspace.storage.write_json(f"{workspace.agent_root}/extra_runs/{plan.plan_id}.json", record)
        return AgentExtraResult(plan.plan_id, prompt, plan.context_report)

    def mark_completed(self, book_title: str, plan_id: str, node_id: str, snapshot_id: str = "") -> None:
        workspace = self.manager.get_workspace(book_title)
        path = f"{workspace.agent_root}/extra_runs/{plan_id}.json"
        record = workspace.storage.read_json(path, default={}) or {}
        record.update({"status": "completed", "node_id": node_id, "snapshot_id": snapshot_id, "updated_at": now_iso()})
        workspace.storage.write_json(path, record)

    def _validate_request(self, request: AgentExtraRequest) -> None:
        if request.extra_type in {"enrichment", "if_line"}:
            if not self.manager.are_direct_path_neighbors(request.book_title, request.start_node_id, request.end_node_id):
                raise ValueError("丰富内容和 IF 线必须选择同一路径中连续两个节点")
        elif request.extra_type in {"prequel", "sequel"}:
            if not self._node(request.book_title, request.reference_node_id):
                raise ValueError("前传或后传必须选择参考节点")
        else:
            raise ValueError("不支持的番外类型")

    def _node(self, title: str, node_id: str) -> dict:
        if not node_id:
            return {}
        return dict(self.manager.ensure_chapter_tree(title).chapter_nodes.get(node_id) or {})

    def _node_summary(self, node: dict) -> dict:
        if not node:
            return {}
        return {key: node.get(key) for key in ("id", "title", "summary", "display_label", "chapter_num", "version", "node_kind")}

    def _history(self, path: list[dict]) -> list[dict]:
        result = []
        for item in path:
            result.append({
                "node_id": item.get("id", ""),
                "label": item.get("display_label") or f"第{item.get('chapter_num', 0)}章",
                "title": item.get("title", ""),
                "summary": str(item.get("summary", ""))[:1200],
            })
        return result

    def _world_index(self, title: str) -> tuple[list[dict], dict[str, dict]]:
        from core.context_assembler import _world_entities
        bible = self.manager.load_world_bible(title)
        index, full = [], {}
        for entity_id, kind, name, item in _world_entities(bible):
            data = asdict(item) if hasattr(item, "__dataclass_fields__") else dict(item)
            full[entity_id] = {"id": entity_id, "kind": kind, "name": name, "data": data}
            index.append({"id": entity_id, "kind": kind, "name": name, "brief": str(data.get("description") or data.get("traits") or data.get("content") or data.get("hint") or "")[:300]})
        return index, full

    def _boundary_context(self, request, start, end, reference, path) -> str:
        if request.extra_type in {"enrichment", "if_line"}:
            start_content = self.manager.read_chapter_node(request.book_title, start.get("id", "")) or ""
            end_content = self.manager.read_chapter_node(request.book_title, end.get("id", "")) or ""
            end_label = "后续连续性边界" if request.extra_type == "enrichment" else "原路线对照（不得视为 IF 已发生事实）"
            return f"【起点正文结尾】\n{start_content[-5000:]}\n\n【{end_label}】\n{end_content[:5000]}"
        reference_content = self.manager.read_chapter_node(request.book_title, reference.get("id", "")) or ""
        if request.extra_type == "prequel":
            return f"【前传时代边界】\n参考节点及未来剧情只能用于避免矛盾，不得成为角色已知事实。\n{reference_content[:4000]}"
        return f"【后传历史边界】\n参考节点及其祖先路径均为已发生历史。\n{reference_content[-5000:]}"

    def _type_contract(self, extra_type: str) -> str:
        return {
            "enrichment": "填补连续节点之间的过程，结尾必须自然衔接终点节点，不改变终点既有事实。",
            "if_line": "从起点作出不同选择，终点节点仅是原路线对照，IF 线不得强行回归原路线。",
            "prequel": "发生在参考节点之前，不得让角色预知未来。",
            "sequel": "发生在参考节点之后，把参考路径视为历史。",
        }[extra_type]

    def _insertion_report(self, request, start, end, reference) -> dict:
        if request.extra_type == "enrichment":
            description = f"{start.get('id')} → 新番外 → {end.get('id')}"
        elif request.extra_type == "if_line":
            description = f"从 {start.get('id')} 分叉；{end.get('id')} 仅作原路线对照"
        else:
            description = f"创建独立{request.extra_type}树；参考节点 {reference.get('id')}"
        return {"description": description, "start_node_id": request.start_node_id, "end_node_id": request.end_node_id, "reference_node_id": request.reference_node_id}

    def _select_skills(self, request):
        from core.agent.repository import AgentRepository
        from core.agent.skills import SkillSelection, SkillService
        if not self.skills_enabled:
            return SkillSelection()
        return SkillService(AgentRepository(self.manager.get_workspace(request.book_title))).select_for_task(
            "extra_generation", "writing_orchestrator", "\n".join([request.title, request.plot, request.requirement, request.extra_type])
        )

    def _call_planner(self, payload: dict, model: str) -> dict:
        prompt = (
            "你是小说番外规划 Agent。严格遵守 type_contract，选择必要世界书实体和历史节点。"
            "只输出 JSON：{chapter_goal:string,scenes:[{title,purpose,conflict,outcome}],"
            "character_states:[{character,start_state,end_state}],foreshadowing_actions:[{action,target}],"
            "selected_world_entities:[{id,name,reason}],selected_history_node_ids:[string],constraints:[string]}。"
            "至少一个场景，不得输出 Markdown。\n\n输入：" + json.dumps(payload, ensure_ascii=False)
        )
        last_error = None
        for _attempt in range(2):
            try:
                response = self.client.chat.completions.create(model=model, messages=[{"role": "user", "content": prompt}], temperature=0.2, max_tokens=8192)
                return self._parse_json(response.choices[0].message.content or "")
            except Exception as exc:
                last_error = exc
                prompt += "\n上次输出无效，只输出合法 JSON。"
        raise RuntimeError(f"番外规划失败: {last_error}")

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
            raise ValueError("番外规划不是 JSON 对象")
        return data

    def _validate_plan(self, data: dict, world_index: list[dict], history: list[dict]) -> dict:
        if not isinstance(data.get("chapter_goal"), str) or not data.get("chapter_goal", "").strip():
            raise ValueError("番外规划缺少目标")
        for key in ("scenes", "character_states", "foreshadowing_actions", "selected_world_entities", "selected_history_node_ids", "constraints"):
            if not isinstance(data.get(key), list):
                raise ValueError(f"番外规划字段 {key} 必须是数组")
        if not data["scenes"]:
            raise ValueError("番外规划至少需要一个场景")
        valid_world = {item["id"]: item for item in world_index}
        data["selected_world_entities"] = [
            {"id": str(item.get("id")), "name": valid_world[str(item.get("id"))]["name"], "reason": str(item.get("reason", "Agent 命中"))[:300]}
            for item in data["selected_world_entities"]
            if isinstance(item, dict) and str(item.get("id")) in valid_world
        ]
        valid_history = {item["node_id"] for item in history}
        data["selected_history_node_ids"] = [str(item) for item in data["selected_history_node_ids"] if str(item) in valid_history]
        return data

    def _resolve_history(self, selected: list[str], history: list[dict]) -> list[dict]:
        by_id = {item["node_id"]: item for item in history}
        recent = [item["node_id"] for item in history[-3:]]
        ids = list(dict.fromkeys([*recent, *selected]))
        return [{**by_id[item], "reason": "近期承接" if item in recent else "Agent 选择"} for item in ids if item in by_id]

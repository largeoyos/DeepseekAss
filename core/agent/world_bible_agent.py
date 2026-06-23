from __future__ import annotations

import json
import re
import uuid
from dataclasses import asdict, dataclass, field

from core.agent.changes import ChangeSetService
from core.agent.repository import AgentRepository
from core.agent.skills import SkillSelection, SkillService
from core.agent.types import now_iso
from core.agent.world_maintenance import WorldBibleMaintenanceService, WorldMaintenanceResult


@dataclass
class WorldDetailRequest:
    book_title: str
    text: str
    model: str
    source_run_id: str = ""
    global_prompt: str = ""


@dataclass
class WorldChangePlan:
    run_id: str
    summary: str
    operations: list[dict]
    conflicts: list[dict]
    change_set_id: str
    selected_skills: list[dict] = field(default_factory=list)
    approval_required: bool = True


class WorldBibleAgentService:
    """World-bible Agent: chapter maintenance is safe/automatic; user details require approval."""

    ALLOWED_ACTIONS = {"entity.create", "entity.patch", "entity.supersede", "entity.archive", "entity.merge"}
    ALLOWED_TYPES = {"character", "location", "timeline", "plot_thread", "world_rule", "foreshadowing"}

    def __init__(self, novel_manager, client=None, *, skills_enabled: bool = True) -> None:
        self.manager = novel_manager
        self.client = getattr(client, "raw_client", client)
        self.skills_enabled = skills_enabled

    def analyze_chapter(self, client, book_title: str, chapter_num: int, version: int, *, model: str, global_user_prompt: str = "", xp_mode: bool = False, plan: dict | None = None) -> WorldMaintenanceResult:
        result = WorldBibleMaintenanceService(self.manager).maintain(
            client, book_title, chapter_num, version,
            model=model, global_user_prompt=global_user_prompt, xp_mode=xp_mode, plan=plan,
        )
        workspace = self.manager.get_workspace(book_title)
        path = f"{workspace.agent_root}/maintenance/reports/{result.task_id}.json"
        record = workspace.storage.read_json(path, default={}) or {}
        record.update({
            "schema_version": 1,
            "agent_kind": "world_bible_manager",
            "run_id": result.task_id,
            "approval_status": "automatic_safe_change" if result.status == "completed" else "pending_retry",
            "updated_at": now_iso(),
        })
        workspace.storage.write_json(path, record)
        return result

    def analyze_user_details(self, request: WorldDetailRequest) -> WorldChangePlan:
        if not request.text.strip():
            raise ValueError("补充细节不能为空")
        if self.client is None:
            raise RuntimeError("世界书管理 Agent 未配置模型客户端")
        workspace = self.manager.get_workspace(request.book_title)
        manifest = workspace.ensure_manifest()
        repository = AgentRepository(workspace)
        run_id = f"world_detail_{uuid.uuid4().hex}"
        skills = self._select_skills(repository, request)
        index = self._world_index(request.book_title)
        active_nodes = self.manager.get_active_path_nodes(request.book_title)
        payload = {
            "user_details": request.text,
            "world_index": index,
            "active_path": [
                {
                    "node_id": item.get("id", ""),
                    "chapter_num": item.get("chapter_num", 0),
                    "version": item.get("version", 0),
                    "title": item.get("title", ""),
                    "summary": str(item.get("summary", ""))[:500],
                }
                for item in active_nodes
            ],
            "global_prompt": request.global_prompt,
            "skills": skills.text,
        }
        raw = self._call(payload, request.model)
        operations, conflicts = self._validate(raw, index, active_nodes)
        change_set = ChangeSetService(self.manager, request.book_title, repository).propose_world_patch(
            run_id,
            manifest.book_id,
            operations,
            reason="将用户或写作顾问补充的细节加入世界书",
        )
        summary = str(raw.get("summary") or f"拟执行 {len(operations)} 项世界书字段变更")
        record = {
            "schema_version": 1,
            "run_id": run_id,
            "agent_kind": "world_bible_manager",
            "source_run_id": request.source_run_id,
            "source_text": request.text,
            "summary": summary,
            "operations": operations,
            "conflicts": conflicts,
            "selected_skills": skills.summaries,
            "change_set_id": change_set.change_set_id,
            "approval_status": "pending",
            "created_at": now_iso(),
        }
        workspace.storage.write_json(f"{workspace.agent_root}/world_detail_runs/{run_id}.json", record)
        return WorldChangePlan(run_id, summary, operations, conflicts, change_set.change_set_id, skills.summaries)

    def confirm_scopes(self, book_title: str, change_set_id: str, operations: list[dict]) -> None:
        workspace = self.manager.get_workspace(book_title)
        repository = AgentRepository(workspace)
        change_set = repository.load_change_set(change_set_id)
        if change_set is None or change_set.status != "pending":
            raise ValueError("待审批世界书变更不存在")
        valid_scopes = {"chapter", "branch", "global"}
        active_ids = {str(item.get("id", "")) for item in self.manager.get_active_path_nodes(book_title)}
        for item in operations:
            scope = str(item.get("scope", ""))
            anchor = str(item.get("anchor_node_id", "") or "")
            if scope not in valid_scopes:
                raise ValueError("所有世界书变更都必须先确认作用域")
            if scope in {"chapter", "branch"} and anchor not in active_ids:
                raise ValueError(f"作用域锚点不在当前活跃路径: {anchor}")
            if scope == "global":
                item["anchor_node_id"] = ""
        target = next((item for item in change_set.operations if item.operation == "world_bible.patch"), None)
        if target is None:
            raise ValueError("ChangeSet 中不存在世界书字段变更")
        target.payload["operations"] = operations
        change_set.validation_result = {
            **dict(change_set.validation_result or {}),
            "scopes_confirmed": True,
            "scope_summary": {
                scope: sum(1 for item in operations if item.get("scope") == scope)
                for scope in sorted(valid_scopes)
            },
        }
        repository.save_change_set(change_set)
        run_path = f"{workspace.agent_root}/world_detail_runs/{change_set.run_id}.json"
        run_record = workspace.storage.read_json(run_path, default={}) or {}
        if isinstance(run_record, dict):
            run_record.update({
                "operations": operations,
                "approval_status": "scope_confirmed",
                "updated_at": now_iso(),
            })
            workspace.storage.write_json(run_path, run_record)

    def approve(self, book_title: str, change_set_id: str):
        workspace = self.manager.get_workspace(book_title)
        repository = AgentRepository(workspace)
        result = ChangeSetService(self.manager, book_title, repository).approve(change_set_id)
        self._update_run_status(workspace, result.run_id, "approved", result.validation_result)
        return result

    def reject(self, book_title: str, change_set_id: str):
        workspace = self.manager.get_workspace(book_title)
        repository = AgentRepository(workspace)
        result = ChangeSetService(self.manager, book_title, repository).reject(change_set_id)
        self._update_run_status(workspace, result.run_id, "rejected", result.validation_result)
        return result

    @staticmethod
    def _update_run_status(workspace, run_id: str, status: str, validation: dict | None = None) -> None:
        path = f"{workspace.agent_root}/world_detail_runs/{run_id}.json"
        record = workspace.storage.read_json(path, default={}) or {}
        if not isinstance(record, dict):
            return
        record.update({
            "approval_status": status,
            "validation_result": validation or {},
            "updated_at": now_iso(),
        })
        workspace.storage.write_json(path, record)

    def _select_skills(self, repository: AgentRepository, request: WorldDetailRequest) -> SkillSelection:
        if not self.skills_enabled:
            return SkillSelection()
        return SkillService(repository).select_for_task(
            "world_bible_management", "world_bible_manager", request.text
        )

    def _call(self, payload: dict, model: str) -> dict:
        prompt = (
            "你是受控的小说世界书管理 Agent。根据用户明确补充的细节、现有世界书索引和当前活跃章节路径，"
            "提出字段级变更，但不得直接写入。必须判断每项事实的生效范围："
            "chapter 表示只属于某个章节版本；branch 表示从锚点章节开始只在该剧情分支生效；"
            "global 表示不依赖剧情分支的全书稳定设定；无法可靠判断时使用 uncertain。"
            "只输出 JSON："
            "{summary:string, operations:[{operation,entity_type,entity_id,payload,reason,risk,scope,anchor_node_id,scope_reason,source_ids}], conflicts:[{message,entity_id}]}。"
            "operation 只能是 entity.create/entity.patch/entity.supersede/entity.archive/entity.merge；"
            "entity_type 只能是 character/location/timeline/plot_thread/world_rule/foreshadowing。"
            "chapter、branch 的 anchor_node_id 必须来自 active_path；global 的 anchor_node_id 留空。"
            "修改已有实体必须使用索引中的 ID；新实体 ID 使用小写英文、数字和下划线组成的稳定 ID。"
            "来自用户补充的信息均需审批，不要声称已经修改世界书。不得输出 Markdown。\n\n输入："
            + json.dumps(payload, ensure_ascii=False)
        )
        last_error = None
        for _attempt in range(2):
            try:
                response = self.client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.1,
                    max_tokens=8192,
                )
                return self._parse_json(response.choices[0].message.content or "")
            except Exception as exc:
                last_error = exc
                prompt += "\n上次输出无效。请只输出字段和类型完全正确的 JSON。"
        raise RuntimeError(f"世界书管理 Agent 分析失败: {last_error}")

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
            raise ValueError("世界书变更计划不是 JSON 对象")
        return data

    def _validate(self, data: dict, index: list[dict], active_nodes: list[dict]) -> tuple[list[dict], list[dict]]:
        raw_operations = data.get("operations")
        if not isinstance(raw_operations, list):
            raise ValueError("世界书变更缺少 operations 数组")
        existing = {str(item["id"]): item for item in index}
        active_ids = {str(item.get("id", "")) for item in active_nodes}
        active_tip = str(active_nodes[-1].get("id", "")) if active_nodes else ""
        operations = []
        for item in raw_operations:
            if not isinstance(item, dict):
                continue
            action = str(item.get("operation", ""))
            kind = str(item.get("entity_type", ""))
            entity_id = str(item.get("entity_id", "")).strip()
            payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
            if action not in self.ALLOWED_ACTIONS or kind not in self.ALLOWED_TYPES:
                continue
            if not entity_id or not re.fullmatch(r"[A-Za-z0-9_.:-]{2,120}", entity_id):
                continue
            if action != "entity.create" and entity_id not in existing:
                continue
            scope = str(item.get("scope", "uncertain") or "uncertain").strip().lower()
            if scope not in {"chapter", "branch", "global", "uncertain"}:
                scope = "uncertain"
            anchor_node_id = str(item.get("anchor_node_id", "") or "").strip()
            if scope == "global":
                anchor_node_id = ""
            elif anchor_node_id not in active_ids:
                anchor_node_id = active_tip
            operations.append({
                "operation": action,
                "entity_type": kind,
                "entity_id": entity_id,
                "payload": payload,
                "reason": str(item.get("reason", "用户补充细节"))[:500],
                "risk": str(item.get("risk", "requires_approval"))[:100],
                "source": "user_or_advisor_detail",
                "source_ids": [str(value) for value in (item.get("source_ids") or [])],
                "scope": scope,
                "anchor_node_id": anchor_node_id,
                "scope_reason": str(item.get("scope_reason", ""))[:500],
            })
        if not operations:
            raise ValueError("世界书管理 Agent 未生成可应用的有效变更")
        conflicts = data.get("conflicts") if isinstance(data.get("conflicts"), list) else []
        return operations, [item for item in conflicts if isinstance(item, dict)]

    def _world_index(self, book_title: str) -> list[dict]:
        from core.context_assembler import _world_entities
        bible = self.manager.load_world_bible(book_title)
        result = []
        for entity_id, kind, name, item in _world_entities(bible):
            data = asdict(item) if hasattr(item, "__dataclass_fields__") else dict(item)
            result.append({
                "id": entity_id,
                "kind": kind,
                "name": name,
                "brief": str(data.get("description") or data.get("current_goal") or data.get("hint") or "")[:300],
                "hidden": bool(data.get("hidden", False)),
            })
        return result

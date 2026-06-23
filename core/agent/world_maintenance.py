from __future__ import annotations

import copy
from dataclasses import asdict, dataclass, field

from core.agent.types import now_iso


@dataclass
class WorldMaintenanceResult:
    task_id: str
    status: str
    chapter_num: int
    version: int
    added: list[dict] = field(default_factory=list)
    updated: list[dict] = field(default_factory=list)
    resolved_threads: list[str] = field(default_factory=list)
    resolved_foreshadowing: list[str] = field(default_factory=list)
    archived: list[dict] = field(default_factory=list)
    deleted_garbage: list[dict] = field(default_factory=list)
    conflicts: list[dict] = field(default_factory=list)
    rebuild_report: dict = field(default_factory=dict)
    snapshot_id: str = ""
    error: str = ""


class WorldBibleMaintenanceService:
    """Idempotent chapter snapshot extraction, active-path rebuild and safe archival."""

    def __init__(self, novel_manager) -> None:
        self.manager = novel_manager

    def maintain(self, client, book_title: str, chapter_num: int, version: int, *, model: str, global_user_prompt: str = "", xp_mode: bool = False, plan: dict | None = None) -> WorldMaintenanceResult:
        task_id = self._task_id(chapter_num, version)
        workspace = self.manager.get_workspace(book_title)
        snapshot = self.manager.snapshot_service(book_title).create(f"第{chapter_num}章 v{version} 世界书维护前备份", source="chapter")
        before = self.manager.load_world_bible(book_title)
        before_view = self._entity_map(before)
        try:
            from core.world_bible import _chapter_world_entry_key
            key = _chapter_world_entry_key(chapter_num, version)
            existing_snapshots = getattr(before, "chapter_snapshots", {}) or {}
            if key in existing_snapshots:
                rebuild = self.manager.rebuild_world_bible_from_active(client, book_title, model=model, global_user_prompt=global_user_prompt, xp_mode=xp_mode)
            else:
                node_id = self.manager._node_id(chapter_num, version)
                rebuild = self.manager.extract_world_bible_for_node(client, book_title, node_id, model=model, global_user_prompt=global_user_prompt, xp_mode=xp_mode)
            bible = self.manager.load_world_bible(book_title)
            archival = self._archive_obsolete(book_title, bible)
            self.manager.save_world_bible(book_title, bible)
            after_view = self._entity_map(bible)
            result = WorldMaintenanceResult(
                task_id=task_id, status="completed", chapter_num=chapter_num, version=version,
                added=[after_view[key] for key in after_view.keys() - before_view.keys()],
                updated=[after_view[key] for key in after_view.keys() & before_view.keys() if after_view[key] != before_view[key]],
                resolved_threads=[item.name for item in bible.active_plot_threads if item.status == "resolved"],
                resolved_foreshadowing=[str(item.get("hint", "")) for item in bible.global_foreshadowing if item.get("status") in {"resolved", "已回收"}],
                archived=archival["archived"], deleted_garbage=archival["deleted_garbage"],
                conflicts=list(getattr(bible, "consistency_warnings", []) or []), rebuild_report=rebuild,
                snapshot_id=snapshot.snapshot_id,
            )
            self._write_result(workspace, result, plan)
            self._delete_pending(workspace, task_id)
            return result
        except Exception as exc:
            error = str(exc)
            try:
                self.manager.snapshot_service(book_title).restore(snapshot.snapshot_id)
            except Exception as restore_exc:
                error += f"；维护回滚失败: {restore_exc}"
            result = WorldMaintenanceResult(task_id, "pending", chapter_num, version, snapshot_id=snapshot.snapshot_id, error=error)
            workspace.storage.write_json(self._pending_path(workspace, task_id), {
                "schema_version": 1, "task_id": task_id, "book_title": book_title,
                "chapter_num": chapter_num, "version": version, "model": model,
                "global_user_prompt": global_user_prompt, "xp_mode": xp_mode,
                "plan": plan or {}, "error": error, "created_at": now_iso(),
            })
            self._write_result(workspace, result, plan)
            return result

    def retry(self, client, book_title: str, task_id: str):
        workspace = self.manager.get_workspace(book_title)
        task = workspace.storage.read_json(self._pending_path(workspace, task_id))
        if not isinstance(task, dict):
            raise ValueError("待重试世界书维护任务不存在")
        return self.maintain(
            client, book_title, int(task["chapter_num"]), int(task["version"]),
            model=str(task["model"]), global_user_prompt=str(task.get("global_user_prompt", "")),
            xp_mode=bool(task.get("xp_mode", False)), plan=task.get("plan") or {},
        )

    def list_pending(self, book_title: str) -> list[dict]:
        workspace = self.manager.get_workspace(book_title)
        result = []
        for path in workspace.storage.list_files(f"{workspace.agent_root}/maintenance/pending"):
            data = workspace.storage.read_json(path)
            if isinstance(data, dict):
                result.append(data)
        return sorted(result, key=lambda item: item.get("created_at", ""), reverse=True)

    def _archive_obsolete(self, book_title: str, bible) -> dict:
        policies = self.manager.get_workspace(book_title).load_context_policies()
        protected_ids = {str(item.entity_id) for item in getattr(bible, "manual_overrides", []) if getattr(item, "entity_id", "")}
        archived = []
        for thread in bible.active_plot_threads:
            protected = thread.id in protected_ids or policies.get(thread.id, {}).get("load_mode") == "resident" or thread.locked
            if thread.status in {"resolved", "dormant"} and not protected and not thread.hidden:
                thread.hidden = True
                archived.append({"type": "plot_thread", "id": thread.id, "name": thread.name, "reason": thread.status})
        for item in bible.global_foreshadowing:
            entity_id = str(item.get("id", ""))
            protected = entity_id in protected_ids or policies.get(entity_id, {}).get("load_mode") == "resident" or bool(item.get("locked"))
            if item.get("status") in {"resolved", "已回收", "dormant"} and not protected and not item.get("hidden"):
                item["hidden"] = True
                archived.append({"type": "foreshadowing", "id": entity_id, "name": item.get("hint", ""), "reason": item.get("status")})
        deleted = []
        for attr, kind, name_key in (("active_plot_threads", "plot_thread", "name"), ("global_foreshadowing", "foreshadowing", "hint")):
            collection = getattr(bible, attr)
            kept = []
            for item in collection:
                data = asdict(item) if hasattr(item, "__dataclass_fields__") else dict(item)
                has_source = bool(data.get("source_refs") or data.get("source_chapter") or data.get("opened_chapter") or data.get("introduced_chapter"))
                entity_id = str(data.get("id", ""))
                name = str(data.get(name_key, "")).strip()
                meaningful = any(data.get(key) not in (None, "", [], {}, False, 0) for key in ("description", "key_details", "relates_to", "next_step"))
                if not entity_id and not name and not meaningful and not has_source:
                    deleted.append({"type": kind, "id": "", "name": "", "reason": "empty_without_source"})
                else:
                    kept.append(item)
            setattr(bible, attr, kept)
        return {"archived": archived, "deleted_garbage": deleted}

    @staticmethod
    def _entity_map(bible) -> dict[str, dict]:
        result = {}
        groups = (("character", bible.characters), ("location", bible.locations), ("plot_thread", bible.active_plot_threads), ("timeline", bible.timeline), ("foreshadowing", bible.global_foreshadowing))
        for kind, items in groups:
            for item in items:
                data = asdict(item) if hasattr(item, "__dataclass_fields__") else copy.deepcopy(item)
                entity_id = str(data.get("id", ""))
                if entity_id:
                    result[f"{kind}:{entity_id}"] = {"type": kind, **data}
        return result

    @staticmethod
    def _task_id(chapter_num: int, version: int) -> str:
        return f"world_ch{chapter_num:04d}_v{version:03d}"

    @staticmethod
    def _pending_path(workspace, task_id: str) -> str:
        return f"{workspace.agent_root}/maintenance/pending/{task_id}.json"

    @staticmethod
    def _delete_pending(workspace, task_id: str) -> None:
        workspace.storage.delete(WorldBibleMaintenanceService._pending_path(workspace, task_id))

    @staticmethod
    def _write_result(workspace, result: WorldMaintenanceResult, plan: dict | None) -> None:
        workspace.storage.write_json(f"{workspace.agent_root}/maintenance/reports/{result.task_id}.json", {**asdict(result), "plan": plan or {}, "updated_at": now_iso()})

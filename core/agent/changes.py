from __future__ import annotations

import hashlib
import uuid

from core.agent.types import ChangeOperation, ChangeSet


class ChangeSetError(RuntimeError):
    pass


class ChangeSetService:
    def __init__(self, novel_manager, book_title: str, repository) -> None:
        self.manager = novel_manager
        self.book_title = book_title
        self.repository = repository

    def propose_chapter(self, run_id: str, book_id: str, chapter_num: int, chapter_title: str, content: str, parent_id: str = "", reason: str = "") -> ChangeSet:
        current = self.manager.read_active_chapter(self.book_title, chapter_num) or ""
        operation = ChangeOperation(
            operation_id=f"op_{uuid.uuid4().hex}",
            operation="chapter.save_version",
            target_type="chapter",
            target_id=str(chapter_num),
            expected_checksum=self._digest(current),
            payload={"chapter_num": chapter_num, "chapter_title": chapter_title, "content": content, "parent_id": parent_id},
        )
        change_set = ChangeSet(f"changes_{uuid.uuid4().hex}", run_id, book_id, [operation], {"valid": True}, reason=reason)
        self.repository.save_change_set(change_set)
        return change_set

    def propose_world_bible(self, run_id: str, book_id: str, world_bible: dict, reason: str = "") -> ChangeSet:
        from core.world_bible import world_bible_to_dict
        current = world_bible_to_dict(self.manager.load_world_bible(self.book_title))
        operation = ChangeOperation(
            operation_id=f"op_{uuid.uuid4().hex}",
            operation="world_bible.replace",
            target_type="world_bible",
            target_id="world_bible",
            expected_checksum=self._digest_json(current),
            payload={"world_bible": world_bible},
        )
        change_set = ChangeSet(f"changes_{uuid.uuid4().hex}", run_id, book_id, [operation], {"valid": True}, reason=reason)
        self.repository.save_change_set(change_set)
        return change_set


    def propose_world_patch(self, run_id: str, book_id: str, operations: list[dict], reason: str = "") -> ChangeSet:
        from core.world_bible import world_bible_to_dict
        current = world_bible_to_dict(self.manager.load_world_bible(self.book_title))
        operation = ChangeOperation(
            operation_id=f"op_{uuid.uuid4().hex}",
            operation="world_bible.patch",
            target_type="world_bible",
            target_id="world_bible",
            expected_checksum=self._digest_json(current),
            payload={"operations": operations},
        )
        change_set = ChangeSet(f"changes_{uuid.uuid4().hex}", run_id, book_id, [operation], {"valid": True, "operation_count": len(operations)}, reason=reason)
        self.repository.save_change_set(change_set)
        return change_set

    def approve(self, change_set_id: str, approved_operation_ids: list[str] | None = None) -> ChangeSet:
        change_set = self.repository.load_change_set(change_set_id)
        if change_set is None or change_set.status != "pending":
            raise ChangeSetError("待审批变更不存在")
        approved = set(approved_operation_ids or [item.operation_id for item in change_set.operations])
        self._validate(change_set, approved)
        snapshot = self.manager.snapshot_service(self.book_title).create(f"应用 Agent 变更 {change_set_id} 前自动备份", source="rollback_backup")
        try:
            for operation in change_set.operations:
                if operation.operation_id not in approved:
                    operation.status = "rejected"
                    continue
                self._apply(operation)
                operation.status = "applied"
            change_set.status = "applied" if all(item.status == "applied" for item in change_set.operations) else "partially_applied"
            change_set.validation_result = {"valid": True, "snapshot_id": snapshot.snapshot_id}
            self.repository.save_change_set(change_set)
            return change_set
        except Exception as exc:
            self.manager.snapshot_service(self.book_title).restore(snapshot.snapshot_id)
            change_set.status = "failed"
            change_set.validation_result = {"valid": False, "error": str(exc), "snapshot_id": snapshot.snapshot_id}
            self.repository.save_change_set(change_set)
            raise ChangeSetError(f"变更应用失败，已恢复: {exc}") from exc

    def reject(self, change_set_id: str) -> ChangeSet:
        change_set = self.repository.load_change_set(change_set_id)
        if change_set is None or change_set.status != "pending":
            raise ChangeSetError("待审批变更不存在")
        change_set.status = "rejected"
        for operation in change_set.operations:
            operation.status = "rejected"
        self.repository.save_change_set(change_set)
        return change_set

    def _validate(self, change_set: ChangeSet, approved: set[str]) -> None:
        for operation in change_set.operations:
            if operation.operation_id not in approved:
                continue
            if operation.operation == "chapter.save_version":
                current = self.manager.read_active_chapter(self.book_title, int(operation.target_id)) or ""
                actual = self._digest(current)
            elif operation.operation in {"world_bible.replace", "world_bible.patch"}:
                from core.world_bible import world_bible_to_dict
                actual = self._digest_json(world_bible_to_dict(self.manager.load_world_bible(self.book_title)))
            else:
                raise ChangeSetError(f"不支持的变更操作: {operation.operation}")
            if operation.expected_checksum and actual != operation.expected_checksum:
                raise ChangeSetError(f"目标已变化，变更过期: {operation.target_id}")

    def _apply(self, operation: ChangeOperation) -> None:
        if operation.operation == "chapter.save_version":
            data = operation.payload
            chapter_num = int(data["chapter_num"])
            version = self.manager.get_next_version(self.book_title, chapter_num)
            _path, saved_version = self.manager.save_chapter_version(self.book_title, chapter_num, data["chapter_title"], data["content"], version=version, parent_id=data.get("parent_id") or None)
            self.manager.switch_active_node(self.book_title, self.manager._node_id(chapter_num, saved_version))
        elif operation.operation == "world_bible.replace":
            from core.world_bible import dict_to_world_bible
            self.manager.save_world_bible(self.book_title, dict_to_world_bible(operation.payload["world_bible"]))
        elif operation.operation == "world_bible.patch":
            import copy
            from core.world_bible import (
                dict_to_world_bible,
                record_manual_view_changes,
                world_bible_to_dict,
            )
            data = world_bible_to_dict(self.manager.load_world_bible(self.book_title))
            before_view = copy.deepcopy(data.get("resolved_view") or {
                key: data.get(key, [])
                for key in (
                    "characters", "locations", "timeline", "active_plot_threads",
                    "key_worldbuilding_passages", "global_foreshadowing",
                    "global_key_dialogues", "world_rules",
                )
            })
            before_view["story_clock"] = copy.deepcopy(data.get("story_clock", {}))
            self._apply_world_patch(data, operation.payload.get("operations", []))
            bible = dict_to_world_bible(data)
            record_manual_view_changes(bible, before_view)
            self.manager.save_world_bible(self.book_title, bible)

    @staticmethod
    def _apply_world_patch(data: dict, operations: list[dict]) -> None:
        collections = {
            "character": "characters",
            "location": "locations",
            "rule": "rules",
            "timeline": "timeline",
            "plot_thread": "active_plot_threads",
            "world_rule": "world_rules",
            "foreshadowing": "global_foreshadowing",
        }
        for op in operations or []:
            if not isinstance(op, dict):
                continue
            kind = str(op.get("entity_type") or op.get("type") or "").strip()
            collection_name = collections.get(kind, kind)
            if collection_name not in data or not isinstance(data.get(collection_name), list):
                raise ChangeSetError(f"不支持的世界书实体类型: {kind or collection_name}")
            entity_id = str(op.get("entity_id") or op.get("id") or "").strip()
            payload = op.get("payload") if isinstance(op.get("payload"), dict) else {}
            action = str(op.get("operation") or "").strip()
            collection = data.setdefault(collection_name, [])
            target = next((item for item in collection if isinstance(item, dict) and str(item.get("id", "")) == entity_id), None)
            if action == "entity.create":
                item = dict(payload)
                if entity_id:
                    item.setdefault("id", entity_id)
                if not item.get("id"):
                    raise ChangeSetError("新增世界书实体必须包含 id")
                if any(isinstance(existing, dict) and existing.get("id") == item.get("id") for existing in collection):
                    raise ChangeSetError(f"世界书实体已存在: {item.get('id')}")
                collection.append(item)
            elif action == "entity.patch":
                if target is None:
                    raise ChangeSetError(f"世界书实体不存在: {entity_id}")
                target.update(payload)
            elif action == "entity.archive":
                if target is None:
                    raise ChangeSetError(f"世界书实体不存在: {entity_id}")
                target["hidden"] = True
                if payload:
                    target.update(payload)
            elif action == "entity.supersede":
                if target is None:
                    raise ChangeSetError(f"世界书实体不存在: {entity_id}")
                supersedes = target.setdefault("supersedes", [])
                source = op.get("supersedes") or payload.get("supersedes")
                if isinstance(source, list):
                    for item in source:
                        if item not in supersedes:
                            supersedes.append(item)
                elif source and source not in supersedes:
                    supersedes.append(source)
                target.update({k: v for k, v in payload.items() if k != "supersedes"})
            elif action == "entity.merge":
                if target is None:
                    raise ChangeSetError(f"世界书实体不存在: {entity_id}")
                target.update(payload)
                for source_id in op.get("source_ids", []) or []:
                    source = next((item for item in collection if isinstance(item, dict) and str(item.get("id", "")) == str(source_id)), None)
                    if source is not None and source is not target:
                        source["hidden"] = True
                        source["merged_into"] = entity_id
            else:
                raise ChangeSetError(f"不支持的世界书变更操作: {action}")

    @staticmethod
    def _digest(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    @staticmethod
    def _digest_json(value: dict) -> str:
        import json
        return ChangeSetService._digest(json.dumps(value, ensure_ascii=False, sort_keys=True))

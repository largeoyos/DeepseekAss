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
            elif operation.operation == "world_bible.replace":
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

    @staticmethod
    def _digest(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    @staticmethod
    def _digest_json(value: dict) -> str:
        import json
        return ChangeSetService._digest(json.dumps(value, ensure_ascii=False, sort_keys=True))

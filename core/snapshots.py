"""Encrypted, content-addressed whole-book snapshots."""
from __future__ import annotations

import difflib
import hashlib
import json
import os
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime

from core.workspace import BookWorkspace


SNAPSHOT_SCHEMA_VERSION = 1


@dataclass
class SnapshotEntry:
    path: str
    sha256: str
    chars: int


@dataclass
class SnapshotManifest:
    schema_version: int = SNAPSHOT_SCHEMA_VERSION
    snapshot_id: str = ""
    message: str = ""
    source: str = "manual"
    created_at: str = ""
    files: list[SnapshotEntry] = field(default_factory=list)


class SnapshotError(RuntimeError):
    pass


class EncryptedSnapshotService:
    def __init__(self, workspace: BookWorkspace, *, auto_retention: int = 30) -> None:
        self.workspace = workspace
        self.storage = workspace.storage
        self.auto_retention = max(1, int(auto_retention))

    def create(self, message: str = "", source: str = "manual") -> SnapshotManifest:
        source = source if source in {"manual", "chapter", "timer", "rollback_backup"} else "manual"
        snapshot_id = datetime.now().strftime("%Y%m%d%H%M%S") + "-" + uuid.uuid4().hex[:8]
        entries: list[SnapshotEntry] = []
        for path in self.workspace.list_content_files():
            text = self.storage.read_text(path)
            if text is None:
                continue
            digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
            block_path = self._block_path(digest)
            if not self.storage.exists(block_path):
                self.storage.write_text(block_path, text)
            entries.append(SnapshotEntry(path=path, sha256=digest, chars=len(text)))
        manifest = SnapshotManifest(
            snapshot_id=snapshot_id,
            message=message.strip() or self._default_message(source),
            source=source,
            created_at=datetime.now().isoformat(timespec="seconds"),
            files=entries,
        )
        self.storage.write_json(self._manifest_path(snapshot_id), {
            **asdict(manifest),
            "files": [asdict(item) for item in entries],
        })
        self._prune_auto_snapshots()
        return manifest

    def create_if_changed(self, message: str = "", source: str = "timer"):
        snapshots = self.list()
        if snapshots:
            latest = snapshots[0]
            latest_files = {item.path: item.sha256 for item in latest.files}
            current_paths = set(self.workspace.list_content_files())
            if current_paths == set(latest_files) and all(
                self.storage.checksum(path) == digest
                for path, digest in latest_files.items()
            ):
                return None
        return self.create(message, source=source)

    def list(self) -> list[SnapshotManifest]:
        manifests: list[SnapshotManifest] = []
        prefix = f"{self.workspace.snapshot_root}/manifests"
        for path in self.storage.list_files(prefix):
            if not path.endswith(".json"):
                continue
            data = self.storage.read_json(path)
            if not isinstance(data, dict):
                continue
            try:
                manifests.append(self._decode_manifest(data))
            except Exception:
                continue
        return sorted(manifests, key=lambda item: item.created_at, reverse=True)

    def get(self, snapshot_id: str) -> SnapshotManifest:
        data = self.storage.read_json(self._manifest_path(snapshot_id))
        if not isinstance(data, dict):
            raise SnapshotError(f"项目版本不存在: {snapshot_id}")
        manifest = self._decode_manifest(data)
        self._verify(manifest)
        return manifest

    def status(self, snapshot_id: str) -> list[dict]:
        manifest = self.get(snapshot_id)
        snapshot_files = {item.path: item for item in manifest.files}
        current_files = set(self.workspace.list_content_files())
        changes: list[dict] = []
        for path, entry in snapshot_files.items():
            if path not in current_files:
                changes.append({"path": path, "status": "deleted"})
                continue
            if self.storage.checksum(path) != entry.sha256:
                changes.append({"path": path, "status": "modified"})
        for path in current_files - set(snapshot_files):
            changes.append({"path": path, "status": "added"})
        return sorted(changes, key=lambda item: item["path"])

    def diff(self, snapshot_id: str, path: str) -> str:
        manifest = self.get(snapshot_id)
        entry = next((item for item in manifest.files if item.path == path), None)
        before = self._read_block(entry.sha256) if entry else ""
        after = self.storage.read_text(path, default="") or ""
        return "".join(difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"{snapshot_id}/{path}",
            tofile=f"workspace/{path}",
        ))

    def restore(self, snapshot_id: str) -> SnapshotManifest:
        target = self.get(snapshot_id)
        self.create(
            message=f"恢复 {snapshot_id} 前自动备份",
            source="rollback_backup",
        )
        current_paths = set(self.workspace.list_content_files())
        target_paths = {item.path for item in target.files}
        rollback = {
            path: self.storage.read_text(path)
            for path in current_paths
        }
        try:
            for entry in target.files:
                self.storage.write_text(entry.path, self._read_block(entry.sha256))
            for path in current_paths - target_paths:
                self.storage.delete(path)
            self._verify_workspace(target)
        except Exception as exc:
            for path in set(self.workspace.list_content_files()) - set(rollback):
                self.storage.delete(path)
            for path, text in rollback.items():
                if text is not None:
                    self.storage.write_text(path, text)
            raise SnapshotError(f"恢复失败，当前书籍已回滚: {exc}") from exc
        return target

    def delete(self, snapshot_id: str) -> bool:
        return self.storage.delete(self._manifest_path(snapshot_id))

    def _verify(self, manifest: SnapshotManifest) -> None:
        for entry in manifest.files:
            text = self._read_block(entry.sha256)
            actual = hashlib.sha256(text.encode("utf-8")).hexdigest()
            if actual != entry.sha256:
                raise SnapshotError(f"快照内容校验失败: {entry.path}")

    def _verify_workspace(self, manifest: SnapshotManifest) -> None:
        expected = {item.path: item.sha256 for item in manifest.files}
        actual_paths = set(self.workspace.list_content_files())
        if actual_paths != set(expected):
            raise SnapshotError("恢复后的文件集合与快照不一致")
        for path, digest in expected.items():
            if self.storage.checksum(path) != digest:
                raise SnapshotError(f"恢复后的文件校验失败: {path}")

    def _read_block(self, digest: str) -> str:
        text = self.storage.read_text(self._block_path(digest))
        if text is None:
            raise SnapshotError(f"快照内容块缺失: {digest}")
        return text

    def _decode_manifest(self, data: dict) -> SnapshotManifest:
        return SnapshotManifest(
            schema_version=int(data.get("schema_version", 1)),
            snapshot_id=str(data["snapshot_id"]),
            message=str(data.get("message", "")),
            source=str(data.get("source", "manual")),
            created_at=str(data.get("created_at", "")),
            files=[
                SnapshotEntry(
                    path=str(item["path"]),
                    sha256=str(item["sha256"]),
                    chars=int(item.get("chars", 0)),
                )
                for item in data.get("files", [])
            ],
        )

    def _prune_auto_snapshots(self) -> None:
        automatic = [
            item for item in self.list()
            if item.source in {"chapter", "timer"}
        ]
        for item in automatic[self.auto_retention:]:
            self.delete(item.snapshot_id)

    def _manifest_path(self, snapshot_id: str) -> str:
        return f"{self.workspace.snapshot_root}/manifests/{snapshot_id}.json"

    def _block_path(self, digest: str) -> str:
        return f"{self.workspace.snapshot_root}/blocks/{digest}.blob"

    @staticmethod
    def _default_message(source: str) -> str:
        labels = {
            "manual": "手动保存",
            "chapter": "章节生成后自动保存",
            "timer": "定时自动保存",
            "rollback_backup": "恢复前自动备份",
        }
        return labels.get(source, "项目版本")

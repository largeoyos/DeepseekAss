"""Book workspace paths and schema migration."""
from __future__ import annotations

import os
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime

from core.storage import EncryptedStorage


WORKSPACE_SCHEMA_VERSION = 1
INTERNAL_DIR = ".deepseekass"


@dataclass
class WorkspaceManifest:
    schema_version: int = WORKSPACE_SCHEMA_VERSION
    book_id: str = ""
    created_at: str = ""
    migrations: list[dict] = field(default_factory=list)
    features: dict[str, bool] = field(default_factory=lambda: {
        "progressive_context": True,
        "encrypted_snapshots": True,
        "controlled_agent": False,
    })


class BookWorkspace:
    def __init__(self, root: str, crypto=None, enc_key: bytes | None = None) -> None:
        self.root = os.path.abspath(root)
        self.storage = EncryptedStorage(self.root, crypto=crypto, enc_key=enc_key)

    @property
    def manifest_path(self) -> str:
        return f"{INTERNAL_DIR}/manifest.json"

    @property
    def context_policies_path(self) -> str:
        return f"{INTERNAL_DIR}/context_policies.json"

    @property
    def snapshot_root(self) -> str:
        return f"{INTERNAL_DIR}/snapshots"

    @property
    def drafts_dir(self) -> str:
        return f"{INTERNAL_DIR}/drafts"

    def chapter_path(self, chapter_num: int, title: str, version: int) -> str:
        safe = str(title).replace("/", "-").replace("\\", "-").replace(":", "：")
        return f"第{chapter_num}章_{safe}_v{version}.txt"

    def generation_history_path(self, chapter_num: int, version: int) -> str:
        return f".generation_history/ch{chapter_num:04d}_v{version:03d}.json"

    def ensure_manifest(self, *, book_id: str = "") -> WorkspaceManifest:
        existing = self.storage.read_json(self.manifest_path)
        if isinstance(existing, dict):
            manifest = WorkspaceManifest(
                schema_version=int(existing.get("schema_version", 1) or 1),
                book_id=str(existing.get("book_id") or book_id or uuid.uuid4().hex),
                created_at=str(existing.get("created_at") or datetime.now().isoformat(timespec="seconds")),
                migrations=list(existing.get("migrations") or []),
                features={**WorkspaceManifest().features, **dict(existing.get("features") or {})},
            )
            if existing != asdict(manifest):
                self.storage.write_json(self.manifest_path, asdict(manifest))
            return manifest

        manifest = WorkspaceManifest(
            book_id=book_id or uuid.uuid4().hex,
            created_at=datetime.now().isoformat(timespec="seconds"),
            migrations=[{
                "id": "legacy-layout-v1",
                "status": "completed",
                "at": datetime.now().isoformat(timespec="seconds"),
            }],
        )
        self.storage.write_json(self.manifest_path, asdict(manifest))
        return manifest

    def load_context_policies(self) -> dict[str, dict]:
        data = self.storage.read_json(self.context_policies_path, default={})
        return data if isinstance(data, dict) else {}

    def save_context_policies(self, policies: dict[str, dict]) -> None:
        self.storage.write_json(self.context_policies_path, policies)

    def list_content_files(self) -> list[str]:
        excluded_prefixes = (
            f"{self.snapshot_root}/",
            f"{INTERNAL_DIR}/restore-",
        )
        return [
            path for path in self.storage.list_files()
            if not path.startswith(excluded_prefixes)
        ]

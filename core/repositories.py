"""Repository interfaces layered on top of the encrypted workspace."""
from __future__ import annotations

from dataclasses import dataclass

from core.storage import EncryptedStorage
from core.workspace import BookWorkspace


@dataclass
class BookRepository:
    workspace: BookWorkspace

    def read_meta(self) -> dict:
        return self.workspace.storage.read_json("meta.json", default={}) or {}

    def write_meta(self, data: dict) -> None:
        self.workspace.storage.write_json("meta.json", data)

    def read_summary(self) -> str:
        return self.workspace.storage.read_text("plot_summary.txt", default="") or ""

    def write_summary(self, text: str) -> None:
        self.workspace.storage.write_text("plot_summary.txt", text)

    def read_world_bible(self) -> dict:
        return self.workspace.storage.read_json("world_bible.json", default={}) or {}

    def write_world_bible(self, data: dict) -> None:
        self.workspace.storage.write_json("world_bible.json", data)


@dataclass
class ConversationRepository:
    storage: EncryptedStorage

    def list_ids(self) -> list[str]:
        return [
            path[:-5] for path in self.storage.list_files()
            if path.endswith(".json")
        ]

    def load(self, conversation_id: str) -> dict | None:
        return self.storage.read_json(f"{conversation_id}.json")

    def save(self, conversation_id: str, record: dict) -> str:
        return self.storage.write_json(f"{conversation_id}.json", record)

    def delete(self, conversation_id: str) -> bool:
        return self.storage.delete(f"{conversation_id}.json")

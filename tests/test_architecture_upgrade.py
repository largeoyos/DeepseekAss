import json
import os
import tempfile
import unittest

from core.agent_tools import (
    AgentPermissionError,
    ControlledAgentTools,
    READ_ONLY,
)
from core.app_services import TaskRunner
from core.context_assembler import ContextAssembler
from core.novel_manager import NovelManager
from core.snapshots import EncryptedSnapshotService
from core.storage import EncryptedStorage, StorageError
from core.workspace import BookWorkspace
from core.world_bible import CharacterEntry, LocationEntry, WorldBible


class PlainCrypto:
    @staticmethod
    def encrypt_text(_key, path, text):
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("ENC:" + text)

    @staticmethod
    def decrypt_text(_key, path):
        with open(path, "r", encoding="utf-8") as handle:
            value = handle.read()
        if not value.startswith("ENC:"):
            raise ValueError("bad key")
        return value[4:]

    @staticmethod
    def encrypt_json(key, path, data):
        PlainCrypto.encrypt_text(key, path, json.dumps(data, ensure_ascii=False))

    @staticmethod
    def decrypt_json(key, path):
        return json.loads(PlainCrypto.decrypt_text(key, path))


class ArchitectureUpgradeTests(unittest.TestCase):
    def test_encrypted_storage_is_atomic_and_path_bounded(self):
        with tempfile.TemporaryDirectory() as root:
            storage = EncryptedStorage(root, crypto=PlainCrypto(), enc_key=b"k")
            storage.write_json("nested/value.json", {"value": 1})
            self.assertTrue(os.path.exists(os.path.join(root, "nested", "value.json.enc")))
            self.assertEqual({"value": 1}, storage.read_json("nested/value.json"))
            self.assertEqual(
                storage.checksum("nested/value.json"),
                storage.checksum("nested/value.json"),
            )
            with self.assertRaises(StorageError):
                storage.write_text("../escape.txt", "no")

    def test_legacy_book_gets_manifest_without_moving_files(self):
        with tempfile.TemporaryDirectory() as root:
            manager = NovelManager(bookshelf_root=root)
            manager.create_book("legacy")
            manager.save_chapter_version("legacy", 1, "start", "content", version=1)
            workspace = manager.get_workspace("legacy")
            manifest = workspace.ensure_manifest(book_id=manager.load_meta("legacy").book_id)
            self.assertEqual(1, manifest.schema_version)
            self.assertTrue(workspace.storage.exists(workspace.manifest_path))
            self.assertEqual("content", manager.read_active_chapter("legacy", 1))

    def test_progressive_context_respects_policy_modes(self):
        with tempfile.TemporaryDirectory() as root:
            manager = NovelManager(bookshelf_root=root)
            manager.create_book("context")
            bible = WorldBible(
                characters=[
                    CharacterEntry(id="hero", name="Hero", current_goal="Find the key"),
                    CharacterEntry(id="villain", name="Villain", current_goal="Hide"),
                ],
                locations=[LocationEntry(id="tower", name="Tower", description="Ancient tower")],
            )
            manager.save_world_bible("context", bible)
            workspace = manager.get_workspace("context")
            workspace.save_context_policies({
                "hero": {"load_mode": "resident", "enabled": True},
                "villain": {
                    "load_mode": "auto",
                    "enabled": True,
                    "keywords": ["ambush"],
                    "brief_description": "Hidden enemy",
                },
                "tower": {"load_mode": "manual", "enabled": True},
            })
            report = ContextAssembler(manager).assemble_chapter(
                "context",
                1,
                "The ambush",
                manual_entity_ids=["tower"],
            )
            rendered = report.render()
            self.assertIn("Hero", rendered)
            self.assertIn("Villain", rendered)
            self.assertIn("Tower", rendered)
            self.assertIn("世界书索引", rendered)

    def test_snapshot_restores_modified_added_and_deleted_files(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = BookWorkspace(root, crypto=PlainCrypto(), enc_key=b"k")
            workspace.ensure_manifest(book_id="book")
            workspace.storage.write_text("a.txt", "one")
            workspace.storage.write_text("deleted.txt", "keep")
            service = EncryptedSnapshotService(workspace)
            snapshot = service.create("baseline")

            workspace.storage.write_text("a.txt", "two")
            workspace.storage.delete("deleted.txt")
            workspace.storage.write_text("added.txt", "new")
            statuses = {item["path"]: item["status"] for item in service.status(snapshot.snapshot_id)}
            self.assertEqual("modified", statuses["a.txt"])
            self.assertEqual("deleted", statuses["deleted.txt"])
            self.assertEqual("added", statuses["added.txt"])

            service.restore(snapshot.snapshot_id)
            self.assertEqual("one", workspace.storage.read_text("a.txt"))
            self.assertEqual("keep", workspace.storage.read_text("deleted.txt"))
            self.assertFalse(workspace.storage.exists("added.txt"))
            self.assertTrue(any(item.source == "rollback_backup" for item in service.list()))

    def test_read_only_agent_cannot_write(self):
        with tempfile.TemporaryDirectory() as root:
            manager = NovelManager(bookshelf_root=root)
            manager.create_book("agent")
            tools = ControlledAgentTools(manager, "agent", READ_ONLY)
            with self.assertRaises(AgentPermissionError):
                tools.write_draft("draft", "content")

    def test_task_runner_emits_terminal_event(self):
        events = []
        runner = TaskRunner(events.append)
        handle = runner.start("test", lambda _handle: 42)
        for _ in range(100):
            if handle.task_id not in runner.active():
                break
            import time
            time.sleep(0.01)
        types = [event.type for event in events]
        self.assertIn("started", types)
        self.assertIn("completed", types)
        self.assertIn("finished", types)


if __name__ == "__main__":
    unittest.main()

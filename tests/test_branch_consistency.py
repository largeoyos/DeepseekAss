import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from core.app_services import ChapterBranchService
from core.novel_manager import NovelManager
from core.world_bible import WorldBible, merge_extracted_world_bible_data
from web.server import create_app
from web.services import WebRuntime


class BranchConsistencyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app = create_app(WebRuntime(client_factory=lambda _: None))
        cls.endpoints = {route.path: route.endpoint for route in app.routes if hasattr(route, "endpoint")}

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.manager = NovelManager(self.temp.name)
        self.manager.create_book("book")
        bible = WorldBible()
        for version, label in ((1, "old-branch"), (2, "new-branch")):
            self.manager.save_chapter_version("book", 1, label, label, version=version)
            self.manager.set_chapter_node_summary("book", 1, version, label)
            bible = merge_extracted_world_bible_data(bible, {"rules": [label]},
                                                     chapter_num=1, chapter_version=version,
                                                     store_chapter_entry=True,
                                                     run_dedup=False)
        self.manager.save_world_bible("book", bible)
        ChapterBranchService(self.manager).activate_node("book", "ch0001_v001")
        self.context = SimpleNamespace(novel_manager=self.manager)

    def test_web_node_and_version_activation_sync_world(self):
        calls = [
            ("/api/books/{title}/nodes/{node_id}/activate", ("book", "ch0001_v002")),
            ("/api/books/{title}/chapters/{chapter_num}/versions/{version}/activate", ("book", 1, 2)),
        ]
        for path, args in calls:
            with self.subTest(path=path):
                ChapterBranchService(self.manager).activate_node("book", "ch0001_v001")
                result = self.endpoints[path](*args, ctx=self.context)
                self.assertTrue(result["ok"])
                self.assertEqual(["new-branch"], self.manager.load_world_bible("book").rules)
                self.assertIn("new-branch", self.manager.load_summary("book"))
                self.assertNotIn("old-branch", self.manager.load_summary("book"))

    def test_tree_activation_drops_old_world_and_reports_missing_snapshots(self):
        node = self.manager.save_extra_node("book", run_id="prequel", extra_type="prequel",
                                           chapter_title="prequel", content="prequel content",
                                           reference_node_id="ch0001_v001", summary="prequel summary")
        result = self.endpoints["/api/books/{title}/chapter-trees/{tree_id}/activate"](
            "book", node["tree_id"], ctx=self.context)
        self.assertEqual([], self.manager.load_world_bible("book").rules)
        self.assertIn(node["id"], result["world_sync"]["missing_snapshots"])
        self.assertGreater(result["world_sync"]["snapshot_skipped_count"], 0)

    def test_sync_failure_restores_branch_summary_and_world(self):
        storage = self.manager.get_workspace("book").storage
        before = {path: storage.read_text(path) for path in ("meta.json", "plot_summary.txt", "world_bible.json")}
        with patch.object(self.manager, "rebuild_world_bible_from_active", side_effect=OSError("disk unavailable")):
            with self.assertRaises(OSError):
                ChapterBranchService(self.manager).activate_node("book", "ch0001_v002")
        self.assertEqual(before, {path: storage.read_text(path) for path in before})


class SummaryVersionTests(unittest.TestCase):
    def test_new_version_cannot_inherit_old_summary(self):
        with tempfile.TemporaryDirectory() as root:
            manager = NovelManager(root)
            manager.create_book("book")
            manager.save_chapter_version("book", 1, "first", "hero died", version=1)
            manager.set_chapter_node_summary("book", 1, 1, "hero died")
            manager.rebuild_plot_summary_from_tree("book")
            manager.save_chapter_version("book", 1, "alternate", "hero survived", version=2)
            ChapterBranchService(manager).activate_node("book", "ch0001_v002")
            self.assertEqual("", manager.list_active_summary_entries("book")[0]["summary"])
            self.assertNotIn("hero died", manager.load_smart_summary("book"))
            self.assertEqual("[摘要不可用]", manager._extract_chapter_summary("book", 1))
            self.assertIn("本版本尚无摘要", manager.load_summary("book"))

    def test_legacy_summary_migrates_only_to_original_active_version(self):
        with tempfile.TemporaryDirectory() as root:
            manager = NovelManager(root)
            manager.create_book("book")
            manager.save_meta("book", schema_version=1, chapter_nodes={}, chapter_versions={
                "1": {"active": 1, "versions": [{"v": 1, "title": "first", "file": "first.txt"}]},
            })
            manager._write_encrypted_text(manager._summary_path("book"), "第1章「first」摘要：old summary")
            # Editing an unmigrated legacy book must bind the old summary first.
            manager.save_chapter_version("book", 1, "alternate", "new content", version=2)
            self.assertEqual("old summary", manager.get_chapter_node_summary("book", 1, 1))
            self.assertEqual("", manager.get_chapter_node_summary("book", 1, 2))
            ChapterBranchService(manager).activate_node("book", "ch0001_v002")
            self.assertNotIn("old summary", manager.load_summary("book"))
            ChapterBranchService(manager).activate_node("book", "ch0001_v001")
            self.assertIn("old summary", manager.load_summary("book"))


if __name__ == "__main__":
    unittest.main()

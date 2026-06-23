import tempfile
import unittest

from core.novel_manager import NovelManager


class ChapterForestTests(unittest.TestCase):
    def _manager_with_two_chapters(self):
        temp = tempfile.TemporaryDirectory()
        manager = NovelManager(bookshelf_root=temp.name)
        title = "森林测试"
        manager.create_book(title)
        manager.save_chapter_version(title, 1, "起点", "第一章正文", version=1)
        manager.set_chapter_node_summary(title, 1, 1, "第一章摘要")
        manager.save_chapter_version(title, 2, "推进", "第二章正文", version=1)
        manager.set_chapter_node_summary(title, 2, 1, "第二章摘要")
        return temp, manager, title

    def test_legacy_book_normalizes_into_primary_tree(self):
        temp, manager, title = self._manager_with_two_chapters()
        try:
            meta = manager.ensure_chapter_tree(title)
            self.assertEqual("primary_tree", meta.active_tree_id)
            self.assertEqual("ch0000_v000", meta.tree_roots["primary_tree"])
            self.assertEqual(["ch0000_v000", "ch0001_v001", "ch0002_v001"], meta.active_path)
            self.assertEqual("primary_tree", meta.chapter_nodes["ch0001_v001"]["tree_id"])
            self.assertEqual("main", meta.chapter_nodes["ch0002_v001"]["node_kind"])
        finally:
            temp.cleanup()

    def test_enrichment_extra_rewires_between_consecutive_nodes_and_delete_reconnects(self):
        temp, manager, title = self._manager_with_two_chapters()
        try:
            node = manager.save_extra_node(
                title,
                run_id="run-enrich-1",
                extra_type="enrichment",
                chapter_title="雨夜补章",
                content="番外正文",
                start_node_id="ch0001_v001",
                end_node_id="ch0002_v001",
                summary="番外摘要",
                generation_record={"operation": "agent_extra_generation"},
            )
            meta = manager.ensure_chapter_tree(title)
            extra_id = node["id"]
            self.assertEqual("extra_uuid", meta.chapter_nodes[extra_id]["storage_kind"])
            self.assertEqual(["ch0000_v000", "ch0001_v001", extra_id, "ch0002_v001"], meta.active_path)
            self.assertIn(extra_id, meta.chapter_nodes["ch0001_v001"]["children_ids"])
            self.assertEqual(extra_id, meta.chapter_nodes["ch0002_v001"]["parent_id"])
            self.assertEqual("番外正文", manager.read_chapter_node(title, extra_id))
            self.assertEqual("agent_extra_generation", manager.load_node_generation_record(title, extra_id)["operation"])

            self.assertTrue(manager.delete_chapter_node(title, extra_id))
            meta = manager.ensure_chapter_tree(title)
            self.assertEqual("ch0001_v001", meta.chapter_nodes["ch0002_v001"]["parent_id"])
            self.assertIn("ch0002_v001", meta.chapter_nodes["ch0001_v001"]["children_ids"])
            self.assertNotIn(extra_id, meta.active_path)
        finally:
            temp.cleanup()

    def test_if_line_extra_branches_without_changing_primary_path(self):
        temp, manager, title = self._manager_with_two_chapters()
        try:
            before_path = list(manager.ensure_chapter_tree(title).active_path)
            node = manager.save_extra_node(
                title,
                run_id="run-if-1",
                extra_type="if_line",
                chapter_title="另一种选择",
                content="IF正文",
                start_node_id="ch0001_v001",
                end_node_id="ch0002_v001",
                summary="IF摘要",
            )
            meta = manager.ensure_chapter_tree(title)
            extra_id = node["id"]
            self.assertEqual(before_path, meta.active_path)
            self.assertEqual("ch0001_v001", meta.chapter_nodes[extra_id]["parent_id"])
            self.assertEqual("ch0001_v001", meta.chapter_nodes["ch0002_v001"]["parent_id"])
            self.assertIn(extra_id, meta.chapter_nodes["ch0001_v001"]["children_ids"])
            self.assertIn("ch0002_v001", meta.chapter_nodes["ch0001_v001"]["children_ids"])
        finally:
            temp.cleanup()

    def test_prequel_and_sequel_create_independent_tree_roots(self):
        temp, manager, title = self._manager_with_two_chapters()
        try:
            before_path = list(manager.ensure_chapter_tree(title).active_path)
            prequel = manager.save_extra_node(
                title,
                run_id="run-prequel-1",
                extra_type="prequel",
                chapter_title="前传",
                content="前传正文",
                reference_node_id="ch0001_v001",
                summary="前传摘要",
            )
            sequel = manager.save_extra_node(
                title,
                run_id="run-sequel-1",
                extra_type="sequel",
                chapter_title="后传",
                content="后传正文",
                reference_node_id="ch0002_v001",
                summary="后传摘要",
            )
            meta = manager.ensure_chapter_tree(title)
            self.assertEqual("primary_tree", meta.active_tree_id)
            self.assertEqual(before_path, meta.active_path)
            self.assertIsNone(meta.chapter_nodes[prequel["id"]]["parent_id"])
            self.assertIsNone(meta.chapter_nodes[sequel["id"]]["parent_id"])
            self.assertIn(prequel["tree_id"], meta.tree_roots)
            self.assertIn(sequel["tree_id"], meta.tree_roots)
            self.assertEqual(prequel["id"], meta.tree_roots[prequel["tree_id"]])
            self.assertEqual("前传正文", manager.read_chapter_node(title, prequel["id"]))

            self.assertTrue(manager.switch_active_tree(title, prequel["tree_id"]))
            switched = manager.ensure_chapter_tree(title)
            self.assertEqual(prequel["tree_id"], switched.active_tree_id)
            self.assertEqual([prequel["id"]], switched.active_path)
        finally:
            temp.cleanup()

    def test_duplicate_extra_run_is_idempotent(self):
        temp, manager, title = self._manager_with_two_chapters()
        try:
            first = manager.save_extra_node(
                title,
                run_id="same-run",
                extra_type="if_line",
                chapter_title="IF",
                content="第一次",
                start_node_id="ch0001_v001",
                end_node_id="ch0002_v001",
            )
            second = manager.save_extra_node(
                title,
                run_id="same-run",
                extra_type="if_line",
                chapter_title="IF",
                content="第二次不应写入",
                start_node_id="ch0001_v001",
                end_node_id="ch0002_v001",
            )
            self.assertEqual(first["id"], second["id"])
            self.assertEqual("第一次", manager.read_chapter_node(title, first["id"]))
        finally:
            temp.cleanup()


if __name__ == "__main__":
    unittest.main()
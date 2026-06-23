import json
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from core.agent.chapter_generation import AgentChapterGenerationService, AgentChapterRequest
from core.agent.world_maintenance import WorldBibleMaintenanceService
from core.novel_manager import NovelManager
from core.world_bible import CharacterEntry, ManualOverride, PlotThread, WorldBible, _chapter_world_entry_key


class FakeCompletions:
    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        payload = self.payloads.pop(0)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload, ensure_ascii=False)))])


class FakeClient:
    def __init__(self, payloads):
        self.chat = SimpleNamespace(completions=FakeCompletions(payloads))


def valid_plan():
    return {
        "chapter_goal": "主角进入钟楼并发现失踪案的新线索",
        "scenes": [{"title": "进入钟楼", "purpose": "推进调查", "conflict": "守卫阻拦", "outcome": "发现暗门"}],
        "character_arcs": [{"character": "阿离", "start_state": "犹豫", "end_state": "决定追查"}],
        "plot_threads": ["钟楼谜案"],
        "foreshadowing_actions": [{"action": "推进", "target": "午夜钟声"}],
        "selected_world_entities": [{"id": "hero", "name": "阿离", "reason": "本章主角"}],
        "selected_history_chapters": [1],
        "constraints": ["不能让主角提前知道幕后真凶"],
        "planning_notes": "保持悬疑节奏",
    }


class AgentChapterGenerationTests(unittest.TestCase):
    def test_prepare_reads_settings_selects_world_and_saves_ledger(self):
        with tempfile.TemporaryDirectory() as root:
            manager = NovelManager(bookshelf_root=root)
            manager.create_book("book")
            manager.save_meta("book", protagonist_bio="阿离，谨慎的调查者", background_story="蒸汽城邦", writing_demand="悬疑慢热", author_plan="本卷调查钟楼")
            manager.save_world_bible("book", WorldBible(characters=[CharacterEntry(id="hero", name="阿离", current_goal="调查钟楼")]))
            manager.save_chapter_version("book", 1, "失踪", "第一章正文", version=1)
            manager.set_chapter_node_summary("book", 1, 1, "阿离接手钟楼失踪案。")
            manager.rebuild_plot_summary_from_tree("book")
            client = FakeClient([valid_plan()])
            request = AgentChapterRequest("book", 2, "午夜钟声", "进入钟楼", "悬疑慢热", 3000, "fake")
            plan = AgentChapterGenerationService(manager, client).prepare(request)
            self.assertEqual("hero", plan.selected_world_entities[0]["id"])
            self.assertIn("阿离", plan.context_report["content"])
            self.assertEqual(1, plan.selected_history[0]["chapter_num"])
            self.assertTrue(plan.selected_skills)
            self.assertIn("chapter-planning", {item["id"] for item in plan.selected_skills})
            workspace = manager.get_workspace("book")
            ledger = workspace.storage.read_json(f"{workspace.agent_root}/chapter_runs/{plan.plan_id}.json")
            self.assertEqual("prepared", ledger["status"])

    def test_invalid_plan_is_repaired_once(self):
        with tempfile.TemporaryDirectory() as root:
            manager = NovelManager(bookshelf_root=root)
            manager.create_book("book")
            client = FakeClient([{"chapter_goal": "bad"}, valid_plan()])
            plan = AgentChapterGenerationService(manager, client).prepare(
                AgentChapterRequest("book", 1, "开始", "", "", 1000, "fake")
            )
            self.assertTrue(plan.scenes)
            self.assertEqual(2, len(client.chat.completions.calls))

    def test_generate_includes_selected_history_and_book_settings(self):
        with tempfile.TemporaryDirectory() as root:
            manager = NovelManager(bookshelf_root=root)
            manager.create_book("book")
            manager.save_meta("book", protagonist_bio="主角设定", background_story="世界观", author_plan="作者规划")
            service = AgentChapterGenerationService(manager, FakeClient([valid_plan()]))
            request = AgentChapterRequest("book", 1, "开始", "剧情", "要求", 1000, "fake")
            plan = service.prepare(request)
            result = service.generate(request, plan)
            self.assertIn("主角设定", result.prompt)
            self.assertIn("世界观", result.prompt)
            self.assertIn("作者规划", result.prompt)
            self.assertIn("本次启用 Skills", result.prompt)

    def test_skills_can_be_disabled_for_deterministic_flow(self):
        with tempfile.TemporaryDirectory() as root:
            manager = NovelManager(bookshelf_root=root)
            manager.create_book("book")
            service = AgentChapterGenerationService(
                manager, FakeClient([valid_plan()]), skills_enabled=False
            )
            request = AgentChapterRequest("book", 1, "开始", "剧情", "要求", 1000, "fake")
            plan = service.prepare(request)
            result = service.generate(request, plan)
            self.assertEqual([], plan.selected_skills)
            self.assertNotIn("本次启用 Skills", result.prompt)
    def test_maintenance_archives_resolved_but_protects_resident(self):
        with tempfile.TemporaryDirectory() as root:
            manager = NovelManager(bookshelf_root=root)
            manager.create_book("book")
            manager.save_chapter_version("book", 1, "start", "content", version=1)
            bible = WorldBible(active_plot_threads=[
                PlotThread(id="archive", name="已完成支线", status="resolved"),
                PlotThread(id="resident", name="核心主线", status="resolved"),
                PlotThread(id="manual", name="手工保护线", status="resolved"),
            ], global_foreshadowing=[{"id": "hint", "hint": "旧伏笔", "status": "resolved"}], manual_overrides=[
                ManualOverride(id="override", operation="patch", entity_type="plot_thread", entity_id="manual", payload={})
            ])
            bible.chapter_snapshots[_chapter_world_entry_key(1, 1)] = {"data": {}}
            bible.chapter_world_entries = dict(bible.chapter_snapshots)
            manager.save_world_bible("book", bible)
            manager.get_workspace("book").save_context_policies({"resident": {"load_mode": "resident"}})
            with patch.object(manager, "rebuild_world_bible_from_active", return_value={"snapshot_count": 1}):
                result = WorldBibleMaintenanceService(manager).maintain(FakeClient([]), "book", 1, 1, model="fake")
            updated = manager.load_world_bible("book")
            self.assertEqual("completed", result.status)
            self.assertTrue(next(item for item in updated.active_plot_threads if item.id == "archive").hidden)
            self.assertFalse(next(item for item in updated.active_plot_threads if item.id == "resident").hidden)
            self.assertFalse(next(item for item in updated.active_plot_threads if item.id == "manual").hidden)
            self.assertTrue(updated.global_foreshadowing[0]["hidden"])
            with patch.object(manager, "rebuild_world_bible_from_active", return_value={"snapshot_count": 1}):
                second = WorldBibleMaintenanceService(manager).maintain(FakeClient([]), "book", 1, 1, model="fake")
            self.assertEqual("completed", second.status)
            self.assertEqual(3, len(manager.load_world_bible("book").active_plot_threads))

    def test_maintenance_failure_keeps_chapter_and_creates_retry_task(self):
        with tempfile.TemporaryDirectory() as root:
            manager = NovelManager(bookshelf_root=root)
            manager.create_book("book")
            manager.save_chapter_version("book", 1, "start", "content", version=1)
            service = WorldBibleMaintenanceService(manager)
            with patch.object(manager, "extract_world_bible_for_node", side_effect=RuntimeError("extract failed")):
                result = service.maintain(FakeClient([]), "book", 1, 1, model="fake")
            self.assertEqual("pending", result.status)
            self.assertEqual("content", manager.read_active_chapter("book", 1))
            pending = service.list_pending("book")
            self.assertEqual(result.task_id, pending[0]["task_id"])


if __name__ == "__main__":
    unittest.main()

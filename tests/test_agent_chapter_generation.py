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
        "must_happen": ["阿离亲自进入钟楼并发现暗门"],
        "may_happen": ["守卫透露午夜曾听见第二次钟声"],
        "must_not_happen": ["幕后真凶正式登场"],
        "withheld_reveals": ["午夜钟声的真正来源"],
        "end_state_requirements": ["阿离确认暗门存在并决定继续追查"],
        "scenes": [{
            "scene_id": "scene_1", "title": "进入钟楼", "purpose": "让调查从传闻进入实地取证",
            "pov_character": "阿离", "time": "午夜前", "location": "钟楼入口与底层大厅",
            "entry_state": "阿离谨慎试探，尚未获得进入许可",
            "goal": "进入钟楼检查失踪者最后出现的位置", "conflict": "守卫拒绝放行并隐瞒异常",
            "key_actions": ["观察守卫反应", "利用证物迫使守卫让步", "检查墙面磨损"],
            "information_released": ["守卫害怕午夜钟声", "墙后通道近期被使用过"],
            "turning_point": "钟响令守卫失神并暴露暗门方向",
            "choice": "阿离暂不追问守卫，先进入暗门",
            "cost": "失去当场逼问口供的机会并暴露调查意图",
            "outcome": "阿离发现并打开暗门", "exit_state": "阿离进入未知通道，守卫态度不明",
            "irreversible_change": "秘密通道从猜测变成阿离亲眼确认的事实",
            "target_words": 1000, "forbidden": ["揭示暗门后的最终真相"],
        }],
        "character_arcs": [{"character": "阿离", "start_state": "犹豫", "end_state": "决定追查", "trigger": "守卫的异常反应", "choice": "独自进入暗门", "cost": "暴露调查意图"}],
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
            self.assertEqual(3000, plan.scenes[0]["target_words"])
            self.assertEqual("阿离", plan.scenes[0]["pov_character"])
            self.assertIn("不可逆变化", plan.render())
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

    def test_contract_requires_verifiable_scene_fields(self):
        with tempfile.TemporaryDirectory() as root:
            manager = NovelManager(bookshelf_root=root)
            manager.create_book("book")
            incomplete = valid_plan()
            incomplete["scenes"] = [{"title": "只有标题"}]
            client = FakeClient([incomplete, valid_plan()])
            plan = AgentChapterGenerationService(manager, client).prepare(
                AgentChapterRequest("book", 1, "开始", "调查钟楼", "", 1800, "fake")
            )
            self.assertEqual(2, len(client.chat.completions.calls))
            self.assertEqual(1800, sum(scene["target_words"] for scene in plan.scenes))
            self.assertTrue(plan.must_happen)
            self.assertTrue(plan.end_state_requirements)

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

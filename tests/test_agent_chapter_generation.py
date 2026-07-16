import copy
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
    def test_prepare_compares_three_distinct_candidate_plans(self):
        with tempfile.TemporaryDirectory() as root:
            manager = NovelManager(bookshelf_root=root)
            manager.create_book("book")
            first, second, third = copy.deepcopy(valid_plan()), copy.deepcopy(valid_plan()), copy.deepcopy(valid_plan())
            for index, item in enumerate((first, second, third), 1):
                item["chapter_goal"] = f"方案{index}的章节目标"
                item["scenes"][0]["scene_id"] = f"scene_{index}"
                item["scenes"][0]["choice"] = f"方案{index}的主动选择"
                item["scenes"][0]["irreversible_change"] = f"方案{index}造成不可逆主线变化"
            payload = {
                "options": [
                    {"option_id": "choice", "strategy": "人物选择驱动", "summary": "主动抉择", "plan": first},
                    {"option_id": "event", "strategy": "外部事件驱动", "summary": "危机突发", "plan": second},
                    {"option_id": "reveal", "strategy": "信息揭示驱动", "summary": "真相反转", "plan": third},
                ]
            }
            critic = {
                "recommended_option_id": "event",
                "recommendation_reason": "外部危机能抬升当前主线压力。",
                "evaluations": [
                    {"option_id": "choice", "causality": 8, "character_agency": 9, "surprise": 6, "main_plot_value": 7, "reason": "", "risk": ""},
                    {"option_id": "event", "causality": 9, "character_agency": 8, "surprise": 8, "main_plot_value": 9, "reason": "推进有效", "risk": "节奏偏快"},
                    {"option_id": "reveal", "causality": 7, "character_agency": 7, "surprise": 9, "main_plot_value": 8, "reason": "", "risk": ""},
                ],
            }
            plan = AgentChapterGenerationService(
                manager, FakeClient([payload, critic]), skills_enabled=False,
                multi_plan_enabled=True,
            ).prepare(AgentChapterRequest("book", 1, "开始", "", "", 1200, "fake"))
            self.assertEqual("event", plan.candidate_id)
            self.assertEqual(3, len(plan.candidate_plans))
            self.assertEqual("外部事件驱动", plan.strategy)
            self.assertEqual(9, plan.critic["main_plot_value"])

    def test_revise_plan_updates_current_plan_without_replanning_candidates(self):
        with tempfile.TemporaryDirectory() as root:
            manager = NovelManager(bookshelf_root=root)
            manager.create_book("book")
            request = AgentChapterRequest("book", 1, "开始", "", "", 1000, "fake")
            plan = AgentChapterGenerationService(
                manager, FakeClient([valid_plan()]), skills_enabled=False
            ).prepare(request)
            revised_payload = copy.deepcopy(valid_plan())
            revised_payload["chapter_goal"] = "主角主动隐瞒线索并承担同伴不信任的代价"
            revised_payload["scenes"][0]["choice"] = "向同伴隐瞒暗门线索"
            revised_payload["scenes"][0]["cost"] = "同伴发现异常后不再完全信任主角"
            client = FakeClient([revised_payload])
            revised = AgentChapterGenerationService(
                manager, client, skills_enabled=False
            ).revise_plan(request=request, current_plan=plan, instruction="修改主角选择并补上代价")

            self.assertEqual(plan.plan_id, revised.plan_id)
            self.assertEqual(plan.candidate_id, revised.candidate_id)
            self.assertIn("主动隐瞒线索", revised.chapter_goal)
            self.assertEqual(1, len(revised.candidate_plans))
            prompt = client.chat.completions.calls[0]["messages"][0]["content"]
            self.assertIn("不得重新构思整章", prompt)
            workspace = manager.get_workspace("book")
            record = workspace.storage.read_json(
                f"{workspace.agent_root}/chapter_runs/{plan.plan_id}.json"
            )
            self.assertEqual("plan_revised", record["status"])
            self.assertEqual(1, len(record["plan_revision_history"]))

            AgentChapterGenerationService(
                manager, FakeClient([]), skills_enabled=False
            ).generate(request, revised)
            approved_record = workspace.storage.read_json(
                f"{workspace.agent_root}/chapter_runs/{plan.plan_id}.json"
            )
            self.assertEqual("approved", approved_record["status"])
            self.assertEqual(1, len(approved_record["plan_revision_history"]))

    def test_story_director_persists_chapter_progress(self):
        with tempfile.TemporaryDirectory() as root:
            manager = NovelManager(bookshelf_root=root)
            manager.create_book("book")
            director = {
                "current_volume_goal": "逼近钟楼真相",
                "protagonist_stage_goal": "取得守卫口供",
                "core_conflict_pressure": "中高",
                "recent_major_choice": "进入暗门",
                "recent_failure_or_cost": "暴露调查意图",
                "unredeemed_promises": ["午夜钟声来源"],
                "next_turn_distance": 2,
                "foreshadowing_density_risks": ["钟声伏笔过密"],
                "chapters_without_main_progress": 0,
            }
            client = FakeClient([valid_plan(), director])
            service = AgentChapterGenerationService(manager, client, skills_enabled=False)
            request = AgentChapterRequest("book", 1, "开始", "", "", 1000, "fake")
            plan = service.prepare(request)
            result = service.update_director_state(request, plan, "阿离进入钟楼暗门。", "fake")
            self.assertTrue(result["reviewed"])
            workspace = manager.get_workspace("book")
            saved = workspace.storage.read_json(f"{workspace.agent_root}/story_director.json")
            self.assertEqual("逼近钟楼真相", saved["current_volume_goal"])
            self.assertEqual(1, saved["last_review_chapter"])

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

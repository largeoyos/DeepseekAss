import json
import tempfile
import time
import unittest

from core.agent.advisor import FICTION_CONTEXT_PREFIX, WritingAdvisorService
from core.agent.continuation import AgentContinuationService, SegmentationResult
from core.agent.changes import ChangeSetService
from core.agent.domain_tools import build_domain_tool_registry
from core.agent.profiles import get_agent_profile
from core.agent.repository import AgentRepository
from core.agent.skills import HUMANIZER_ZH_STYLE_BRIEF, SkillService
from core.agent.tools import ToolContext, ToolRegistry, ToolSpec
from core.agent.types import ToolCallRequest
from core.agent.web_search import WebSearchClient, WebSearchConfig
from core.novel_manager import NovelManager
from core.world_bible import ManualOverride, WorldBible, apply_manual_overrides
from ui.continuation_dialogs import SectionPreviewDialog, suggest_directions



class _FakeContinuationMessage:
    def __init__(self, content):
        self.content = content


class _FakeContinuationChoice:
    def __init__(self, content):
        self.message = _FakeContinuationMessage(content)


class _FakeContinuationCompletions:
    def __init__(self, content):
        self.outputs = list(content) if isinstance(content, (list, tuple)) else [content]
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        value = self.outputs[min(len(self.calls) - 1, len(self.outputs) - 1)]
        return type("Resp", (), {"choices": [_FakeContinuationChoice(value)]})()

class _FakeContinuationClient:
    def __init__(self, content):
        self.chat = type("Chat", (), {"completions": _FakeContinuationCompletions(content)})()

class ExtendedAgentTests(unittest.TestCase):
    def test_new_agent_profiles_and_tools_are_registered(self):
        with tempfile.TemporaryDirectory() as root:
            manager = NovelManager(bookshelf_root=root)
            manager.create_book("book")
            registry = build_domain_tool_registry(manager)
            advisor = get_agent_profile("writing_advisor")
            supervisor = get_agent_profile("chapter_supervisor")
            world = get_agent_profile("world_bible_manager")
            self.assertIn("chapter.read_node", advisor.allowed_tools)
            self.assertIn("project.active_state", supervisor.allowed_tools)
            self.assertIn("world_bible.propose_patch", world.allowed_tools)
            names = {item["function"]["name"] for item in registry.schemas_for(advisor.allowed_tools)}
            self.assertIn("chapter.summary_search", names)
            self.assertIn("agent.save_advice", names)
            self.assertNotIn("web.search", names)

    def test_web_search_rejects_private_endpoint(self):
        config = WebSearchConfig(enabled=True, endpoint="https://127.0.0.1/search")
        from core.agent.web_search import WebSearchError
        with self.assertRaises(WebSearchError):
            WebSearchClient(config).search("test")

    def test_web_search_tool_is_hidden_until_configured(self):
        with tempfile.TemporaryDirectory() as root:
            manager = NovelManager(bookshelf_root=root)
            manager.create_book("book")
            disabled = build_domain_tool_registry(manager, web_search_config={"agent_web_enabled": False})
            self.assertEqual([], disabled.schemas_for(["web.search"]))
            enabled = build_domain_tool_registry(manager, web_search_config={
                "agent_web_enabled": True,
                "agent_web_endpoint": "https://search.example.com/api",
            })
            self.assertEqual("web.search", enabled.schemas_for(["web.search"])[0]["function"]["name"])

    def test_agent_continuation_segments_losslessly_and_records_run(self):
        with tempfile.TemporaryDirectory() as root:
            manager = NovelManager(bookshelf_root=root)
            manager.create_book("book")
            first = "第一段正文：" + "甲" * 30
            second = "第二段正文：" + "乙" * 30
            source = first + "\n\n" + second
            client = _FakeContinuationClient(json.dumps([
                {"title": "开端", "start_quote": ""},
                {"title": "转折", "start_quote": second[:20]},
            ], ensure_ascii=False))
            service = AgentContinuationService(manager, client)
            report = service.segment_text_with_report(source, "model-x", book_title="book")

            self.assertEqual(source, "".join(content for _, content in report.sections))
            self.assertEqual(1, report.agent_chunks)
            self.assertEqual(0, report.fallback_chunks)
            self.assertTrue(report.selected_skills)
            workspace = manager.get_workspace("book")
            runs = workspace.storage.list_files(f"{workspace.agent_root}/continuation_runs")
            self.assertTrue(any(path.endswith(".json") for path in runs))
            record = workspace.storage.read_json(runs[0])
            self.assertEqual(len(source), record["output_summary"]["covered_chars"])
            self.assertEqual(1, record["output_summary"]["chunks_total"])
            self.assertIn("续写导入分段 Agent", client.chat.completions.calls[0]["messages"][0]["content"])

    def test_agent_continuation_preserves_all_whitespace_from_anchor_slices(self):
        first = "\n  第一幕发生在车站。" + "甲" * 30
        second = "第二幕转到雨夜的小巷。" + "乙" * 30
        third = "第三幕回到清晨的旅馆。" + "丙" * 30 + "\n"
        source = first + "\n\n" + second + "\n\n" + third
        client = _FakeContinuationClient(json.dumps([
            {"title": "车站", "start_quote": ""},
            {"title": "雨夜", "start_quote": second[:20]},
            {"title": "清晨", "start_quote": third[:20]},
        ], ensure_ascii=False))

        sections = AgentContinuationService(client=client).segment_text(source, "model-x")

        self.assertEqual(source, "".join(content for _, content in sections))
        self.assertTrue(sections[0][1].startswith("\n  "))
        self.assertTrue(sections[-1][1].endswith("\n"))
        prompt = client.chat.completions.calls[0]["messages"][0]["content"]
        self.assertIn("start_quote", prompt)
        self.assertIn("绝不输出 content", prompt)

    def test_agent_continuation_rejects_invalid_anchor_payloads(self):
        source = "第一段开场。" + "甲" * 30 + "\n\n" + "第二段转折。" + "乙" * 30
        service = AgentContinuationService(client=None)
        valid_anchor = "第二段转折。" + "乙" * 14
        invalid_payloads = [
            '{"title": "非数组"}',
            json.dumps([{"title": "改写", "content": "模型正文"}], ensure_ascii=False),
            json.dumps([{"title": "", "start_quote": ""}], ensure_ascii=False),
            json.dumps([{"title": "开端", "start_quote": ""}, {"title": "太短", "start_quote": "短"}], ensure_ascii=False),
            json.dumps([{"title": "开端", "start_quote": ""}, {"title": "不存在", "start_quote": "不在原文中的连续锚点" * 2}], ensure_ascii=False),
            json.dumps([{"title": "开端", "start_quote": ""}, {"title": "转折", "start_quote": valid_anchor}, {"title": "重复", "start_quote": valid_anchor}], ensure_ascii=False),
            json.dumps([{"title": "开端", "start_quote": ""}, {"title": "后段", "start_quote": valid_anchor}, {"title": "乱序", "start_quote": source[:20]}], ensure_ascii=False),
        ]

        for payload in invalid_payloads:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                service._parse_sections(payload, source)

    def test_agent_continuation_retries_invalid_json_once_then_succeeds(self):
        first = "第一段正文：" + "甲" * 30
        second = "第二段正文：" + "乙" * 30
        source = first + "\n\n" + second
        repaired = json.dumps([
            {"title": "开端", "start_quote": ""},
            {"title": "转折", "start_quote": second[:20]},
        ], ensure_ascii=False)
        client = _FakeContinuationClient(["not-json", repaired])

        report = AgentContinuationService(client=client).segment_text_with_report(source, "model-x")

        self.assertEqual(source, "".join(content for _, content in report.sections))
        self.assertEqual(1, report.repair_attempts)
        self.assertEqual(1, report.agent_chunks)
        self.assertEqual(0, report.fallback_chunks)
        self.assertIn("上次输出无效", client.chat.completions.calls[1]["messages"][0]["content"])

    def test_agent_continuation_rejects_model_body_and_falls_back_losslessly(self):
        source = "第一段正文。\n\n第二段正文。\n\n第三段正文。"
        client = _FakeContinuationClient(json.dumps([
            {"title": "开端", "content": "模型改写正文"},
        ], ensure_ascii=False))

        report = AgentContinuationService(client=client).segment_text_with_report(source, "model-x")

        self.assertTrue(report.used_fallback)
        self.assertEqual(1, report.fallback_chunks)
        self.assertEqual(1, report.repair_attempts)
        self.assertEqual(source, "".join(content for _, content in report.sections))
        self.assertTrue(report.errors)

    def test_agent_continuation_chunks_long_text_without_losing_tail(self):
        source = "甲" * 39000 + "\n\n" + "乙" * 39000 + "\n\n" + "丙" * 5000
        client = _FakeContinuationClient(json.dumps([
            {"title": "原文块", "start_quote": ""},
        ], ensure_ascii=False))

        report = AgentContinuationService(client=client).segment_text_with_report(source, "model-x")

        self.assertGreaterEqual(report.chunks_total, 3)
        self.assertEqual(report.chunks_total, report.agent_chunks)
        self.assertEqual(0, report.fallback_chunks)
        self.assertEqual(report.chunks_total, len(client.chat.completions.calls))
        self.assertEqual(source, "".join(content for _, content in report.sections))
        self.assertTrue(report.sections[-1][1].endswith("丙" * 5000))

    def test_agent_continuation_falls_back_only_for_invalid_chunk(self):
        source = "甲" * 39000 + "\n\n" + "乙" * 39000 + "\n\n" + "丙" * 5000
        valid = json.dumps([{"title": "原文块", "start_quote": ""}], ensure_ascii=False)
        invalid = json.dumps([{"title": "改写", "content": "模型正文"}], ensure_ascii=False)
        client = _FakeContinuationClient([valid, invalid, invalid, valid])

        report = AgentContinuationService(client=client).segment_text_with_report(source, "model-x")

        self.assertGreaterEqual(report.chunks_total, 3)
        self.assertEqual(report.chunks_total - 1, report.agent_chunks)
        self.assertEqual(1, report.fallback_chunks)
        self.assertEqual(1, report.repair_attempts)
        self.assertEqual(source, "".join(content for _, content in report.sections))

    def test_preview_status_supports_report_and_legacy_sections(self):
        sections = [("全文", "保留原文")]
        report = SegmentationResult(
            sections=sections,
            total_chars=4,
            covered_chars=4,
            chunks_total=2,
            agent_chunks=1,
            fallback_chunks=1,
            repair_attempts=1,
            errors=["第 2/2 块：格式无效"],
            selected_skills=[{"id": "chapter-continuation"}],
        )
        unpacked, unpacked_report = SectionPreviewDialog._unpack_segmenter_output(report)
        legacy, legacy_report = SectionPreviewDialog._unpack_segmenter_output(sections)
        status = SectionPreviewDialog._format_segmentation_status(SectionPreviewDialog, unpacked, unpacked_report)

        self.assertEqual(sections, unpacked)
        self.assertIs(report, unpacked_report)
        self.assertEqual(sections, legacy)
        self.assertIsNone(legacy_report)
        self.assertIn("本地回退", status)
        self.assertIn("覆盖率 100%", status)
        self.assertIn("Skills：chapter-continuation", status)
    def test_agent_direction_uses_expanded_context_limits(self):
        world_tail = "世界尾标记"
        background_tail = "背景尾标记"
        plot_tail = "剧情尾标记"
        world_data = {
            "characters": [{
                "name": "主角",
                "relationships": [{"type": "牵绊", "target": "关系信息" * 800 + world_tail}],
            }],
        }
        setting = "背景信息" * 1490 + background_tail
        plot = "剧情信息" * 1590 + plot_tail
        requirement = "必须保持第一人称，并以克制的节奏描写雨夜。"
        requested_plot = "主角必须在旧书店找到信件，并与店主发生简短对话。"
        client = _FakeContinuationClient("方向1：继续追查 | 紧张感 | 主角循线索推进真相")

        directions = AgentContinuationService(client=client).suggest_directions(
            setting,
            plot,
            "model-x",
            world_data=world_data,
            continuation_requirement=requirement,
            requested_plot=requested_plot,
        )

        prompt = client.chat.completions.calls[0]["messages"][0]["content"]
        self.assertEqual(["方向1：继续追查 | 紧张感 | 主角循线索推进真相"], directions)
        self.assertIn(world_tail, prompt)
        self.assertIn(background_tail, prompt)
        self.assertIn(plot_tail, prompt)
        self.assertIn(requirement, prompt)
        self.assertIn(requested_plot, prompt)
    def test_legacy_direction_uses_continuation_requirement_and_plot(self):
        requirement = "必须使用第三人称，避免新增冲突。"
        requested_plot = "两位角色在早餐桌上和解，并提及昨夜的误会。"
        client = _FakeContinuationClient("方向1：早餐和解 | 温暖克制 | 用对话化解误会")

        directions = suggest_directions(
            client,
            "小镇日常",
            "两人昨夜发生误会。",
            "model-x",
            continuation_requirement=requirement,
            requested_plot=requested_plot,
        )

        prompt = client.chat.completions.calls[0]["messages"][0]["content"]
        self.assertEqual(["方向1：早餐和解 | 温暖克制 | 用对话化解误会"], directions)
        self.assertIn("【本次续写要求（必须遵守）】", prompt)
        self.assertIn(requirement, prompt)
        self.assertIn("【用户指定续写剧情（必须作为方向约束）】", prompt)
        self.assertIn(requested_plot, prompt)
    def test_saved_advice_artifacts_are_listed_for_library(self):
        with tempfile.TemporaryDirectory() as root:
            manager = NovelManager(bookshelf_root=root)
            manager.create_book("book")
            service = WritingAdvisorService(manager, client=None)
            first = service.save_advice("book", "run-1", "第一条构思", title="下章冲突")
            second = service.save_advice("book", "run-2", "第二条构思", title="城市细节")
            artifacts = service.list_advice("book")
            self.assertEqual({first, second}, {item["artifact_id"] for item in artifacts})
            self.assertTrue(all(item["kind"] == "writing_advice" for item in artifacts))
            self.assertIn("content", artifacts[0])

    def test_advisor_fiction_wrapper_and_history_management(self):
        with tempfile.TemporaryDirectory() as root:
            manager = NovelManager(bookshelf_root=root)
            manager.create_book("book")
            service = WritingAdvisorService(manager, client=None)
            repository = AgentRepository(manager.get_workspace("book"))
            manifest = manager.ensure_workspace("book")
            session = repository.create_session(
                manifest.book_id, "book", "writing_advisor", "写作顾问"
            )
            wrapped = service.wrap_fiction_request("分析这段虚构冲突")
            self.assertTrue(wrapped.startswith(FICTION_CONTEXT_PREFIX))
            session.messages = [
                {"role": "user", "content": wrapped, "at": "2026-01-01T00:00:00"},
                {"role": "assistant", "content": "顾问回答", "at": "2026-01-01T00:00:01"},
            ]
            session.epochs = [{"summary": "旧压缩上下文"}]
            repository.save_session(session)

            history = service.list_history("book")
            self.assertEqual("分析这段虚构冲突", history[0]["content"])
            self.assertTrue(service.delete_history_message("book", 0))
            self.assertEqual(["顾问回答"], [item["content"] for item in service.list_history("book")])
            self.assertEqual(1, service.clear_history("book"))
            saved = repository.load_session(session.session_id)
            self.assertEqual([], saved.messages)
            self.assertEqual([], saved.epochs)
    def test_tool_timeout_returns_failure_without_waiting_for_completion(self):
        registry = ToolRegistry()
        registry.register(ToolSpec(
            "slow", "slow", {"type": "object", "properties": {}},
            lambda _ctx, _args: time.sleep(0.2), timeout_seconds=0.02,
        ))
        context = ToolContext("run", "book", "book", "writing_advisor", "read_only", object())
        started = time.monotonic()
        result = registry.execute(ToolCallRequest("1", "slow", {}), context, ["slow"])
        self.assertFalse(result.success)
        self.assertEqual("tool_failed", result.error_code)
        self.assertIn("timed out", result.content)
        self.assertLess(time.monotonic() - started, 0.15)

    def test_world_patch_requires_approval_and_applies_field_change(self):
        with tempfile.TemporaryDirectory() as root:
            manager = NovelManager(bookshelf_root=root)
            manager.create_book("book")
            manifest = manager.ensure_workspace("book")
            repository = AgentRepository(manager.get_workspace("book"))
            service = ChangeSetService(manager, "book", repository)
            change = service.propose_world_patch("run", manifest.book_id, [{
                "operation": "entity.create",
                "entity_type": "character",
                "entity_id": "character_test",
                "payload": {"id": "character_test", "name": "测试角色", "traits": "谨慎"},
            }])
            self.assertEqual("pending", change.status)
            self.assertFalse(manager.load_world_bible("book").characters)
            service.approve(change.change_set_id)
            bible = manager.load_world_bible("book")
            self.assertEqual("测试角色", bible.characters[0].name)
            self.assertTrue(any(item.entity_id == "character_test" for item in bible.manual_overrides))
            self.assertTrue(manager.snapshot_service("book").list())

    def test_scoped_overrides_follow_active_path(self):
        bible = WorldBible(manual_overrides=[
            ManualOverride(
                id="global", operation="add", entity_type="character", entity_id="global_char",
                payload={"id": "global_char", "name": "全局角色"}, scope="global",
            ),
            ManualOverride(
                id="branch", operation="add", entity_type="character", entity_id="branch_char",
                payload={"id": "branch_char", "name": "分支角色"}, scope="branch", anchor_node_id="ch0002_v001",
            ),
            ManualOverride(
                id="chapter", operation="add", entity_type="character", entity_id="chapter_char",
                payload={"id": "chapter_char", "name": "章节角色"}, scope="chapter", anchor_node_id="ch0002_v001",
            ),
        ])
        apply_manual_overrides(bible, ["ch0001_v001", "ch0002_v001"], "ch0002_v001")
        self.assertEqual({"global_char", "branch_char", "chapter_char"}, {item.id for item in bible.characters})

        other_branch = WorldBible(manual_overrides=bible.manual_overrides)
        apply_manual_overrides(other_branch, ["ch0001_v001", "ch0002_v002"], "ch0002_v002")
        self.assertEqual({"global_char"}, {item.id for item in other_branch.characters})

        later_chapter = WorldBible(manual_overrides=bible.manual_overrides)
        apply_manual_overrides(later_chapter, ["ch0001_v001", "ch0002_v001", "ch0003_v001"], "ch0003_v001")
        self.assertEqual({"global_char", "branch_char"}, {item.id for item in later_chapter.characters})

    def test_branch_scoped_changes_disappear_on_sibling_branch(self):
        with tempfile.TemporaryDirectory() as root:
            manager = NovelManager(bookshelf_root=root)
            manager.create_book("book")
            manager.save_chapter_version("book", 1, "root", "one", version=1)
            root_id = manager._node_id(1, 1)
            manager.save_chapter_version("book", 2, "branch-a", "two-a", version=1, parent_id=root_id)
            manager.save_chapter_version("book", 2, "branch-b", "two-b", version=2, parent_id=root_id)
            branch_a = manager._node_id(2, 1)
            branch_b = manager._node_id(2, 2)
            manager.switch_active_node("book", branch_a)
            manifest = manager.ensure_workspace("book")
            repository = AgentRepository(manager.get_workspace("book"))
            service = ChangeSetService(manager, "book", repository)
            change = service.propose_world_patch("run", manifest.book_id, [{
                "operation": "entity.create",
                "entity_type": "character",
                "entity_id": "branch_only",
                "payload": {"id": "branch_only", "name": "仅分支A"},
                "scope": "branch",
                "anchor_node_id": branch_a,
                "scope_reason": "该角色只在分支A登场",
            }])
            service.approve(change.change_set_id)
            self.assertTrue(any(item.id == "branch_only" for item in manager.load_world_bible("book").characters))

            manager.switch_active_node("book", branch_b)
            manager.rebuild_world_bible_from_active(None, "book")
            self.assertFalse(any(item.id == "branch_only" for item in manager.load_world_bible("book").characters))
    def test_new_agents_receive_matching_skills(self):
        with tempfile.TemporaryDirectory() as root:
            manager = NovelManager(bookshelf_root=root)
            manager.create_book("book")
            skills = SkillService(AgentRepository(manager.get_workspace("book")))
            supervisor = {item.skill_id for item in skills.select_for_task(
                "chapter_supervision", "chapter_supervisor", "连续性和场景节奏"
            ).documents}
            world = {item.skill_id for item in skills.select_for_task(
                "world_bible_management", "world_bible_manager", "世界书伏笔"
            ).documents}
            continuation = {item.skill_id for item in skills.select_for_task(
                "continuation_segmentation", "writing_orchestrator", "续写分段和长篇上下文"
            ).documents}
            chapter = {item.skill_id for item in skills.select_for_task(
                "chapter_generation", "writing_orchestrator", "去AI腔，描写自然"
            ).documents}
            polish = {item.skill_id for item in skills.select_for_task(
                "chapter_polish", "writing_orchestrator", "润色去AI腔，不改剧情"
            ).documents}
            self.assertIn("continuity-review", supervisor)
            self.assertIn("world-bible-maintenance", world)
            self.assertIn("foreshadowing", world)
            self.assertIn("chapter-continuation", continuation)
            self.assertIn("humanizer-zh", supervisor)
            self.assertIn("humanizer-zh", chapter)
            self.assertIn("humanizer-zh", polish)

    def test_humanizer_style_brief_blocks_common_ai_patterns(self):
        self.assertIn("不是", HUMANIZER_ZH_STYLE_BRIEF)
        self.assertIn("不仅", HUMANIZER_ZH_STYLE_BRIEF)
        self.assertIn("描写要多样化", HUMANIZER_ZH_STYLE_BRIEF)


if __name__ == "__main__":
    unittest.main()

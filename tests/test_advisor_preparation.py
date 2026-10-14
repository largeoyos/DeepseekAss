import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from core.agent.advisor import AdvisorRequest, WritingAdvisorService
from core.agent.context import AgentContextAssembler
from core.agent.repository import AgentRepository
from core.agent.types import AgentRunRequest
from core.novel_manager import NovelManager, NovelMeta
from core.world_bible import CharacterEntry, WorldBible
from tests.test_agent_runtime import FakeClient, FakeCompletions, response


class AdvisorContextTests(unittest.TestCase):
    def manager(self, **fields):
        return SimpleNamespace(load_meta=Mock(return_value=NovelMeta(title="book", **fields)),
                               get_workspace=Mock(return_value=SimpleNamespace(load_context_policies=lambda: {})),
                               context_assembler=Mock(side_effect=AssertionError("heavy pipeline")),
                               retrieval_backend=Mock(side_effect=AssertionError("embedding search")),
                               load_world_bible=Mock(side_effect=AssertionError("unrequested world load")))

    def test_small_question_has_bounded_local_context_without_full_book_reads(self):
        manager = self.manager(protagonist_bio="人物" * 10000, background_story="背景" * 10000,
                               writing_demand="要求" * 10000, author_plan="规划" * 10000)
        report = AgentContextAssembler(manager).assemble_advisor("book", "给一个城市起名")
        self.assertLessEqual(len(report.content), 6000)
        self.assertGreater(report.omitted_chars, 0)
        self.assertEqual(len(report.content), report.total_chars)
        self.assertIn("尚未发生", report.content)
        manager.load_meta.assert_called_once_with("book")
        manager.context_assembler.assert_not_called()
        manager.retrieval_backend.assert_not_called()
        manager.load_world_bible.assert_not_called()

    def test_question_selects_existing_summaries_only_on_active_branch(self):
        manager = self.manager(
            active_path=["root", "a", "b", "c"],
            chapter_nodes={
                "root": {"virtual": True, "summary": "virtual"},
                "a": {"chapter_num": 1, "title": "出发", "summary": "第一章有效摘要"},
                "b": {"chapter_num": 2, "title": "渡河", "summary": "第二章有效摘要"},
                "c": {"chapter_num": 3, "title": "入城", "summary": "第三章有效摘要"},
                "old": {"chapter_num": 1, "title": "出发", "summary": "非活跃版本事实"},
            },
        )
        assembler = AgentContextAssembler(manager)
        for question in ("分析第一章动机", "分析第 1 章", "Analyze chapter 1", "出发这章怎么改"):
            with self.subTest(question=question):
                report = assembler.assemble_advisor("book", question)
                self.assertIn("第一章有效摘要", report.content)
                self.assertNotIn("第二章有效摘要", report.content)
                self.assertNotIn("非活跃版本事实", report.content)
        recent = assembler.assemble_advisor("book", "接下来怎么写")
        self.assertNotIn("第一章有效摘要", recent.content)
        self.assertIn("第二章有效摘要", recent.content)
        self.assertIn("第三章有效摘要", recent.content)
        general = assembler.assemble_advisor("book", "如何写好对话")
        self.assertFalse(any(s["source"] == "chapter_summary" for s in general.sources))

    def test_manual_references_survive_and_disabled_or_hidden_entries_stay_hidden(self):
        manager = self.manager()
        manager.load_world_bible = Mock(return_value=WorldBible(characters=[
            CharacterEntry(id="hero", name="林舟", current_goal="找到妹妹"),
            CharacterEntry(id="hidden", name="不可展开", hidden=True),
            CharacterEntry(id="disabled", name="策略禁用"),
        ]))
        manager.get_workspace = Mock(return_value=SimpleNamespace(
            load_context_policies=lambda: {"disabled": {"enabled": False}}))
        report = AgentContextAssembler(manager).assemble_advisor(
            "book", "分析动机", ["hero", "hidden", "disabled", "作者指定：对比第五章结尾"],
        )
        self.assertIn("作者指定：对比第五章结尾", report.content)
        self.assertIn("林舟", report.content)
        self.assertIn("找到妹妹", report.content)
        self.assertNotIn("不可展开", report.content)
        self.assertNotIn("策略禁用", report.content)
        manager.retrieval_backend.assert_not_called()

    def test_other_agents_keep_their_existing_context_pipeline(self):
        assembler = AgentContextAssembler(self.manager())
        request = AgentRunRequest("b", "s", "chapter_supervisor", "审查", ["hero"], book_title="book")
        with patch.object(assembler, "assemble", return_value="unchanged") as full:
            self.assertEqual("unchanged", assembler.assemble_for_request(request))
            full.assert_called_once_with("book", ["hero"])

    def test_resident_policies_still_load_pinned_facts_without_search(self):
        manager = self.manager()
        manager.get_workspace.return_value.load_context_policies = lambda: {
            "hero": {"load_mode": "resident"}, "unrelated": {"load_mode": "auto"},
        }
        manager.load_world_bible = Mock(return_value=WorldBible(characters=[
            CharacterEntry(id="hero", name="林舟", current_goal="不能背叛同伴"),
            CharacterEntry(id="unrelated", name="路人设定"),
        ]))
        report = AgentContextAssembler(manager).assemble_advisor("book", "帮我构思一个冲突")
        self.assertIn("不能背叛同伴", report.content)
        self.assertNotIn("路人设定", report.content)
        manager.load_world_bible.assert_called_once_with("book")
        manager.retrieval_backend.assert_not_called()


class AdvisorPreparationIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.manager = NovelManager(self.tmp.name)
        self.manager.create_book("book")
        self.repo = AgentRepository(self.manager.get_workspace("book"))

    def request(self, text="给一座新城市起名", backend="legacy", references=None):
        return AdvisorRequest("book", text, "fake", settings={
            "agent_runtime_backend": backend, "agent_skills_enabled": False,
        }, manual_references=references or [])

    def test_simple_question_can_complete_in_one_model_turn_on_both_backends(self):
        for backend in ("legacy", "langgraph"):
            with self.subTest(backend=backend):
                calls = FakeCompletions([response("可以叫作雾港。")])
                client = FakeClient(calls)
                service = WritingAdvisorService(self.manager, client)
                # Neither a full chapter context nor an embedding request should
                # precede the model call, even when retrieval is configured.
                with patch.object(self.manager, "context_assembler", side_effect=AssertionError("heavy context")), \
                     patch.object(self.manager, "retrieval_backend", side_effect=AssertionError("retrieval")):
                    result = service.ask(self.request(backend=backend))
                self.assertEqual("completed", result.status, result.error)
                self.assertEqual("", result.error)
                self.assertEqual("可以叫作雾港。", result.answer)
                self.assertEqual(1, len(calls.calls))
                self.assertNotEqual("required", calls.calls[0].get("tool_choice"))
                run = self.repo.load_run(result.run_id)
                self.assertEqual([], run.tool_calls)
                ledger = self.repo.storage.read_json(f"{self.repo.root}/ledger/{result.run_id}.json")
                ready = next(e for e in ledger["events"] if e["event_type"] == "context_ready")
                self.assertLessEqual(ready["payload"]["chars"], 6000)
                self.assertGreaterEqual(ready["payload"]["duration_ms"], 0)

    def test_needed_tools_still_execute_before_final_answer(self):
        self.manager.save_chapter_version("book", 1, "出发", "林舟拿走了银钥匙。")
        calls = FakeCompletions([
            response(tool_calls=[("chapter.read", {"chapter_num": 1})]),
            response("林舟拿走的是银钥匙，依据第一章正文。"),
        ])
        result = WritingAdvisorService(self.manager, FakeClient(calls)).ask(self.request("第一章拿走了什么？"))
        self.assertEqual("completed", result.status, result.error)
        self.assertEqual(2, len(calls.calls))
        self.assertIn("银钥匙", calls.calls[1]["messages"][-1]["content"])
        self.assertEqual("chapter.read", result.tool_calls[0]["request"]["tool_name"])

    def test_session_lookup_is_indexed_and_history_remains_editable(self):
        calls = FakeCompletions([response("雾港"), response("保留第一个名字"), response("新建议")])
        service = WritingAdvisorService(self.manager, FakeClient(calls))
        first = service.ask(self.request())
        with patch.object(AgentRepository, "list_sessions", side_effect=AssertionError("full session scan")):
            second = service.ask(self.request("采用第一个，接着想城门名字"))
            self.assertEqual(first.session_id, second.session_id)
            history = calls.calls[1]["messages"]
            self.assertTrue(any(m["role"] == "assistant" and m["content"] == "雾港" for m in history))
            self.assertEqual(4, len(service.list_history("book")))
            self.assertEqual(4, service.clear_history("book"))
            service.ask(self.request("重新起名"))
            self.assertFalse(any(m["role"] == "assistant" for m in calls.calls[2]["messages"]))

    def test_stale_session_index_falls_back_without_crossing_agent_kinds(self):
        book_id = self.manager.ensure_workspace("book").book_id
        advisor = self.repo.create_session(book_id, "book", "writing_advisor", "顾问")
        other = self.repo.create_session(book_id, "book", "world_bible_manager", "世界书")
        path = f"{self.repo.root}/advisor_session.json"
        for session_id in ("session_missing", other.session_id):
            with self.subTest(session_id=session_id):
                self.repo.storage.write_json(path, {"session_id": session_id})
                restored = WritingAdvisorService._existing_session(self.repo, book_id)
                self.assertEqual(advisor.session_id, restored.session_id)
                self.assertEqual(advisor.session_id, self.repo.storage.read_json(path)["session_id"])

    def test_corrupt_index_is_rebuilt_from_existing_sessions(self):
        book_id = self.manager.ensure_workspace("book").book_id
        advisor = self.repo.create_session(book_id, "book", "writing_advisor", "顾问")
        path = f"{self.repo.root}/advisor_session.json"
        self.repo.storage.write_text(path, "{broken")
        restored = WritingAdvisorService._existing_session(self.repo, book_id)
        self.assertEqual(advisor.session_id, restored.session_id)
        self.assertEqual(advisor.session_id, self.repo.storage.read_json(path)["session_id"])


if __name__ == "__main__":
    unittest.main()

import json
import os
import tempfile
import unittest
from types import SimpleNamespace as NS
from unittest.mock import Mock

from core.agent.advisor import AdvisorRequest, AdvisorResult, WritingAdvisorService
from core.agent.model import AgentModelAdapter
from core.agent.repository import AgentRepository
from core.model_adapters import OpenAIChatAdapter
from core.model_config import migrate_legacy_config
from core.model_gateway import ModelGateway
from core.model_types import ProviderProtocol
from core.novel_manager import NovelManager


def chunk(text="", calls=None, finish=None):
    return NS(choices=[NS(delta=NS(content=text, tool_calls=calls or []), finish_reason=finish)], usage=None)


class AdvisorStreamingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.manager = NovelManager(bookshelf_root=self.temp.name)
        self.manager.create_book("book")
        self.events, self.requests, self.closed = [], [], []

    def client(self, create):
        def tracked(**kwargs):
            self.requests.append(kwargs)
            self.assertTrue(kwargs["stream"])
            return create(kwargs)
        adapter = OpenAIChatAdapter(lambda _: NS(chat=NS(completions=NS(create=tracked))))
        config = migrate_legacy_config({"text": {"api_key": "test", "base_url": "https://example.invalid/v1",
                                                 "model": "deepseek-v4-flash"}}, {})
        for provider in config.providers.values():
            provider.max_retries = 0
        return ModelGateway(config, adapters={ProviderProtocol.OPENAI_CHAT: adapter}).compat_client("agent_advisor", book_title="book")

    def ask(self, client, **kwargs):
        return WritingAdvisorService(self.manager, client).ask(
            AdvisorRequest("book", "帮我构思", "deepseek-v4-flash", settings={"agent_skills_enabled": False}),
            event_sink=self.events.append, **kwargs)

    def test_tokens_arrive_before_stream_finishes_and_tools_survive_fragments(self):
        def create(kwargs):
            turn = len(self.requests)
            try:
                if turn == 1:
                    name = next(t["function"]["name"] for t in kwargs["tools"]
                                if t["function"]["name"].startswith("project_summary_"))
                    yield chunk(calls=[NS(index=0, id="call_1", function=NS(name=name, arguments='{"_call_reason":'))])
                    yield chunk(calls=[NS(index=0, id=None, function=NS(name=None, arguments='"读取项目"}'))], finish="tool_calls")
                else:
                    self.assertEqual("tool", kwargs["messages"][-1]["role"])
                    yield chunk("第一段")
                    self.assertEqual("第一段", [e.payload["text"] for e in self.events if e.event_type == "model_delta"][-1])
                    yield chunk("\n第二段", finish="stop")
                    yield NS(choices=[], usage=NS(prompt_tokens=10, completion_tokens=5, total_tokens=15))
            finally:
                self.closed.append(turn)
        result = self.ask(self.client(create))
        self.assertEqual("completed", result.status)
        self.assertEqual("第一段\n第二段", result.answer)
        self.assertEqual("project.summary", result.tool_calls[0]["request"]["tool_name"])
        self.assertEqual([1, 2], self.closed)
        repo = AgentRepository(self.manager.get_workspace("book"))
        history = repo.load_session(result.session_id).messages
        self.assertEqual(1, sum(m["role"] == "assistant" for m in history))
        self.assertEqual(result.answer, history[-1]["content"])
        self.assertEqual(15, repo.load_run(result.run_id).usage["total_tokens"])

    def test_stream_failure_preserves_partial_and_does_not_retry(self):
        def create(_):
            try:
                yield chunk("已输出内容")
                raise RuntimeError("connection failed")
            finally:
                self.closed.append(True)
        result = self.ask(self.client(create))
        self.assertEqual("failed", result.status)
        self.assertEqual("已输出内容", result.answer)
        self.assertEqual(1, len(self.requests))
        self.assertEqual([True], self.closed)
        history = AgentRepository(self.manager.get_workspace("book")).load_session(result.session_id).messages
        self.assertFalse(any(m["role"] == "assistant" for m in history))

    def test_cancel_closes_stream_and_does_not_execute_further_turns(self):
        def create(_):
            try:
                yield chunk("部分回答")
                yield chunk("不得显示")
            finally:
                self.closed.append(True)
        result = self.ask(self.client(create), cancel_requested=lambda: any(e.event_type == "model_delta" for e in self.events))
        self.assertEqual("cancelled", result.status)
        self.assertEqual("部分回答", result.answer)
        self.assertEqual([True], self.closed)
        self.assertEqual(1, len(self.requests))

    def test_unknown_streamed_tool_is_rejected_before_execution(self):
        def create(_):
            yield chunk(calls=[NS(index=0, id="bad", function=NS(name="unauthorized", arguments="{}"))])
        result = self.ask(self.client(create))
        self.assertEqual("failed", result.status)
        self.assertEqual([], result.tool_calls)
        self.assertIn("未授权工具", result.error)

    def test_text_only_fallback_remains_streamed(self):
        calls, text = [], []
        def create(**kwargs):
            calls.append(kwargs)
            if "tools" in kwargs:
                raise RuntimeError("unsupported tools")
            return iter([chunk("仅供"), chunk("构思")])
        adapter = AgentModelAdapter(NS(chat=NS(completions=NS(create=create))), "fake")
        result = adapter.complete([], [{"type": "function", "function": {"name": "read", "parameters": {}}}], on_text=text.append)
        self.assertTrue(result.planning_only)
        self.assertEqual(["仅供", "构思"], text)
        self.assertEqual("仅供构思", result.content)
        self.assertTrue(all(call["stream"] for call in calls))

    def test_langchain_binding_retains_stream_callbacks(self):
        from core.agent.gateway_langchain import GatewayLangChainChatModel
        from langchain_core.messages import HumanMessage
        seen = []
        client = NS(chat=NS(completions=NS(create=lambda **_: iter([chunk("即时"), chunk("回答")]))))
        model = GatewayLangChainChatModel(client=client, model="fake", on_text=seen.append)
        bound = model.bind_tools([{"type": "function", "function": {"name": "read", "description": "read", "parameters": {"type": "object", "properties": {}}}}])
        result = bound.invoke([HumanMessage(content="提问")])
        self.assertEqual(["即时", "回答"], seen)
        self.assertEqual("即时回答", result.content)


class AdvisorStreamUiTests(unittest.TestCase):
    def setUp(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from ui.main_window import DeepSeekChatGUI
        self.gui = DeepSeekChatGUI
        self.view = NS(_agent_advisor_running=True, _agent_advisor_buffer=[],
                       _agent_advisor_render_timer=Mock(), _render_assistant_stream=Mock(),
                       _agent_advisor_status=Mock(), _agent_plan_preview=Mock(),
                       _agent_stop_advisor_btn=Mock(), _agent_ask_advisor_btn=Mock(),
                       _agent_save_advice_btn=Mock(), _agent_add_world_btn=Mock())
        self.view._agent_advisor_render_timer.isActive.return_value = False

    def test_live_delta_and_final_answer_share_one_render_slot(self):
        self.gui._on_agent_advisor_event(self.view, NS(event_type="model_delta", payload={"text": "第一段"}))
        self.gui._flush_agent_advisor_render(self.view)
        self.view._render_assistant_stream.assert_called_with("第一段")
        self.gui._on_agent_advisor_ready(self.view, AdvisorResult("r", "s", "第一段完成", "completed"))
        self.view._render_assistant_stream.assert_called_with("第一段完成")
        self.view._agent_add_world_btn.setEnabled.assert_called_with(True)
        self.view._agent_advisor_render_timer.stop.assert_called()

    def test_cancelled_answer_cannot_be_saved_as_complete(self):
        self.view._agent_advisor_buffer = ["部分文字"]
        self.gui._on_agent_advisor_ready(self.view, AdvisorResult("r", "s", "部分文字", "cancelled"))
        self.assertIn("未完成", self.view._render_assistant_stream.call_args.args[0])
        self.view._agent_save_advice_btn.setEnabled.assert_called_with(False)
        self.view._agent_add_world_btn.setEnabled.assert_called_with(False)


if __name__ == "__main__":
    unittest.main()

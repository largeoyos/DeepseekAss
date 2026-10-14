"""A model call must contribute usage exactly once across desktop entry points."""
import os
import tempfile
import unittest
import httpx
from types import SimpleNamespace
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication
from core.token_log_manager import TokenLogManager
from core.novel_manager import NovelManager
from core.auth_manager import AuthManager
from cryptography.fernet import Fernet
from ui.main_window import DeepSeekChatGUI, _UsageLoggingCompletionsProxy
from ui.token_log_dialog import ContentViewerDialog, TokenLogDialog


USAGE = {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150,
         "completion_tokens_details": {"reasoning_tokens": 20}}


def chunks():
    yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(
        content="回复", reasoning_content="思考"))], usage=None)
    yield SimpleNamespace(choices=[], usage=USAGE)


class RequestLoggingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        root = tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)
        self.manager = TokenLogManager(root.name)
        self.owner = SimpleNamespace(
            _token_log_manager=self.manager,
            _client=SimpleNamespace(
                model="m", strategy=SimpleNamespace(get_name=lambda: "test"),
                last_usage=USAGE, last_gateway_result=None, _cancel_requested=False,
                temperature=0.5, top_p=1, frequency_penalty=0,
            ),
            _model_config=SimpleNamespace(providers={}),
            _resolve_auxiliary_model=lambda model: model,
            _begin_api_task=Mock(return_value="task"), _finish_api_task=Mock(),
            _api_operation_label=lambda operation: operation,
            _get_current_book_title=lambda: "book", _body_generation_model=lambda: "m",
            _stream_signals=SimpleNamespace(token=Mock(), finished=Mock(), error=Mock()),
        )
        for name in ("_log_token_usage", "_count_hanzi", "_format_chapter_stats_block"):
            setattr(self.owner, name, getattr(DeepSeekChatGUI, name).__get__(self.owner))

    def assert_single_request(self, *, usage=True):
        rows = self.manager.list_entries()
        self.assertEqual(1, len(rows))
        self.assertEqual("request", rows[0].direction)
        self.assertEqual("请求", rows[0].prompt_full)
        self.assertEqual({"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}
                         if usage else {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                         self.manager.totals())
        return rows[0]

    def test_auxiliary_non_stream_call(self):
        api = SimpleNamespace(create=Mock(return_value=SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="回复", reasoning_content="思考"))],
            usage=USAGE)))
        _UsageLoggingCompletionsProxy(api, self.owner, "summary").create(
            model="m", messages=[{"role": "user", "content": "请求"}])
        api.create.assert_called_once()
        row = self.assert_single_request()
        self.assertEqual("回复", row.content_full)
        self.assertEqual("思考", row.reasoning_content_full)
        self.assertEqual(20, row.reasoning_tokens)

    def test_auxiliary_stream_multiple_chunks_count_once(self):
        api = SimpleNamespace(create=Mock(side_effect=lambda **kwargs: chunks()))
        proxy = _UsageLoggingCompletionsProxy(api, self.owner, "summary")
        list(proxy.create(model="m", messages=[{"role": "user", "content": "请求"}], stream=True))
        api.create.assert_called_once()
        row = self.assert_single_request()
        self.assertEqual("回复", row.content_full)
        self.assertEqual("思考", row.reasoning_content_full)

    def test_identical_calls_remain_distinct(self):
        api = SimpleNamespace(create=Mock(side_effect=lambda **kwargs: chunks()))
        proxy = _UsageLoggingCompletionsProxy(api, self.owner, "summary")
        for _ in range(2):
            list(proxy.create(model="m", messages=[{"role": "user", "content": "请求"}], stream=True))
        rows = self.manager.list_entries()
        self.assertEqual(2, len(rows))
        self.assertNotEqual(rows[0].id, rows[1].id)
        self.assertEqual(300, self.manager.totals()["total_tokens"])

    def test_auxiliary_failure_is_one_unavailable_record(self):
        api = SimpleNamespace(create=Mock(side_effect=RuntimeError("failed")))
        with self.assertRaises(RuntimeError):
            _UsageLoggingCompletionsProxy(api, self.owner, "summary").create(
                model="m", messages=[{"role": "user", "content": "请求"}])
        row = self.assert_single_request(usage=False)
        self.assertEqual("unavailable", row.usage_status)
        self.assertEqual("failed", row.final_failure_reason)

    def test_closed_stream_keeps_partial_reply_without_estimated_usage(self):
        api = SimpleNamespace(create=Mock(side_effect=lambda **kwargs: chunks()))
        stream = _UsageLoggingCompletionsProxy(api, self.owner, "summary").create(
            model="m", messages=[{"role": "user", "content": "请求"}], stream=True)
        next(stream)
        stream.close()
        self.assertEqual("回复", self.assert_single_request(usage=False).content_full)

    def test_chapter_generation_keeps_input_output_and_total(self):
        api = SimpleNamespace(create=Mock(side_effect=lambda **kwargs: chunks()))
        self.owner._client.client_for = lambda *args, **kwargs: SimpleNamespace(
            chat=SimpleNamespace(completions=api))
        content, stats, cancelled = DeepSeekChatGUI._stream_chapter_completion(
            self.owner, operation="chapter", messages=[{"role": "user", "content": "请求"}],
            prompt_text="请求", max_tokens=100, emit_tokens=False)
        api.create.assert_called_once()
        self.assertFalse(cancelled)
        self.assertEqual("回复", content)
        self.assertEqual(USAGE, stats["usage"])
        row = self.assert_single_request()
        self.assertEqual(2, row.char_count)
        self.assertEqual(20, row.reasoning_tokens)

    def test_main_chat_counts_once(self):
        self.owner._client.chat_stream = Mock(return_value=iter(["回", "复"]))
        DeepSeekChatGUI._run_stream(self.owner, "请求")
        self.owner._stream_signals.error.emit.assert_not_called()
        self.owner._client.chat_stream.assert_called_once_with("请求")
        self.assertEqual("回复", self.assert_single_request().content_full)

    def test_cancelled_chapter_keeps_returned_usage_once(self):
        def cancelled_stream():
            yield from chunks()
            self.owner._client._cancel_requested = True
        api = SimpleNamespace(create=Mock(side_effect=lambda **kwargs: cancelled_stream()))
        self.owner._client.client_for = lambda *args, **kwargs: SimpleNamespace(
            chat=SimpleNamespace(completions=api))
        _, _, cancelled = DeepSeekChatGUI._stream_chapter_completion(
            self.owner, operation="chapter", messages=[], prompt_text="请求",
            max_tokens=100, emit_tokens=False)
        self.assertTrue(cancelled)
        self.assert_single_request()

    def test_legacy_rows_without_prompt_fields_are_readable(self):
        self.manager._read = Mock(return_value=[{
            "id": "old", "timestamp": "2026-09-01 10:00:00", "operation": "chat",
            "direction": "receive", "strategy": "test", "model": "m",
            "content_preview": "旧回复", "total_tokens": 150,
        }])
        row = self.manager.list_entries()[0]
        self.assertEqual("", row.prompt_full)
        self.assertEqual("旧回复", row.content_preview)
        self.assertEqual(150, self.manager.totals()["total_tokens"])

    def test_interrupted_chapter_saves_encrypted_draft_and_failure_log(self):
        with tempfile.TemporaryDirectory() as root:
            manager = NovelManager(root, crypto=AuthManager, enc_key=Fernet.generate_key())
            manager.create_book("book")
            self.owner._novel_manager = manager
            def broken_stream():
                yield next(chunks())
                raise httpx.RemoteProtocolError("incomplete chunked read")
            api = SimpleNamespace(create=Mock(side_effect=lambda **kwargs: broken_stream()))
            self.owner._client.client_for = lambda *args, **kwargs: SimpleNamespace(
                chat=SimpleNamespace(completions=api))
            for _ in range(2):
                with self.assertRaisesRegex(RuntimeError, "中断草稿已保存"):
                    DeepSeekChatGUI._stream_chapter_completion(
                        self.owner, operation="agent_novel_chapter", messages=[],
                        prompt_text="请求", max_tokens=100, emit_tokens=False)
            self.assertEqual(2, api.create.call_count)
            workspace = manager.get_workspace("book")
            files = workspace.storage.list_files(".drafts")
            self.assertEqual(2, len(files))
            for path in files:
                self.assertTrue(workspace.storage.actual_path(path).endswith(".enc"))
                self.assertFalse(os.path.exists(workspace.storage.resolve(path)))
                draft = workspace.storage.read_json(path)
                self.assertEqual("回复", draft["content"])
                self.assertEqual("思考", draft["reasoning_content"])
                self.assertEqual("interrupted", draft["status"])
            rows = self.manager.list_entries()
            self.assertEqual(2, len(rows))
            self.assertEqual("回复", rows[0].content_full)
            self.assertIn("incomplete chunked read", rows[0].final_failure_reason)
            self.assertEqual("unavailable", rows[0].usage_status)
            self.assertEqual(0, self.manager.totals()["total_tokens"])

    def test_draft_write_failure_still_records_partial_output(self):
        def broken_stream():
            yield next(chunks())
            raise httpx.ReadError("disconnected")
        api = SimpleNamespace(create=Mock(side_effect=lambda **kwargs: broken_stream()))
        self.owner._client.client_for = lambda *args, **kwargs: SimpleNamespace(
            chat=SimpleNamespace(completions=api))
        self.owner._novel_manager = SimpleNamespace(save_interrupted_generation=Mock(side_effect=OSError("disk full")))
        with self.assertRaisesRegex(RuntimeError, "草稿保存失败"):
            DeepSeekChatGUI._stream_chapter_completion(
                self.owner, operation="chapter", messages=[], prompt_text="请求",
                max_tokens=100, emit_tokens=False)
        row = self.assert_single_request(usage=False)
        self.assertEqual("回复", row.content_full)
        self.assertIn("disk full", row.final_failure_reason)

    def test_failure_before_output_is_logged_once_without_draft(self):
        api = SimpleNamespace(create=Mock(side_effect=httpx.ReadError("disconnected")))
        self.owner._client.client_for = lambda *args, **kwargs: SimpleNamespace(
            chat=SimpleNamespace(completions=api))
        self.owner._novel_manager = SimpleNamespace(save_interrupted_generation=Mock())
        with self.assertRaisesRegex(RuntimeError, "disconnected"):
            DeepSeekChatGUI._stream_chapter_completion(
                self.owner, operation="chapter", messages=[], prompt_text="请求",
                max_tokens=100, emit_tokens=False)
        api.create.assert_called_once()
        self.owner._novel_manager.save_interrupted_generation.assert_not_called()
        self.assert_single_request(usage=False)

    def test_cancelled_chat_does_not_log_previous_reply(self):
        self.owner._client._cancel_requested = True
        self.owner._client.last_usage = None
        self.owner._client.chat_stream = Mock(return_value=iter(["部分回复"]))
        self.owner._client.export_messages = Mock(return_value=[{"role": "assistant", "content": "旧回复"}])
        DeepSeekChatGUI._run_stream(self.owner, "请求")
        self.owner._stream_signals.error.emit.assert_not_called()
        self.assertEqual("部分回复", self.assert_single_request(usage=False).content_full)

    def test_request_content_view_and_search(self):
        entry = self.manager.add_entry(operation="chat", direction="request", strategy="test",
                                       model="m", prompt="请求", content="回复", usage=USAGE)
        viewer = ContentViewerDialog(None, entry)
        self.addCleanup(viewer.close)
        self.assertEqual("请求", viewer._prompt.toPlainText())
        self.assertEqual("回复", viewer._text.toPlainText())
        dialog = TokenLogDialog(None, self.manager)
        self.addCleanup(dialog.close)
        dialog._search.setText("请求")
        self.assertEqual(1, dialog._table.rowCount())
        self.assertEqual("请求 / 回复", dialog._table.item(0, 1).text())


if __name__ == "__main__":
    unittest.main()

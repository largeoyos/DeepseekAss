import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu")

from cryptography.fernet import Fernet
from PyQt6.QtCore import QEventLoop, QTimer
from PyQt6.QtWidgets import QApplication
from core.auth_manager import AuthManager
from core.display_history import DisplayHistoryStore
from ui.display_history_view import DisplayHistoryView
from ui.main_window import DeepSeekChatGUI, REPLY_JUMP_NAV_SCRIPT


class DisplayHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = DisplayHistoryStore(self.temp.name)

    def test_restart_restores_all_messages_and_appends_new_reply(self):
        self.store.append("user", "第一个问题")
        self.store.update_reply("答")
        self.store.update_reply("完整回答")
        self.store.save()
        previous_id = self.store.session_id
        restored = DisplayHistoryStore(self.temp.name)
        restored.update_reply("第二次启动的回答")
        restored.save()
        self.assertEqual(["第一个问题", "完整回答", "第二次启动的回答"], [e["html"] for e in restored.entries])
        self.assertEqual(2, len(restored.list_sessions()))
        self.assertEqual(2, len(restored.load_session(previous_id)["entries"]))

    def test_new_user_and_tool_turn_do_not_replace_previous_output(self):
        self.store.update_reply("第一轮分析")
        self.store.begin_reply()
        self.store.update_reply("读取资料后的回答")
        self.store.append("user", "追问")
        self.store.update_reply("追问回答")
        self.assertEqual(4, len(self.store.entries))
        self.assertEqual("第一轮分析", self.store.entries[0]["html"])

    def test_structured_refresh_updates_stable_ids_without_duplicates(self):
        self.store.append("user", "<strong>你：</strong><br>问题")
        self.store.append("user", "<strong>你：</strong><br>问题", "structured:one")
        self.store.append("assistant", "答案", "structured:two")
        self.store.append("assistant", "修改后的答案", "structured:two")
        self.assertEqual(2, len(self.store.entries))
        self.assertEqual("修改后的答案", self.store.entries[-1]["html"])

    def test_encrypted_account_data_and_wrong_key_rejection(self):
        key = Fernet.generate_key()
        store = DisplayHistoryStore(os.path.join(self.temp.name, "account"), AuthManager, key)
        store.append("assistant", "仅属于这个账号的内容")
        store.save()
        for path in Path(store.storage.root).rglob("*"):
            if path.is_file():
                self.assertTrue(path.name.endswith(".enc"))
                self.assertNotIn("仅属于这个账号".encode(), path.read_bytes())
        self.assertEqual(1, len(DisplayHistoryStore(store.storage.root, AuthManager, key).entries))
        with self.assertRaises(Exception):
            DisplayHistoryStore(store.storage.root, AuthManager, Fernet.generate_key())
        self.assertEqual([], DisplayHistoryStore(os.path.join(self.temp.name, "other")).entries)

    def test_failed_save_remains_dirty_and_can_retry(self):
        self.store.append("assistant", "不能丢失")
        with patch.object(self.store.storage, "write_json", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.store.save()
        self.assertTrue(self.store.dirty)
        self.store.save()
        self.assertFalse(self.store.dirty)
        self.assertEqual("不能丢失", DisplayHistoryStore(self.temp.name).entries[0]["html"])

    def test_snapshot_path_cannot_escape_account_directory(self):
        with self.assertRaises(ValueError):
            self.store.load_session("../another-account")

    def test_close_flushes_last_advisor_text_before_saving(self):
        calls = []
        window = NS(_streaming=False, _conversation_dirty=False, _agent_advisor_running=True,
                    _cancel_agent_advisor=lambda: calls.append("cancel"),
                    _flush_agent_advisor_render=lambda: calls.append("flush"),
                    _display_history=NS(save=lambda: calls.append("save")))
        event = Mock()
        DeepSeekChatGUI.closeEvent(window, event)
        self.assertEqual(["cancel", "flush", "save"], calls)
        event.accept.assert_called_once()

    def test_close_save_failure_keeps_window_open(self):
        window = NS(_streaming=False, _conversation_dirty=False, _agent_advisor_running=False,
                    _display_history=NS(save=Mock(side_effect=OSError("full"))))
        event = Mock()
        with patch("ui.main_window.QMessageBox.warning"):
            DeepSeekChatGUI.closeEvent(window, event)
        event.ignore.assert_called_once()
        event.accept.assert_not_called()


class DisplayHistoryRefreshTests(unittest.TestCase):
    """Execute the refresh JavaScript without requiring a Chromium renderer."""
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from PyQt6.QtQml import QJSEngine
        self.engine = QJSEngine()
        self.engine.evaluate("""
            var root = null, original = null, inserted = 0, navBuilds = 0, scrolls = 0, writes = 0;
            function message(content) {
                var node = {};
                Object.defineProperty(node, 'innerHTML', {get: () => content, set: v => {content=v; writes++;}});
                Object.defineProperty(node, 'outerHTML', {set: v => {root=message(v);}});
                return node;
            }
            var window = {innerHeight: 100, scrollY: 0, scrollTo: () => {scrolls++;}};
            var document = {documentElement: {scrollHeight: 1000},
                getElementById: () => root,
                body: {scrollHeight: 1000, insertAdjacentHTML: (position, body) => {
                    inserted++; root=message(body.slice(body.indexOf('>')+1, body.lastIndexOf('</div>')));
                }}};
        """)
        def run(script, callback):
            result = self.engine.evaluate(script)
            self.assertFalse(result.isError(), result.toString())
            callback(result.toVariant())
        self.view = NS(_ready=True, entry_html=DisplayHistoryView.entry_html,
                       page=lambda: NS(runJavaScript=run),
                       navigation_script="function rebuildReplyJumpNav() {navBuilds++;}")

    def update(self, content):
        DisplayHistoryView._show_entry(self.view, {"id": "one", "kind": "assistant", "html": content})

    def test_stream_keeps_element_and_navigation_identity(self):
        self.update("开头")
        self.engine.evaluate("original=root")
        self.update("开头与后续文字")
        self.assertTrue(self.engine.evaluate("root===original").toBool())
        self.assertEqual("开头与后续文字", self.engine.evaluate("root.innerHTML").toString())
        self.assertEqual(1, self.engine.evaluate("inserted").toInt())
        self.assertEqual(1, self.engine.evaluate("navBuilds").toInt())

    def test_scrollback_is_preserved_and_unchanged_content_is_not_rewritten(self):
        self.update("回答")
        self.update("回答")
        self.assertEqual(0, self.engine.evaluate("scrolls").toInt())
        self.assertEqual(0, self.engine.evaluate("writes").toInt())
        self.engine.evaluate("window.scrollY=900")
        self.update("回答继续")
        self.assertEqual(1, self.engine.evaluate("scrolls").toInt())


class DisplayHistoryBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Some Windows test hosts terminate natively in even an unmodified
        # QWebEngineView.setHtml(). Probe in a child so this cannot kill the suite.
        probe = subprocess.run([sys.executable, "-c",
            "from PyQt6.QtWidgets import QApplication; "
            "from PyQt6.QtWebEngineWidgets import QWebEngineView; "
            "app=QApplication([]); view=QWebEngineView(); view.setHtml('<html>test</html>')"],
            capture_output=True, timeout=20)
        if probe.returncode:
            raise unittest.SkipTest("此测试环境中的原生 QWebEngineView 无法初始化页面")
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = DisplayHistoryStore(self.temp.name)
        self.view = DisplayHistoryView(self.store, lambda: "<style>body{color:#333}</style>", REPLY_JUMP_NAV_SCRIPT)

    def tearDown(self):
        self.view._save_timer.stop()
        self.view.close()
        self.view.deleteLater()
        self.app.sendPostedEvents()
        self.temp.cleanup()

    def wait(self, trigger):
        loop = QEventLoop()
        values = []
        timer = QTimer()
        timer.setSingleShot(True)
        timer.timeout.connect(loop.quit)
        timer.start(10000)
        def done(value):
            values.append(value)
            loop.quit()
        trigger(done)
        loop.exec()
        timer.stop()
        self.assertTrue(values, "WebEngine callback timed out")
        return values[0]

    def test_real_dom_keeps_previous_output_across_resets_and_repeated_updates(self):
        self.view.append_block("user", "旧问题")
        self.wait(lambda done: self.view.update_reply("旧答案", done))
        self.view.setHtml("<html><body>欢迎页</body></html>")
        self.view.append_block("user", "新问题")
        self.view.update_reply("新")
        self.wait(lambda done: self.view.update_reply("新答案", done))
        result = self.wait(lambda done: self.view.page().runJavaScript(
            "Array.from(document.querySelectorAll('[id^=display-]')).map(x=>x.textContent)", done))
        self.assertEqual(["旧问题", "旧答案", "新问题", "新答案"], result)
        self.store.save()
        self.assertEqual(4, len(DisplayHistoryStore(self.temp.name).entries))

    def test_model_state_is_saved_even_before_first_page_load(self):
        self.view.append_block("user", "启动时立即输入")
        self.view.update_reply("尚未绘制的文字")
        self.store.save()
        restored = DisplayHistoryStore(self.temp.name)
        self.assertEqual(["启动时立即输入", "尚未绘制的文字"], [e["html"] for e in restored.entries])


if __name__ == "__main__":
    unittest.main()

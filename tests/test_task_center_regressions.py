import os
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from core.task_manager import (
    TaskEvent, TaskHandle, TaskRunner, current_task_handle, report_task_failure, task_cancel_requested,
)
from ui.main_window import DeepSeekChatGUI
from ui.task_center_dialog import TaskCenterDialog
from web.server import create_app
from web.services import WebRuntime


def wait_done(runner, task_id):
    deadline = time.monotonic() + 3
    while task_id in runner.active() and time.monotonic() < deadline:
        time.sleep(.005)
    if task_id in runner.active():
        raise AssertionError("Task did not finish")
    return runner.get_record(task_id)


class TaskLifecycleTests(unittest.TestCase):
    def test_non_cancellable_work_rejects_cancel_and_finishes_successfully(self):
        runner = TaskRunner()
        entered, release = threading.Event(), threading.Event()
        def work(handle):
            entered.set()
            release.wait(3)
            return "saved"
        handle = runner.start("save", work)
        try:
            self.assertTrue(entered.wait(2))
            self.assertFalse(runner.cancel(handle.task_id))
        finally:
            release.set()
        self.assertEqual("completed", wait_done(runner, handle.task_id).status)

    def test_unobserved_late_cancel_does_not_lie_about_completed_work(self):
        runner = TaskRunner()
        entered, release = threading.Event(), threading.Event()
        effects = []
        def work(handle):
            self.assertFalse(handle.cancelled)
            entered.set()
            release.wait(3)
            effects.append("saved")
        handle = runner.start("save", work, cancellable=True)
        try:
            self.assertTrue(entered.wait(2))
            self.assertTrue(runner.cancel(handle.task_id))
            self.assertEqual("cancelling", runner.get_record(handle.task_id).status)
        finally:
            release.set()
        self.assertEqual("completed", wait_done(runner, handle.task_id).status)
        self.assertEqual(["saved"], effects)

    def test_running_and_completed_tasks_cannot_be_retried(self):
        runner = TaskRunner()
        release = threading.Event()
        handle = runner.start("work", lambda h: release.wait(3), retryable=True)
        try:
            with self.assertRaises(ValueError):
                runner.retry(handle.task_id)
        finally:
            release.set()
        wait_done(runner, handle.task_id)
        with self.assertRaises(ValueError):
            runner.retry(handle.task_id)

    def test_repeated_retry_does_not_duplicate_active_retry(self):
        runner = TaskRunner()
        release = threading.Event()
        attempts = []
        def work(handle):
            attempts.append(handle.task_id)
            if len(attempts) == 1:
                raise ValueError("first attempt failed")
            release.wait(3)
        first = runner.start("work", work, retryable=True)
        wait_done(runner, first.task_id)
        second = runner.retry(first.task_id)
        try:
            with self.assertRaises(ValueError):
                runner.retry(first.task_id)
        finally:
            release.set()
        wait_done(runner, second.task_id)
        self.assertEqual(2, len(attempts))

    def test_caught_worker_error_is_recorded_without_leaking_to_other_threads(self):
        runner = TaskRunner()
        handle = runner.start("legacy", lambda h: report_task_failure("model failed"))
        record = wait_done(runner, handle.task_id)
        self.assertEqual("failed", record.status)
        self.assertEqual("model failed", record.error)
        self.assertIsNone(current_task_handle())


class DesktopTaskTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def make_host(self):
        client = SimpleNamespace(_cancel_requested=False)
        client.cancel = lambda: setattr(client, "_cancel_requested", True)
        return SimpleNamespace(
            _client=client, _task_runner=TaskRunner(),
            _stream_signals=SimpleNamespace(error=SimpleNamespace(emit=Mock())),
            _stop_btn=SimpleNamespace(setVisible=Mock()),
        )

    def test_desktop_worker_is_registered_and_receives_cancel_from_task_center(self):
        host = self.make_host()
        entered, release = threading.Event(), threading.Event()
        effects = []
        def worker():
            entered.set()
            release.wait(3)
            if task_cancel_requested(host._client._cancel_requested):
                return
            effects.append("saved")
        handle = DeepSeekChatGUI._start_background_task(host, "生成章节", worker, cancellable=True)
        try:
            self.assertTrue(entered.wait(2))
            self.assertEqual("生成章节", host._task_runner.history()[0].name)
            self.assertTrue(host._task_runner.cancel(handle.task_id))
            self.assertTrue(host._client._cancel_requested)
        finally:
            release.set()
        self.assertEqual("cancelled", wait_done(host._task_runner, handle.task_id).status)
        self.assertEqual([], effects)

    def test_main_stop_updates_task_center_and_caught_failures_are_visible(self):
        host = self.make_host()
        entered, release = threading.Event(), threading.Event()
        def worker():
            entered.set()
            release.wait(3)
            if task_cancel_requested(host._client._cancel_requested):
                return
        handle = DeepSeekChatGUI._start_background_task(host, "聊天", worker, cancellable=True)
        try:
            self.assertTrue(entered.wait(2))
            DeepSeekChatGUI._on_stop(host)
            self.assertEqual("cancelling", host._task_runner.get_record(handle.task_id).status)
        finally:
            release.set()
        self.assertEqual("cancelled", wait_done(host._task_runner, handle.task_id).status)
        failed = DeepSeekChatGUI._start_background_task(host, "提取", lambda: report_task_failure("提取失败"))
        self.assertEqual("failed", wait_done(host._task_runner, failed.task_id).status)

    def test_refresh_keeps_selected_id_and_buttons_follow_capabilities(self):
        runner = TaskRunner()
        old = runner.start("old", lambda h: None)
        wait_done(runner, old.task_id)
        dialog = TaskCenterDialog(None, runner)
        dialog._timer.stop()
        try:
            dialog._table.selectRow(0)
            self.assertFalse(dialog._cancel_button.isEnabled())
            self.assertFalse(dialog._retry_button.isEnabled())
            new = runner.start("new", lambda h: None)
            wait_done(runner, new.task_id)
            dialog._refresh()
            self.assertEqual(old.task_id, dialog._selected_task_id())
            self.assertEqual(1, dialog._table.currentRow())
            self.assertEqual(dialog._table.EditTrigger.NoEditTriggers, dialog._table.editTriggers())
        finally:
            dialog.close()

    def test_refresh_clears_selection_when_task_is_removed(self):
        runner = TaskRunner(history_limit=10)
        old = runner.start("old", lambda h: None)
        wait_done(runner, old.task_id)
        dialog = TaskCenterDialog(None, runner)
        dialog._timer.stop()
        try:
            dialog._table.selectRow(0)
            for i in range(10):
                handle = runner.start(str(i), lambda h: None)
                wait_done(runner, handle.task_id)
            dialog._refresh()
            self.assertEqual("", dialog._selected_task_id())
            self.assertFalse(dialog._cancel_button.isEnabled())
        finally:
            dialog.close()


class TaskEventSubscriptionTests(unittest.TestCase):
    def test_streamed_text_contains_each_chunk_once_and_closes_response(self):
        runtime = WebRuntime(client_factory=lambda _: None)
        texts = ["A" * 50, "B" * 50, "C" * 50, "D" * 50, "E" * 600, "tail"]
        class Response:
            closed = False
            def __iter__(self):
                return iter(SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=t))])
                            for t in texts)
            def close(self):
                self.closed = True
        response = Response()
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kw: response)))
        events = []
        handle = TaskHandle("stream", events.append)
        params = dict(model="test", temperature=.5, top_p=1, max_tokens=1000, frequency_penalty=0)
        result = runtime._stream_completion(handle, client, [], params)
        self.assertEqual("".join(texts), result)
        self.assertEqual(result, "".join(e.data.get("text", "") for e in events))
        self.assertTrue(response.closed)

    def test_world_rebuild_rejects_cancel_instead_of_falsely_reporting_cancelled(self):
        runtime = WebRuntime(client_factory=lambda _: None)
        app = create_app(runtime)
        endpoint = next(route.endpoint for route in app.routes if route.path == "/api/books/{title}/world/rebuild")
        entered, release = threading.Event(), threading.Event()
        effects = []
        def rebuild(*args, **kwargs):
            entered.set()
            release.wait(3)
            effects.append("saved")
            return {"ok": True}
        manager = SimpleNamespace(rebuild_world_bible_from_active=rebuild,
                                  load_meta=lambda _: SimpleNamespace(xp_mode=False))
        context = SimpleNamespace(username="user", settings={}, novel_manager=manager)
        task_id = endpoint("book", SimpleNamespace(requirement=""), context)["task_id"]
        try:
            self.assertTrue(entered.wait(2))
            self.assertFalse(runtime.serialize_task(task_id)["cancellable"])
            self.assertFalse(runtime.cancel_task("user", task_id))
        finally:
            release.set()
        self.assertEqual("completed", wait_done(runtime._task_runner, task_id).status)
        self.assertEqual(["saved"], effects)

    def test_replay_and_live_are_disjoint_and_two_subscribers_get_every_event(self):
        runtime = WebRuntime(client_factory=lambda _: None)
        runtime._on_task_event(TaskEvent("demo", "progress", data={"text": "A"}))
        replay1, q1 = runtime.subscribe_task_events("demo")
        replay2, q2 = runtime.subscribe_task_events("demo")
        self.assertEqual(["A"], [e.data["text"] for e in replay1])
        self.assertEqual(replay1, replay2)
        self.assertTrue(q1.empty())
        self.assertTrue(q2.empty())
        runtime._on_task_event(TaskEvent("demo", "progress", data={"text": "B"}))
        runtime._on_task_event(TaskEvent("demo", "completed"))
        for q in (q1, q2):
            self.assertEqual("B", q.get_nowait().data["text"])
            self.assertEqual("completed", q.get_nowait().type)
            self.assertTrue(q.empty())
            runtime.unsubscribe_task_events("demo", q)
        self.assertNotIn("demo", runtime._task_subscribers)


class TaskSseEndpointTests(unittest.IsolatedAsyncioTestCase):
    async def test_live_endpoint_does_not_replay_chunks_twice_and_reconnect_uses_cursor(self):
        runtime = WebRuntime(client_factory=lambda _: None)
        runtime.context_from_token = lambda _: SimpleNamespace(username="user")
        runtime.user_owns_task = lambda *args: True
        app = create_app(runtime)
        endpoint = next(route.endpoint for route in app.routes if route.path == "/api/tasks/{task_id}/events")
        async def connected():
            return False
        request = SimpleNamespace(headers={}, is_disconnected=connected)
        runtime._on_task_event(TaskEvent("demo", "progress", data={"text": "A"}))
        response = await endpoint(request, "demo", "token")
        stream = response.body_iterator
        first = await anext(stream)
        self.assertIn('"text": "A"', first)
        runtime._on_task_event(TaskEvent("demo", "progress", data={"text": "B"}))
        runtime._on_task_event(TaskEvent("demo", "completed"))
        remainder = [item async for item in stream]
        self.assertEqual(2, len(remainder))
        self.assertIn('"text": "B"', remainder[0])
        self.assertNotIn("demo", runtime._task_subscribers)
        request.headers = {"last-event-id": "2"}
        response = await endpoint(request, "demo", "token")
        replay = [item async for item in response.body_iterator]
        self.assertEqual(1, len(replay))
        self.assertIn("event: completed", replay[0])

    async def test_disconnected_stream_unsubscribes(self):
        runtime = WebRuntime(client_factory=lambda _: None)
        runtime.context_from_token = lambda _: SimpleNamespace(username="user")
        runtime.user_owns_task = lambda *args: True
        app = create_app(runtime)
        endpoint = next(route.endpoint for route in app.routes if route.path == "/api/tasks/{task_id}/events")
        async def connected():
            return False
        runtime._on_task_event(TaskEvent("demo", "progress", data={"text": "A"}))
        response = await endpoint(SimpleNamespace(headers={}, is_disconnected=connected), "demo", "token")
        await anext(response.body_iterator)
        await response.body_iterator.aclose()
        self.assertNotIn("demo", runtime._task_subscribers)


if __name__ == "__main__":
    unittest.main()

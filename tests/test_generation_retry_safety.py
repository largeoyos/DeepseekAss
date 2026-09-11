import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from core.task_manager import TaskHandle
from web.services import WebRuntime


class GenerationRetrySafetyTests(unittest.TestCase):
    def setUp(self):
        self.runtime = WebRuntime(client_factory=lambda _: None)
        self.context = SimpleNamespace(username="reader", require_model=lambda *args: {})

    def start(self):
        return self.runtime.start_generation(self.context, title="book", chapter_title="chapter",
                                             plot="", target_words=100)

    def wait(self, task_id):
        deadline = time.monotonic() + 3
        while task_id in self.runtime._task_runner.active() and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertNotIn(task_id, self.runtime._task_runner.active())
        return self.runtime.serialize_task(task_id)

    def failed_task(self):
        with patch.object(self.runtime, "_run_generation_task", side_effect=ValueError("model unavailable")):
            task_id = self.start()
            self.assertEqual("failed", self.wait(task_id)["status"])
        return task_id

    def test_retry_success_holds_and_releases_its_own_lock(self):
        task_id = self.failed_task()
        def success(*args, **kwargs):
            self.assertTrue(self.runtime._generation_locks["reader"].locked())
            return "generated"
        with patch.object(self.runtime, "_run_generation_task", side_effect=success):
            retry = self.runtime.retry_task("reader", task_id)
            result = self.wait(retry)
        self.assertEqual("completed", result["status"])
        self.assertEqual("generated", result["result_preview"])
        self.assertFalse(self.runtime._generation_locks["reader"].locked())

    def test_retry_cannot_release_another_running_tasks_lock(self):
        task_id = self.failed_task()
        entered, release = threading.Event(), threading.Event()
        def blocked(*args, **kwargs):
            entered.set()
            release.wait(3)
        with patch.object(self.runtime, "_run_generation_task", side_effect=blocked) as generate:
            running = self.start()
            try:
                self.assertTrue(entered.wait(2))
                retry = self.runtime.retry_task("reader", task_id)
                self.assertEqual("failed", self.wait(retry)["status"])
                self.assertTrue(self.runtime._generation_locks["reader"].locked())
                self.assertEqual(1, generate.call_count)
                with self.assertRaises(RuntimeError):
                    self.start()
            finally:
                release.set()
                self.wait(running)
        self.assertFalse(self.runtime._generation_locks["reader"].locked())

    def test_cancelled_execution_does_not_generate_or_touch_lock(self):
        with patch.object(self.runtime, "start_task", return_value="pending") as start:
            self.start()
        callback = start.call_args.args[2]
        handle = TaskHandle("cancelled")
        handle.cancel()
        with patch.object(self.runtime, "_run_generation_task") as generate:
            callback(handle)
            generate.assert_not_called()
        self.assertFalse(self.runtime._generation_locks["reader"].locked())


if __name__ == "__main__":
    unittest.main()

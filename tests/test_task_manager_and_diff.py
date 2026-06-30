import tempfile
import time
import unittest

from core.task_manager import TaskRunner, WorkspaceTaskHistoryStore
from core.workspace import BookWorkspace
from core.world_bible import CharacterEntry, WorldBible
from core.world_bible_diff import diff_world_bibles, summarize_world_bible_diff


class TaskManagerTests(unittest.TestCase):
    def wait_done(self, runner, task_id):
        for _ in range(100):
            if task_id not in runner.active():
                return
            time.sleep(0.01)
        self.fail("task did not finish")

    def test_task_runner_records_progress_and_completion(self):
        events = []
        runner = TaskRunner(events.append)

        def task(handle):
            handle.progress("half", percent=50, stage="draft")
            return "ok"

        handle = runner.start("demo", task)
        self.wait_done(runner, handle.task_id)
        record = runner.get_record(handle.task_id)
        self.assertEqual("completed", record.status)
        self.assertEqual(100, record.progress)
        self.assertEqual("draft", record.stage)
        self.assertTrue(any(event.type == "progress" for event in events))

    def test_task_runner_cancel_and_retry(self):
        calls = {"count": 0}
        runner = TaskRunner()

        def task(handle):
            calls["count"] += 1
            handle.cancel()
            return "cancelled"

        first = runner.start("retryable", task, retryable=True)
        self.wait_done(runner, first.task_id)
        self.assertEqual("cancelled", runner.get_record(first.task_id).status)
        second = runner.retry(first.task_id)
        self.wait_done(runner, second.task_id)
        self.assertEqual(first.task_id, runner.get_record(second.task_id).retry_of)
        self.assertEqual(2, calls["count"])

    def test_workspace_task_history_store_round_trip(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = BookWorkspace(root)
            store = WorkspaceTaskHistoryStore(workspace)
            runner = TaskRunner(history_store=store)
            handle = runner.start("persist", lambda _handle: "ok")
            self.wait_done(runner, handle.task_id)
            reloaded = TaskRunner(history_store=store)
            records = reloaded.history()
            self.assertEqual(1, len(records))
            self.assertEqual("completed", records[0].status)
            self.assertTrue(workspace.storage.exists(".deepseekass/tasks.json"))


class WorldBibleDiffTests(unittest.TestCase):
    def test_world_bible_diff_flags_locked_removal_as_high_risk(self):
        old = WorldBible(characters=[CharacterEntry(id="hero", name="Hero", locked=True)])
        new = WorldBible(characters=[])
        items = diff_world_bibles(old, new)
        summary = summarize_world_bible_diff(items)
        self.assertEqual(1, summary["removed"])
        self.assertEqual(1, summary["high"])
        self.assertEqual("removed", items[0].change_type)

    def test_world_bible_diff_tracks_added_and_modified_entries(self):
        old = WorldBible(characters=[CharacterEntry(id="hero", name="Hero", current_goal="A")])
        new = WorldBible(characters=[
            CharacterEntry(id="hero", name="Hero", current_goal="B"),
            CharacterEntry(id="ally", name="Ally"),
        ])
        items = diff_world_bibles(old, new)
        changes = {item.change_type for item in items}
        self.assertIn("modified", changes)
        self.assertIn("added", changes)


if __name__ == "__main__":
    unittest.main()

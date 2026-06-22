import json
import unittest
from types import SimpleNamespace

from utils.supervision import audit_chapter, supervise_chapter


def response(content):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
    )


class FakeCompletions:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        value = self.outputs.pop(0)
        if isinstance(value, Exception):
            raise value
        return response(value)


class FakeClient:
    def __init__(self, outputs):
        self.completions = FakeCompletions(outputs)
        self.chat = SimpleNamespace(completions=self.completions)


def audit_payload(status="fulfilled"):
    item = {
        "id": "1",
        "requirement": "The hero opens the sealed door",
        "status": status,
        "evidence": "The door opens" if status == "fulfilled" else "",
        "problem": "" if status == "fulfilled" else "The event is absent",
        "repair": "" if status == "fulfilled" else "Add the event",
    }
    return json.dumps({
        "outline_items": [item],
        "hard_constraint_issues": [],
        "continuity_issues": [],
        "repair_instruction": "" if status == "fulfilled" else "Add the missing event",
    })


class SupervisionTests(unittest.TestCase):
    def test_passed_outline_does_not_modify_content(self):
        original = "a" * 300
        client = FakeClient([audit_payload()])
        content, result = supervise_chapter(
            lambda action: client,
            chapter_content=original,
            chapter_title="Chapter 1",
            chapter_outline="Open the sealed door",
            model="test",
        )
        self.assertEqual(original, content)
        self.assertEqual("passed", result.status)
        self.assertEqual(0, result.repair_rounds)
        self.assertEqual(1, client.completions.calls)

    def test_missing_outline_is_repaired_and_reaudited(self):
        original = "a" * 300
        repaired = "b" * 300
        client = FakeClient([audit_payload("missing"), repaired, audit_payload()])
        content, result = supervise_chapter(
            lambda action: client,
            chapter_content=original,
            chapter_title="Chapter 1",
            chapter_outline="Open the sealed door",
            model="test",
        )
        self.assertEqual(repaired, content)
        self.assertEqual("passed", result.status)
        self.assertEqual(1, result.repair_rounds)
        self.assertEqual(3, client.completions.calls)

    def test_repair_stops_after_two_rounds(self):
        client = FakeClient([
            audit_payload("missing"), "b" * 300,
            audit_payload("missing"), "c" * 300,
            audit_payload("missing"),
        ])
        content, result = supervise_chapter(
            lambda action: client,
            chapter_content="a" * 300,
            chapter_title="Chapter 1",
            chapter_outline="Open the sealed door",
            model="test",
            max_repair_rounds=2,
        )
        self.assertEqual("c" * 300, content)
        self.assertEqual("warning", result.status)
        self.assertEqual(2, result.repair_rounds)
        self.assertEqual(5, client.completions.calls)

    def test_invalid_audit_fails_open_without_repair(self):
        original = "a" * 300
        client = FakeClient(["not-json"])
        content, result = supervise_chapter(
            lambda action: client,
            chapter_content=original,
            chapter_title="Chapter 1",
            model="test",
        )
        self.assertEqual(original, content)
        self.assertEqual("warning", result.status)
        self.assertTrue(result.audit_failed)
        self.assertEqual(1, client.completions.calls)

    def test_word_count_is_checked_locally(self):
        client = FakeClient([json.dumps({
            "outline_items": [],
            "hard_constraint_issues": [],
            "continuity_issues": [],
            "repair_instruction": "",
        })])
        result = audit_chapter(
            client,
            chapter_content="short",
            chapter_title="Chapter 1",
            chapter_outline="",
            requirements="",
            continuity_context="",
            target_words=10,
            model="test",
        )
        self.assertTrue(result.needs_repair)
        self.assertEqual("word_count", result.hard_constraint_issues[0]["type"])

    def test_short_repair_output_keeps_original(self):
        original = "a" * 300
        client = FakeClient([audit_payload("missing"), "too short"])
        content, result = supervise_chapter(
            lambda action: client,
            chapter_content=original,
            chapter_title="Chapter 1",
            chapter_outline="Open the sealed door",
            model="test",
        )
        self.assertEqual(original, content)
        self.assertEqual("warning", result.status)
        self.assertEqual(2, client.completions.calls)


if __name__ == "__main__":
    unittest.main()

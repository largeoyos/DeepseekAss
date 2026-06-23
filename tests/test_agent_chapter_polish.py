import json
import tempfile
import unittest
from types import SimpleNamespace

from core.agent.chapter_polish import (
    AgentChapterPolishService,
    AgentPolishRequest,
)
from core.agent.repository import AgentRepository
from core.novel_manager import NovelManager


class _FakeCompletions:
    def __init__(self, responses):
        self.responses = list(responses)

    def create(self, **_kwargs):
        if not self.responses:
            raise AssertionError("unexpected model call")
        content = self.responses.pop(0)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
        )


class _FakeClient:
    def __init__(self, responses):
        self.chat = SimpleNamespace(completions=_FakeCompletions(responses))


def _plan_json(**updates):
    data = {
        "detected_issues": [
            {"category": "重复", "description": "句式重复", "evidence": "他走"}
        ],
        "polish_actions": [{"target": "句式", "action": "减少重复"}],
        "preserved_facts": ["主角进入房间"],
        "preserved_dialogue_intents": ["主角拒绝邀请"],
        "selected_world_entities": [],
        "selected_history_chapters": [],
        "constraints": ["不改变剧情"],
        "rewrite_required": False,
        "rewrite_reasons": [],
    }
    data.update(updates)
    return json.dumps(data, ensure_ascii=False)


def _audit_json(passed, **updates):
    data = {
        "passed": passed,
        "plot_drift": [],
        "fact_drift": [],
        "character_drift": [],
        "dialogue_intent_drift": [],
        "new_facts": [],
        "requirement_issues": [],
        "format_issues": [],
        "repair_instruction": "",
    }
    data.update(updates)
    return json.dumps(data, ensure_ascii=False)


class AgentChapterPolishTests(unittest.TestCase):
    def _book(self, root):
        manager = NovelManager(root)
        manager.create_book("测试书")
        manager.save_chapter_version("测试书", 1, "第一章", "主角进入房间，并拒绝了邀请。")
        request = AgentPolishRequest(
            book_title="测试书",
            node_id=manager._node_id(1, 1),
            chapter_num=1,
            chapter_title="第一章",
            requirement="改善句式和节奏",
            model="test-model",
        )
        return manager, request

    def test_prepare_builds_encrypted_workspace_run(self):
        with tempfile.TemporaryDirectory() as root:
            manager, request = self._book(root)
            service = AgentChapterPolishService(manager, _FakeClient([_plan_json()]))
            plan = service.prepare(request)
            workspace = manager.get_workspace("测试书")
            record = workspace.storage.read_json(
                f"{workspace.agent_root}/chapter_polish_runs/{plan.plan_id}.json"
            )
        self.assertEqual("prepared", record["status"])
        self.assertFalse(plan.rewrite_required)
        self.assertIn("主角进入房间", plan.preserved_facts)
        self.assertIn("chapter-polish", {item["id"] for item in plan.selected_skills})

    def test_plot_change_requirement_is_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            manager, request = self._book(root)
            request.requirement = "增加一段新剧情，让主角背叛同伴"
            service = AgentChapterPolishService(manager, _FakeClient([_plan_json()]))
            plan = service.prepare(request)
        self.assertTrue(plan.rewrite_required)
        self.assertTrue(plan.rewrite_reasons)

    def test_invalid_plan_json_is_repaired_once(self):
        with tempfile.TemporaryDirectory() as root:
            manager, request = self._book(root)
            service = AgentChapterPolishService(
                manager, _FakeClient(["not-json", _plan_json()])
            )
            plan = service.prepare(request)
        self.assertFalse(plan.rewrite_required)
    def test_polish_skills_can_be_disabled(self):
        with tempfile.TemporaryDirectory() as root:
            manager, request = self._book(root)
            service = AgentChapterPolishService(
                manager, _FakeClient([_plan_json()]), skills_enabled=False
            )
            plan = service.prepare(request)
            prompt, _original = service.build_prompt(request, plan)
        self.assertEqual([], plan.selected_skills)
        self.assertNotIn("本次启用 Skills", prompt)
    def test_validation_passes_without_repair(self):
        with tempfile.TemporaryDirectory() as root:
            manager, request = self._book(root)
            client = _FakeClient([_plan_json(), _audit_json(True)])
            service = AgentChapterPolishService(manager, client)
            plan = service.prepare(request)
            result = service.validate_and_repair(
                request, plan, "原文", "润色稿"
            )
        self.assertTrue(result.passed)
        self.assertEqual(0, result.report["repair_rounds"])

    def test_failed_repair_is_saved_as_artifact(self):
        with tempfile.TemporaryDirectory() as root:
            manager, request = self._book(root)
            first_audit = _audit_json(
                False, plot_drift=["新增事件"], repair_instruction="删除新增事件"
            )
            second_audit = _audit_json(False, fact_drift=["地点改变"])
            client = _FakeClient([_plan_json(), first_audit, "修复后的正文", second_audit])
            service = AgentChapterPolishService(manager, client)
            plan = service.prepare(request)
            result = service.validate_and_repair(
                request, plan, "原文", "错误润色稿"
            )
            artifact = AgentRepository(
                manager.get_workspace("测试书")
            ).load_artifact(result.artifact_id)
        self.assertFalse(result.passed)
        self.assertEqual(1, result.report["repair_rounds"])
        self.assertEqual("修复后的正文", artifact["content"])


if __name__ == "__main__":
    unittest.main()

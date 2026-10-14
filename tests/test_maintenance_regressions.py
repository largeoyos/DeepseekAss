"""Offline integration checks for resumable GUI/CLI world maintenance."""
import copy
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

from control_main import main
from core.agent.world_maintenance import WorldBibleMaintenanceService
from core.control_service import ControlMaintenanceError
from core.control_writing import WritingControlMixin
from core.model_adapters import OpenAIChatAdapter
from core.model_config import migrate_legacy_config
from core.model_gateway import ModelGateway
from core.model_types import ModelResult, ProviderProtocol, TaskStage
from core.novel_manager import NovelManager
from core.world_bible import (
    WorldBible, _world_extraction_completion_options,
    extract_and_merge_world_bible, merge_extracted_world_bible_data,
)


class RecordingAdapter:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.requests = []

    def complete(self, request):
        self.requests.append(request)
        value = self.outcomes.pop(0)
        if isinstance(value, Exception):
            raise value
        return ModelResult(content=value)


def gateway_for(adapter):
    config = migrate_legacy_config({"text": {
        "api_key": "offline-test", "base_url": "https://example.invalid/v1", "model": "deepseek-v4-flash",
    }}, {})
    for provider in config.providers.values():
        provider.max_retries = 0
    # Make the GUI Agent route visibly different from the summary route.
    model = copy.deepcopy(next(iter(config.models.values())))
    model.model_id = "world-model"
    model.model = "deepseek-v4-pro"
    config.models[model.model_id] = model
    config.routes[TaskStage.AGENT.value].primary_model_id = model.model_id
    return ModelGateway(config, adapters={ProviderProtocol.OPENAI_CHAT: adapter}, book_route_loader=lambda _: {})


class MaintenanceRegressions(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.manager = NovelManager(bookshelf_root=self.temp.name)
        self.manager.create_book("book")
        self.manager.save_chapter_version("book", 1, "开篇", "老周来到旧邮船。", version=1)
        self.node_id = "ch0001_v001"

    def service(self, adapter):
        service = WritingControlMixin()
        service.manager = self.manager
        service._require = Mock()
        service._require_generation = Mock()
        service._book = lambda _: ("book", self.manager.load_meta("book"))
        service._book_identity = lambda *_: {"title": "book"}
        service._writing_gateway = lambda: gateway_for(adapter)
        service._writing_user_prompt = lambda: "离线测试用户偏好"
        return service

    def test_cli_resumes_world_after_failure_and_uses_gui_agent_route(self):
        adapter = RecordingAdapter(["已完成摘要", TimeoutError("simulated"), "{}"])
        with self.assertRaises(ControlMaintenanceError) as caught:
            self.service(adapter).maintain_chapter("book", self.node_id)
        self.assertEqual("已完成摘要", caught.exception.details["summary"])
        self.assertEqual("pending", caught.exception.details["status"])
        self.assertEqual(1, len(WorldBibleMaintenanceService(self.manager).list_pending("book")))
        # A new service instance represents an independent CLI retry process.
        result = self.service(adapter).maintain_chapter("book", self.node_id)
        self.assertEqual("completed", result["status"])
        self.assertTrue(result["summary_reused"])
        self.assertEqual([TaskStage.EXTRACTION, TaskStage.AGENT, TaskStage.AGENT],
                         [request.stage for request in adapter.requests])
        self.assertEqual("deepseek-v4-pro", adapter.requests[-1].model_profile.model)
        for request in adapter.requests:
            self.assertIn("离线测试用户偏好", request.messages[0]["content"])
        self.assertEqual([], WorldBibleMaintenanceService(self.manager).list_pending("book"))
        result = self.service(adapter).maintain_chapter("book", self.node_id)
        self.assertTrue(result["world_report"]["reused_snapshot"])
        self.assertEqual(3, len(adapter.requests))

    def test_force_failure_retry_keeps_force_for_world_but_not_summary(self):
        adapter = RecordingAdapter(["摘要", "{}", "新摘要", TimeoutError("simulated"), "{}"])
        service = self.service(adapter)
        service.maintain_chapter("book", self.node_id)
        with self.assertRaises(ControlMaintenanceError):
            service.maintain_chapter("book", self.node_id, force=True)
        result = self.service(adapter).maintain_chapter("book", self.node_id)
        self.assertTrue(result["summary_reused"])
        self.assertFalse(result["world_report"]["reused_snapshot"])
        self.assertEqual(5, len(adapter.requests))

    def test_changed_content_invalidates_completed_steps(self):
        adapter = RecordingAdapter(["摘要", "{}", "更新摘要", "{}"])
        service = self.service(adapter)
        service.maintain_chapter("book", self.node_id)
        with patch.object(self.manager, "read_chapter_node", return_value="改写后的正文"):
            result = service.maintain_chapter("book", self.node_id)
        self.assertFalse(result["summary_reused"])
        self.assertFalse(result["world_report"]["reused_snapshot"])
        self.assertEqual(4, len(adapter.requests))

    def test_partial_extraction_is_pending_and_does_not_replace_good_snapshot(self):
        adapter = RecordingAdapter(["摘要", "{}", "更新摘要", "{}", TimeoutError("chunk failed")])
        service = self.service(adapter)
        service.maintain_chapter("book", self.node_id)
        before = self.manager.load_world_bible("book").chapter_snapshots
        with patch("core.world_bible._split_chapter_for_world_extraction", return_value=["老周", "旧邮船"]):
            with self.assertRaises(ControlMaintenanceError):
                service.maintain_chapter("book", self.node_id, force=True)
        self.assertEqual(before, self.manager.load_world_bible("book").chapter_snapshots)

    def test_cli_pending_has_nonzero_exit_and_structured_progress(self):
        error = ControlMaintenanceError("待重试", {"status": "pending", "summary": "已保存"})
        output = io.StringIO()
        with patch("control_main.execute", side_effect=error), redirect_stdout(output):
            code = main(["chapter", "maintain", "--book", "book", "--node-id", self.node_id, "--force"])
        payload = json.loads(output.getvalue())
        self.assertNotEqual(0, code)
        self.assertFalse(payload["ok"])
        self.assertEqual("maintenance_pending", payload["error"]["code"])
        self.assertEqual("已保存", payload["error"]["details"]["summary"])

    def test_gateway_preserves_disabled_thinking_through_wire_serialization(self):
        adapter = RecordingAdapter(["{}"])
        gateway_for(adapter).compat_client("chapter_tree_world_bible").chat.completions.create(
            model="deepseek-v4-flash", messages=[{"role": "user", "content": "提取"}],
            reasoning_effort="high", **_world_extraction_completion_options("deepseek-v4-flash"),
        )
        wire = OpenAIChatAdapter._kwargs(adapter.requests[0], stream=False)
        self.assertEqual({"type": "disabled"}, wire["extra_body"]["thinking"])
        self.assertNotIn("reasoning_effort", wire)
        self.assertEqual({"type": "json_object"}, wire["response_format"])

    def test_dedup_persists_through_actual_manager_rebuild(self):
        adapter = RecordingAdapter([
            json.dumps({"characters": [{"name": "老周"}, {"name": "周德海"}],
                        "locations": [{"name": "旧邮船", "description": "旧船"},
                                      {"name": "退役邮船", "description": "退役"}]}),
            json.dumps({"groups": [["老周", "周德海"]]}),
            json.dumps({"groups": [["旧邮船", "退役邮船"]]}),
        ])
        client = gateway_for(adapter).compat_client("chapter_tree_world_bible")
        self.manager.extract_world_bible_for_node(client, "book", self.node_id, model="deepseek-v4-flash")
        for _ in range(2):
            bible = self.manager.load_world_bible("book")
            self.assertEqual(1, len(bible.characters))
            self.assertEqual(1, len(bible.locations))
            self.assertIn("退役", bible.locations[0].description)
            entry = bible.chapter_snapshots[self.node_id]
            self.assertTrue(entry["extraction_complete"])
            self.assertTrue(entry["content_hash"])
            self.manager.rebuild_world_bible_from_active(None, "book")
        self.assertEqual(3, len(adapter.requests))

    def test_dedup_snapshot_does_not_copy_previous_chapter_facts(self):
        existing = merge_extracted_world_bible_data(WorldBible(), {
            "locations": [{"name": "旧邮船", "description": "只属于前一章的历史事实"}],
        }, chapter_num=1, chapter_version=1, store_chapter_entry=True, run_dedup=False)
        previous = copy.deepcopy(existing.chapter_snapshots["ch0001_v001"])
        adapter = RecordingAdapter([
            json.dumps({"locations": [{"name": "退役邮船", "description": "本章"}]}),
            json.dumps({"groups": [["旧邮船", "退役邮船"]]}),
        ])
        result = extract_and_merge_world_bible(gateway_for(adapter).compat_client("chapter_tree_world_bible"),
                    "本章正文", 2, existing, "deepseek-v4-flash", chapter_version=1)
        entry = result.chapter_snapshots["ch0002_v001"]
        self.assertEqual("本章", entry["data"]["locations"][0]["description"])
        self.assertEqual(previous, result.chapter_snapshots["ch0001_v001"])


if __name__ == "__main__":
    unittest.main()

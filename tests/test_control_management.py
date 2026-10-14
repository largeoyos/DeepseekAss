"""Permission and persistence checks for CLI writing management."""
import io
import json
import os
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from control_main import main
from core.auth_manager import AuthManager
from core.control_auth import ControlGrantStore
from core.control_service import ControlService, ControlPermissionError, ControlValidationError, ControlConflictError
from tests import test_control_interface as fixtures


class ControlManagementTests(unittest.TestCase):
    setUp = fixtures.ControlInterfaceTests.setUp
    tearDown = fixtures.ControlInterfaceTests.tearDown

    def full_service(self):
        token, _ = ControlGrantStore.create(self.username, self.password, name="manager",
            scopes={"read", "propose", "manage", "approve", "generate"})
        return ControlService(ControlGrantStore.resolve(self.username, token))

    def test_existing_grants_cannot_manage_or_apply(self):
        for name, args in [
            ("create_project", {"title": "new"}),
            ("update_project", {"book": self.title, "fields": {"genre": "new"}}),
            ("activate_branch", {"book": self.title, "node_id": "ch0001_v002"}),
            ("apply_change", {"book": self.title, "change_set_id": "missing"}),
            ("discard_change", {"book": self.title, "change_set_id": "missing"}),
            ("configure_models", {"config": {"model": "new"}}),
            ("configure_generation", {"enabled": True}),
            ("rebuild_world", {"book": self.title}),
        ]:
            with self.subTest(name=name), self.assertRaises(ControlPermissionError):
                self.service.invoke(name, args)

    def test_new_chapter_binds_parent_at_proposal_and_can_be_discarded(self):
        service = self.full_service()
        change = service.propose_new_chapter(self.title, 2, "第二章", "完整的新章正文")
        preview = service.get_change_set(self.title, change["change_set_id"], True)
        self.assertEqual("ch0001_v001", preview["change_set"]["operations"][0]["payload"]["parent_id"])
        service.activate_branch(self.title, node_id="ch0001_v002")
        service.apply_change(self.title, change["change_set_id"])
        self.assertEqual("ch0001_v001", service.read_current_chapter(self.title)["node"]["parent_id"])
        rejected = service.propose_new_chapter(self.title, 3, "第三章", "放弃的稿件")
        service.discard_change(self.title, rejected["change_set_id"])
        self.assertEqual("rejected", service.get_change_set(self.title, rejected["change_set_id"])["change_set"]["status"])
        with self.assertRaises(ControlValidationError):
            service.propose_new_chapter(self.title, 4, "错误父节点", "正文", parent_id="missing")

    def test_create_and_update_persist_with_stable_id_and_snapshot(self):
        service = self.full_service()
        project = service.create_project("新书", {"genre": "悬疑"})["project"]
        service.update_project(project["book_id"], {"background_story": "港口", "author_plan": "五章"})
        meta = service.manager.load_meta("新书")
        self.assertEqual("港口", meta.background_story)
        self.assertEqual(project["book_id"], meta.book_id)
        with self.assertRaises(ControlConflictError):
            service.create_project("新书")
        with self.assertRaises(ControlValidationError):
            service.update_project("新书", {"active_path": []})
        with self.assertRaises(ValueError):
            service.create_project("..")

    def test_revision_preview_apply_and_branch_roundtrip(self):
        service = self.full_service()
        content = "修订正文" * 300
        proposed = service.propose_chapter_revision(self.title, "ch0001_v001", "修订", content)
        change_id = proposed["change_set_id"]
        preview = service.get_change_set(self.title, change_id)
        self.assertNotIn("content", preview["change_set"]["operations"][0]["payload"])
        full = service.get_change_set(self.title, change_id, include_content=True)
        self.assertEqual(content, full["change_set"]["operations"][0]["payload"]["content"])
        result = service.apply_change(self.title, change_id)
        node_id = result["change_set"]["operations"][0]["payload"]["created_node_id"]
        self.assertEqual("ch0001_v001", service.read_current_chapter(self.title)["node"]["id"])
        service.activate_branch(self.title, node_id=node_id)
        self.assertEqual(content, service.read_current_chapter(self.title)["content"])
        service.activate_branch(self.title, node_id="ch0001_v001")
        self.assertIn("ABCDEFGHIJKLMNOPQRSTUVWXYZ", service.read_current_chapter(self.title)["content"])
        with self.assertRaises(ControlValidationError):
            service.activate_branch(self.title, node_id=node_id, tree_id="main")

    def test_model_configuration_is_encrypted_and_status_is_sanitized(self):
        service = self.full_service()
        result = service.configure_models({"text": {"api_key": "unit-test-secret", "base_url": "https://example.com/v1", "model": "writer"}})
        self.assertNotIn("unit-test-secret", json.dumps(result))
        self.assertTrue(result["models"][0]["api_key_configured"])
        self.assertTrue(result["models"][0]["provider_enabled"])
        with open(os.path.join(service.user_dir, "config.enc"), "rb") as handle:
            self.assertNotIn(b"unit-test-secret", handle.read())
        service.configure_generation(True)
        self.assertTrue(ControlService(service.grant).settings["control_agent_enabled"])
        service.configure_generation(False)
        with self.assertRaises(ControlPermissionError):
            service.plan_chapter(self.title, "下一章")

    def test_plan_read_rejects_path_traversal(self):
        with self.assertRaises(ControlValidationError):
            self.service.read_plan(self.title, "../../config.enc")

    def test_cli_register_from_environment_and_invalid_scopes_no_account(self):
        output = io.StringIO()
        with patch.dict(os.environ, {"CLI_TEST_PASSWORD": "unit-new-pass"}), redirect_stdout(output):
            code = main(["--username", "new-cli", "auth", "register", "--password-env", "CLI_TEST_PASSWORD", "--allow-manage", "--allow-approve"])
        self.assertEqual(0, code)
        data = json.loads(output.getvalue())["data"]
        self.assertNotIn("unit-new-pass", output.getvalue())
        self.assertIn("manage", ControlGrantStore.resolve("new-cli", data["token"]).scopes)
        output = io.StringIO()
        with patch.dict(os.environ, {"CLI_TEST_PASSWORD": "unit-new-pass"}), redirect_stdout(output):
            code = main(["--username", "invalid-cli", "auth", "register", "--password-env", "CLI_TEST_PASSWORD", "--read-only", "--allow-approve"])
        self.assertEqual(2, code)
        self.assertFalse(AuthManager.authenticate("invalid-cli", "unit-new-pass")[0])


if __name__ == "__main__":
    unittest.main()


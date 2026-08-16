import io
import asyncio
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta
from unittest.mock import patch

import core.auth_manager as auth_module
from control_main import main as control_main
from core.agent.changes import ChangeSetService
from core.agent.repository import AgentRepository
from core.auth_manager import AuthManager
from core.control_auth import ControlAuthError, ControlGrantStore
from core.control_service import ControlPermissionError, ControlService, ControlValidationError
from core.novel_manager import NovelManager
from core.world_bible import dict_to_world_bible, world_bible_to_dict


class ControlInterfaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_users_dir = auth_module.USERS_DIR
        self.old_users_db = auth_module.USERS_DB
        auth_module.USERS_DIR = os.path.join(self.temp.name, "users")
        auth_module.USERS_DB = os.path.join(auth_module.USERS_DIR, "users.json")
        self.username = "alice"
        self.password = "secret123"
        self.enc_key = AuthManager.register(self.username, self.password)
        self.manager = NovelManager(
            os.path.join(AuthManager.get_user_dir(self.username), "bookshelf"),
            crypto=AuthManager,
            enc_key=self.enc_key,
        )
        self.title = "控制测试"
        self.manager.create_book(self.title)
        self.manager.save_chapter_version(self.title, 1, "起点", "第一章正文-ABCDEFGHIJKLMNOPQRSTUVWXYZ", version=1)
        self.manager.save_chapter_version(self.title, 1, "平行版本", "平行线包含蓝月", version=2, parent_id="ch0000_v000")
        self.manager.switch_active_node(self.title, "ch0001_v001")
        self.manager.save_world_bible(self.title, dict_to_world_bible({
            "characters": [{"id": "char-a", "name": "阿青", "traits": "谨慎"}],
            "locations": [{"id": "loc-city", "name": "白城", "description": "北方城邦"}],
        }), force=True)
        self.token, self.grant_meta = ControlGrantStore.create(
            self.username,
            self.password,
            name="test-agent",
            scopes={"read", "propose"},
            expires_days=90,
        )
        self.service = ControlService(ControlGrantStore.resolve(self.username, self.token))

    def tearDown(self):
        auth_module.USERS_DIR = self.old_users_dir
        auth_module.USERS_DB = self.old_users_db
        self.temp.cleanup()

    def test_grant_is_scoped_revocable_and_does_not_store_raw_token(self):
        registry_path = ControlGrantStore._registry_path(self.username)
        with open(registry_path, "r", encoding="utf-8") as handle:
            raw = handle.read()
        self.assertNotIn(self.token, raw)
        self.assertNotIn(self.password, raw)
        self.assertNotIn(self.enc_key.decode("ascii"), raw)
        self.assertEqual(("propose", "read"), tuple(sorted(self.service.grant.scopes)))
        self.service.invoke("list_books")
        audit_dir = os.path.join(AuthManager.get_user_dir(self.username), ".deepseekass", "control_audit")
        audit_path = os.path.join(audit_dir, os.listdir(audit_dir)[0])
        audit = AuthManager.decrypt_json(self.enc_key, audit_path)
        self.assertEqual("list_books", audit["tool"])
        self.assertNotIn(self.token, json.dumps(audit))
        self.assertTrue(ControlGrantStore.revoke(self.username, self.password, self.grant_meta["grant_id"]))
        with self.assertRaises(ControlAuthError):
            ControlGrantStore.resolve(self.username, self.token)

    def test_wrong_and_expired_tokens_are_rejected(self):
        with self.assertRaises(ControlAuthError):
            ControlGrantStore.resolve(self.username, self.token + "wrong")
        data = ControlGrantStore._load(self.username)
        data["grants"][0]["expires_at"] = (datetime.now().astimezone() - timedelta(seconds=1)).isoformat()
        ControlGrantStore._save(self.username, data)
        with self.assertRaises(ControlAuthError):
            ControlGrantStore.resolve(self.username, self.token)

    def test_read_tree_paged_content_and_search_all_branches(self):
        books = self.service.invoke("list_books")
        book_id = books["books"][0]["book_id"]
        tree = self.service.invoke("get_chapter_tree", {"book": book_id, "limit": 2})
        self.assertEqual(2, len(tree["nodes"]))
        self.assertIsNotNone(tree["page"]["next_offset"])
        first = self.service.invoke("read_chapter", {"book": book_id, "node_id": "ch0001_v001", "start": 0, "max_chars": 8})
        second = self.service.invoke("read_chapter", {"book": book_id, "node_id": "ch0001_v001", "start": first["page"]["next_start"], "max_chars": 100})
        self.assertEqual("第一章正文-ABCDEFGHIJKLMNOPQRSTUVWXYZ", first["content"] + second["content"])
        active = self.service.invoke("search_chapters", {"book": book_id, "query": "蓝月", "scope": "active"})
        all_nodes = self.service.invoke("search_chapters", {"book": book_id, "query": "蓝月", "scope": "all"})
        self.assertEqual([], active["results"])
        self.assertEqual("ch0001_v002", all_nodes["results"][0]["node_id"])

    def test_world_read_search_and_patch_requires_approval(self):
        overview = self.service.invoke("get_world_bible", {"book": self.title})
        self.assertEqual(1, overview["categories"]["character"])
        found = self.service.invoke("search_world_bible", {"book": self.title, "query": "谨慎"})
        self.assertEqual("char-a", found["results"][0]["id"])
        before = world_bible_to_dict(self.manager.load_world_bible(self.title))
        proposed = self.service.invoke("propose_world_bible_patch", {
            "book": self.title,
            "operations": [{
                "operation": "patch",
                "entity_type": "character",
                "entity_id": "char-a",
                "payload": {"traits": "果断"},
                "scope": "global",
                "anchor_node_id": "",
            }],
            "reason": "更新人物状态",
        })
        self.assertFalse(proposed["applied"])
        self.assertEqual(before, world_bible_to_dict(self.manager.load_world_bible(self.title)))
        repo = AgentRepository(self.manager.get_workspace(self.title))
        ChangeSetService(self.manager, self.title, repo).approve(proposed["change_set_id"])
        after = world_bible_to_dict(self.manager.load_world_bible(self.title))
        self.assertEqual("果断", next(item for item in after["characters"] if item["id"] == "char-a")["traits"])

    def test_invalid_world_scope_is_rejected_before_proposal(self):
        with self.assertRaises(ControlValidationError):
            self.service.propose_world_bible_patch(self.title, [{
                "operation": "patch",
                "entity_type": "character",
                "entity_id": "char-a",
                "payload": {"traits": "错误"},
                "scope": "branch",
                "anchor_node_id": "missing",
            }])

    def test_all_supported_world_patch_actions_apply(self):
        operations = [
            {"operation": "patch", "entity_type": "character", "entity_id": "char-a", "payload": {"traits": "坚定"}, "scope": "global"},
            {"operation": "create", "entity_type": "character", "entity_id": "char-b", "payload": {"id": "char-b", "name": "阿白"}, "scope": "global"},
            {"operation": "create", "entity_type": "character", "entity_id": "char-c", "payload": {"id": "char-c", "name": "阿赤"}, "scope": "global"},
            {"operation": "archive", "entity_type": "character", "entity_id": "char-c", "payload": {}, "scope": "global"},
            {"operation": "merge", "entity_type": "character", "entity_id": "char-a", "payload": {}, "source_ids": ["char-b"], "scope": "global"},
            {"operation": "create", "entity_type": "world_rule", "entity_id": "rule-new", "payload": {"id": "rule-new", "name": "新规则", "content": "规则正文"}, "scope": "global"},
            {"operation": "supersede", "entity_type": "world_rule", "entity_id": "rule-new", "payload": {}, "supersedes": ["rule-old"], "scope": "global"},
        ]
        proposed = self.service.propose_world_bible_patch(self.title, operations, "批量维护")
        repo = AgentRepository(self.manager.get_workspace(self.title))
        ChangeSetService(self.manager, self.title, repo).approve(proposed["change_set_id"])
        characters = world_bible_to_dict(self.manager.load_world_bible(self.title))["characters"]
        char_a = next(item for item in characters if item["id"] == "char-a")
        char_b = next(item for item in characters if item["id"] == "char-b")
        char_c = next(item for item in characters if item["id"] == "char-c")
        self.assertEqual("坚定", char_a["traits"])
        self.assertTrue(char_b["hidden"])
        self.assertTrue(char_c["hidden"])
        rule = next(item for item in world_bible_to_dict(self.manager.load_world_bible(self.title))["world_rules"] if item["id"] == "rule-new")
        self.assertEqual("rule-old", rule["supersedes"])

    def test_chapter_revision_creates_inactive_version_after_approval(self):
        active_before = list(self.manager.ensure_chapter_tree(self.title).active_path)
        proposed = self.service.propose_chapter_revision(
            self.title,
            "ch0001_v001",
            "起点修订",
            "修订后的正文",
            "语气调整",
        )
        self.assertEqual([1, 2], [item["v"] for item in self.manager.get_chapter_versions(self.title, 1)])
        repo = AgentRepository(self.manager.get_workspace(self.title))
        approved = ChangeSetService(self.manager, self.title, repo).approve(proposed["change_set_id"])
        created = approved.operations[0].payload["created_node_id"]
        self.assertEqual("修订后的正文", self.manager.read_chapter_node(self.title, created))
        self.assertEqual(active_before, self.manager.ensure_chapter_tree(self.title).active_path)

    def test_stale_chapter_revision_is_rejected(self):
        proposed = self.service.propose_chapter_revision(self.title, "ch0001_v001", "起点", "拟议正文")
        self.manager.save_chapter_version(self.title, 1, "起点", "同时发生的人工修改", version=1)
        repo = AgentRepository(self.manager.get_workspace(self.title))
        with self.assertRaises(Exception) as caught:
            ChangeSetService(self.manager, self.title, repo).approve(proposed["change_set_id"])
        self.assertIn("已变化", str(caught.exception))
        self.assertEqual([1, 2], [item["v"] for item in self.manager.get_chapter_versions(self.title, 1)])

    def test_read_only_grant_cannot_propose(self):
        token, _grant = ControlGrantStore.create(
            self.username, self.password, name="reader", scopes={"read"}, expires_days=1
        )
        reader = ControlService(ControlGrantStore.resolve(self.username, token))
        with self.assertRaises(ControlPermissionError):
            reader.propose_chapter_revision(self.title, "ch0001_v001", "标题", "正文")

    def test_cli_returns_stable_json_envelope(self):
        old_token = os.environ.get("DEEPSEEKASS_CONTROL_TOKEN")
        os.environ["DEEPSEEKASS_CONTROL_TOKEN"] = self.token
        output = io.StringIO()
        try:
            with redirect_stdout(output):
                code = control_main(["--username", self.username, "books", "list"])
        finally:
            if old_token is None:
                os.environ.pop("DEEPSEEKASS_CONTROL_TOKEN", None)
            else:
                os.environ["DEEPSEEKASS_CONTROL_TOKEN"] = old_token
        self.assertEqual(0, code)
        payload = json.loads(output.getvalue())
        self.assertTrue(payload["ok"])
        self.assertEqual(self.title, payload["data"]["books"][0]["title"])

    def test_cli_auth_grant_and_human_approval_do_not_need_automation_token(self):
        grant_output = io.StringIO()
        with patch("control_main._password", return_value=self.password), redirect_stdout(grant_output):
            code = control_main(["--username", self.username, "auth", "grant", "--name", "cli-reader", "--read-only"])
        self.assertEqual(0, code)
        grant_payload = json.loads(grant_output.getvalue())
        self.assertEqual(["read"], grant_payload["data"]["grant"]["scopes"])

        proposed = self.service.propose_chapter_revision(self.title, "ch0001_v001", "CLI 修订", "CLI 审批正文")
        old_token = os.environ.pop("DEEPSEEKASS_CONTROL_TOKEN", None)
        output, errors = io.StringIO(), io.StringIO()
        try:
            with (
                patch("control_main._password", return_value=self.password),
                patch("builtins.input", return_value=proposed["change_set_id"]),
                redirect_stdout(output),
                redirect_stderr(errors),
            ):
                code = control_main(["--username", self.username, "changes", "approve", "--book", self.title, proposed["change_set_id"]])
        finally:
            if old_token is not None:
                os.environ["DEEPSEEKASS_CONTROL_TOKEN"] = old_token
        self.assertEqual(0, code)
        self.assertTrue(json.loads(output.getvalue())["ok"])
        self.assertIn("approval_preview", errors.getvalue())
        self.assertEqual("CLI 审批正文", self.manager.read_chapter_node(self.title, "ch0001_v003"))

    def test_cli_argument_error_is_json_with_exit_code_two(self):
        output = io.StringIO()
        with redirect_stdout(output):
            code = control_main(["books"])
        self.assertEqual(2, code)
        payload = json.loads(output.getvalue())
        self.assertFalse(payload["ok"])
        self.assertEqual("invalid_arguments", payload["error"]["code"])

    def test_mcp_discovers_strict_tools_and_calls_structured_result(self):
        from mcp import Client
        from core.control_mcp import create_mcp_server

        async def exercise():
            server = create_mcp_server(self.service)
            tools = await server.list_tools()
            self.assertEqual(13, len(tools))
            self.assertTrue(all(item.input_schema for item in tools))
            self.assertTrue(all(item.output_schema for item in tools))
            patch_tool = next(item for item in tools if item.name == "propose_world_bible_patch")
            operation_schema = patch_tool.input_schema["$defs"]["WorldPatchOperation"]
            self.assertEqual(["create", "patch", "archive", "supersede", "merge"], operation_schema["properties"]["operation"]["enum"])
            async with Client(server) as client:
                discovered = await client.list_tools()
                self.assertEqual(13, len(discovered.tools))
                result = await client.call_tool("list_books", {})
                self.assertFalse(result.is_error)
                self.assertEqual(self.title, result.structured_content["books"][0]["title"])

        asyncio.run(exercise())

    def test_mcp_stdio_handshake_and_stdout_protocol(self):
        from mcp import Client
        from mcp.client.stdio import StdioServerParameters, stdio_client

        script = (
            "import os; import core.auth_manager as auth; "
            "auth.USERS_DIR=os.environ['DEEPSEEKASS_TEST_USERS_DIR']; "
            "auth.USERS_DB=os.path.join(auth.USERS_DIR,'users.json'); "
            "from control_main import main; "
            "raise SystemExit(main(['--username','alice','mcp']))"
        )

        async def exercise():
            params = StdioServerParameters(
                command=sys.executable,
                args=["-c", script],
                cwd=os.path.dirname(os.path.dirname(__file__)),
                env={
                    "DEEPSEEKASS_TEST_USERS_DIR": auth_module.USERS_DIR,
                    "DEEPSEEKASS_CONTROL_TOKEN": self.token,
                },
            )
            async with Client(stdio_client(params), read_timeout_seconds=15) as client:
                tools = await client.list_tools()
                self.assertEqual(13, len(tools.tools))
                result = await client.call_tool("get_project", {"book": self.title})
                self.assertFalse(result.is_error)
                self.assertEqual(self.title, result.structured_content["project"]["title"])

        asyncio.run(exercise())

    def test_web_review_and_approval_support_external_chapter_proposal(self):
        from fastapi.testclient import TestClient
        from web.server import create_app
        from web.services import WebRuntime

        proposed = self.service.propose_chapter_revision(
            self.title, "ch0001_v001", "网页审批修订", "网页审批后的正文", "外部 AI 修订"
        )
        client = TestClient(create_app(WebRuntime()))
        login = client.post("/api/auth/login", json={"username": self.username, "password": self.password})
        self.assertEqual(200, login.status_code, login.text)
        headers = {"Authorization": f"Bearer {login.json()['token']}"}
        state = client.get(f"/api/books/{self.title}/agent/state", headers=headers)
        self.assertEqual(200, state.status_code, state.text)
        change = next(item for item in state.json()["pending_changes"] if item["change_set_id"] == proposed["change_set_id"])
        self.assertEqual("外部 AI 控制接口", change["review"]["origin_label"])
        review = change["review"]["operations"][0]
        self.assertFalse(review["will_activate"])
        self.assertIn("网页审批后的正文", review["diff"])
        active_before = list(self.manager.ensure_chapter_tree(self.title).active_path)
        approved = client.post(
            f"/api/books/{self.title}/agent/changes/approve",
            headers=headers,
            json={"change_set_id": proposed["change_set_id"]},
        )
        self.assertEqual(200, approved.status_code, approved.text)
        self.assertEqual(active_before, self.manager.ensure_chapter_tree(self.title).active_path)


if __name__ == "__main__":
    unittest.main()

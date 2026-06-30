from __future__ import annotations

import os
import tempfile
import time
import unittest
from types import SimpleNamespace

from fastapi.testclient import TestClient

import core.auth_manager as auth_module
from core.auth_manager import AuthManager
from web.server import create_app
from web.services import WebRuntime


class FakeOpenAIClient:
    def __init__(self) -> None:
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, *, stream=False, **_kwargs):
        if stream:
            return iter([SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="正文"))])])
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="OK"))])


class WebFullFeatureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.old_users_dir = auth_module.USERS_DIR
        self.old_users_db = auth_module.USERS_DB
        auth_module.USERS_DIR = os.path.join(self.tmp.name, "users")
        auth_module.USERS_DB = os.path.join(auth_module.USERS_DIR, "users.json")
        os.makedirs(auth_module.USERS_DIR, exist_ok=True)
        self.enc_key = AuthManager.register("alice", "pass123")
        self.runtime = WebRuntime(token_ttl_seconds=60, client_factory=lambda _config: FakeOpenAIClient())
        self.client = TestClient(create_app(self.runtime))
        token = self.client.post("/api/auth/login", json={"username": "alice", "password": "pass123"}).json()["token"]
        self.headers = {"Authorization": f"Bearer {token}"}

    def tearDown(self) -> None:
        auth_module.USERS_DIR = self.old_users_dir
        auth_module.USERS_DB = self.old_users_db
        self.tmp.cleanup()

    def wait_task(self, task_id: str) -> dict:
        for _ in range(50):
            task = self.client.get(f"/api/tasks/{task_id}", headers=self.headers).json()["task"]
            if task["status"] in {"completed", "failed", "cancelled"}:
                return task
            time.sleep(0.05)
        self.fail(f"task did not finish: {task_id}")

    def test_sensitive_api_settings_markdown_role_and_diagnostics(self):
        denied = self.client.put(
            "/api/settings/api",
            headers=self.headers,
            json={"text": {"api_key": "key", "base_url": "http://example.invalid", "model": "m"}},
        )
        self.assertEqual(denied.status_code, 403)

        ticket = self.client.post(
            "/api/auth/confirm",
            headers=self.headers,
            json={"password": "pass123"},
        ).json()["sensitive_ticket"]
        saved = self.client.put(
            "/api/settings/api",
            headers={**self.headers, "X-Sensitive-Ticket": ticket},
            json={"text": {"api_key": "secret-key", "base_url": "http://example.invalid", "model": "fake-model"}},
        )
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertTrue(saved.json()["api"]["text"]["api_key_configured"])

        note = self.client.put(
            "/api/markdown/file",
            headers=self.headers,
            json={"path": "ideas/one.md", "content": "# Idea"},
        )
        self.assertEqual(note.status_code, 200, note.text)
        read_note = self.client.get("/api/markdown/file?path=ideas%2Fone.md", headers=self.headers)
        self.assertEqual(read_note.json()["content"], "# Idea")

        role = self.client.post(
            "/api/roleplay/characters",
            headers=self.headers,
            json={"profile": {"name": "A", "identity": "test"}},
        )
        self.assertEqual(role.status_code, 200, role.text)
        roles = self.client.get("/api/roleplay/characters", headers=self.headers).json()["book"]["profiles"]
        self.assertEqual(roles[0]["name"], "A")

        diag = self.client.get("/api/diagnostics", headers=self.headers)
        self.assertEqual(diag.status_code, 200)

    def test_chapter_tree_world_and_continuation_import(self):
        self.client.post("/api/books", headers=self.headers, json={"title": "Book"})
        imported = self.client.post(
            "/api/continuation/import",
            headers=self.headers,
            json={"title": "Book", "sections": [{"title": "One", "content": "第一章正文"}]},
        )
        self.assertEqual(imported.status_code, 200, imported.text)
        task = self.wait_task(imported.json()["task_id"])
        self.assertEqual(task["status"], "completed", task)

        tree = self.client.get("/api/books/Book/chapter-tree", headers=self.headers)
        self.assertEqual(tree.status_code, 200, tree.text)
        nodes = [node for node in tree.json()["nodes"] if not node.get("virtual")]
        self.assertEqual(len(nodes), 1)

        world = self.client.get("/api/books/Book/world", headers=self.headers)
        data = world.json()["world"]
        data.setdefault("rules", []).append("rule")
        saved = self.client.put("/api/books/Book/world", headers=self.headers, json={"world": data})
        self.assertEqual(saved.status_code, 200, saved.text)

        chapters = self.client.get("/api/books/Book/chapters", headers=self.headers).json()["chapters"]
        self.assertEqual(chapters[0]["chapter_num"], 1)


if __name__ == "__main__":
    unittest.main()

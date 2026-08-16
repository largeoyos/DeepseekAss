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
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=self.create_completion)
        )

    def create_completion(self, *, stream=False, **_kwargs):
        if stream:
            return iter([
                self._chunk("第一段正文，人物进入新的场景。"),
                self._chunk("第二段正文，冲突被推到台前。"),
                self._chunk("结尾留下继续写作的余波。"),
            ])
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="本章摘要：测试。"))]
        )

    @staticmethod
    def _chunk(text: str):
        return SimpleNamespace(
            choices=[SimpleNamespace(delta=SimpleNamespace(content=text))]
        )


class WebMvpTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.old_users_dir = auth_module.USERS_DIR
        self.old_users_db = auth_module.USERS_DB
        auth_module.USERS_DIR = os.path.join(self.tmp.name, "users")
        auth_module.USERS_DB = os.path.join(auth_module.USERS_DIR, "users.json")
        os.makedirs(auth_module.USERS_DIR, exist_ok=True)
        self.enc_key = AuthManager.register("alice", "pass123")
        self.runtime = WebRuntime(
            token_ttl_seconds=60,
            client_factory=lambda _api_config: FakeOpenAIClient(),
        )
        self.client = TestClient(create_app(self.runtime))

    def tearDown(self) -> None:
        auth_module.USERS_DIR = self.old_users_dir
        auth_module.USERS_DB = self.old_users_db
        self.tmp.cleanup()

    def login(self) -> str:
        response = self.client.post(
            "/api/auth/login",
            json={"username": "alice", "password": "pass123"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["token"]

    def auth_headers(self, token: str) -> dict:
        return {"Authorization": f"Bearer {token}"}

    def configure_api(self) -> None:
        user_dir = AuthManager.get_user_dir("alice")
        AuthManager.encrypt_json(self.enc_key, os.path.join(user_dir, "config.enc"), {
            "text": {
                "api_key": "test-key",
                "base_url": "http://example.invalid",
                "model": "deepseek-v4-flash",
            },
            "image": {},
        })

    def test_login_success_failure_and_protected_access(self):
        bad = self.client.post(
            "/api/auth/login",
            json={"username": "alice", "password": "wrong"},
        )
        self.assertEqual(bad.status_code, 401)

        token = self.login()
        ok = self.client.get("/api/books", headers=self.auth_headers(token))
        self.assertEqual(ok.status_code, 200)

        missing = self.client.get("/api/books")
        self.assertEqual(missing.status_code, 401)

    def test_register_creates_user_and_returns_authenticated_session(self):
        registered = self.client.post(
            "/api/auth/register",
            json={"username": "bob", "password": "secure123"},
        )
        self.assertEqual(registered.status_code, 200, registered.text)
        token = registered.json()["token"]
        session = self.client.get(
            "/api/session",
            headers=self.auth_headers(token),
        )
        self.assertEqual(session.status_code, 200, session.text)
        self.assertEqual(session.json()["user"]["username"], "bob")

        duplicate = self.client.post(
            "/api/auth/register",
            json={"username": "bob", "password": "secure123"},
        )
        self.assertEqual(duplicate.status_code, 400)

        weak = self.client.post(
            "/api/auth/register",
            json={"username": "charlie", "password": "123"},
        )
        self.assertEqual(weak.status_code, 400)

    def test_token_expiry_rejects_access(self):
        runtime = WebRuntime(token_ttl_seconds=-1)
        client = TestClient(create_app(runtime))
        response = client.post(
            "/api/auth/login",
            json={"username": "alice", "password": "pass123"},
        )
        token = response.json()["token"]
        denied = client.get("/api/books", headers=self.auth_headers(token))
        self.assertEqual(denied.status_code, 401)

    def test_books_and_meta_crud(self):
        token = self.login()
        headers = self.auth_headers(token)
        created = self.client.post("/api/books", headers=headers, json={"title": "测试书"})
        self.assertEqual(created.status_code, 200, created.text)

        books = self.client.get("/api/books", headers=headers).json()["books"]
        self.assertEqual([book["title"] for book in books], ["测试书"])

        saved = self.client.put(
            "/api/books/%E6%B5%8B%E8%AF%95%E4%B9%A6/meta",
            headers=headers,
            json={
                "protagonist_bio": "主角设定",
                "background_story": "世界背景",
                "writing_demand": "克制但有张力",
                "author_plan": "第一卷建立目标",
                "genre": "fantasy",
                "style_tone": "serious",
            },
        )
        self.assertEqual(saved.status_code, 200, saved.text)
        meta = self.client.get(
            "/api/books/%E6%B5%8B%E8%AF%95%E4%B9%A6/meta",
            headers=headers,
        ).json()["meta"]
        self.assertEqual(meta["protagonist_bio"], "主角设定")
        self.assertEqual(meta["style_tone"], "serious")

    def test_generate_without_api_config_returns_clear_error(self):
        token = self.login()
        headers = self.auth_headers(token)
        self.client.post("/api/books", headers=headers, json={"title": "无配置书"})
        response = self.client.post(
            "/api/books/%E6%97%A0%E9%85%8D%E7%BD%AE%E4%B9%A6/generate",
            headers=headers,
            json={"chapter_title": "开端", "plot": "开始", "target_words": 800},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("桌面端设置中心", response.json()["detail"])

    def test_model_center_masks_secrets_and_saves_global_and_book_routes(self):
        self.configure_api()
        token = self.login()
        headers = self.auth_headers(token)
        center = self.client.get("/api/model-center", headers=headers)
        self.assertEqual(200, center.status_code, center.text)
        config = center.json()["config"]
        self.assertEqual(2, config["schema_version"])
        provider = next(iter(config["providers"].values()))
        self.assertNotEqual("test-key", provider["api_key"])
        self.assertTrue(provider["api_key_configured"])
        provider["allow_insecure_http"] = True

        confirmed = self.client.post(
            "/api/auth/confirm", headers=headers, json={"password": "pass123"},
        )
        ticket_headers = {**headers, "X-Sensitive-Ticket": confirmed.json()["sensitive_ticket"]}
        saved = self.client.put(
            "/api/model-center", headers=ticket_headers, json={"config": config},
        )
        self.assertEqual(200, saved.status_code, saved.text)
        config_path = os.path.join(AuthManager.get_user_dir("alice"), "config.enc")
        with open(config_path, "rb") as encrypted_file:
            self.assertNotIn(b"test-key", encrypted_file.read())

        self.client.post("/api/books", headers=headers, json={"title": "路由书"})
        book_routes = {
            stage: {"inherit": True, "primary_model_id": "", "fallback_model_ids": [], "overrides": {}}
            for stage in ("interactive", "drafting", "planning", "extraction", "review", "agent")
        }
        route_saved = self.client.put(
            "/api/books/%E8%B7%AF%E7%94%B1%E4%B9%A6/model-routes",
            headers=headers,
            json={"routes": book_routes},
        )
        self.assertEqual(200, route_saved.status_code, route_saved.text)
        loaded = self.client.get(
            "/api/books/%E8%B7%AF%E7%94%B1%E4%B9%A6/model-routes", headers=headers,
        )
        self.assertTrue(loaded.json()["routes"]["drafting"]["inherit"])

    def test_fake_generation_saves_chapter_and_replays_sse(self):
        self.configure_api()
        token = self.login()
        headers = self.auth_headers(token)
        self.client.post("/api/books", headers=headers, json={"title": "生成书"})
        response = self.client.post(
            "/api/books/%E7%94%9F%E6%88%90%E4%B9%A6/generate",
            headers=headers,
            json={"chapter_title": "开端", "plot": "主角出门", "target_words": 800},
        )
        self.assertEqual(response.status_code, 200, response.text)
        task_id = response.json()["task_id"]

        task = None
        for _ in range(50):
            task = self.client.get(f"/api/tasks/{task_id}", headers=headers).json()["task"]
            if task["status"] in {"completed", "failed"}:
                break
            time.sleep(0.05)
        self.assertIsNotNone(task)
        self.assertEqual(task["status"], "completed", task)

        chapter = self.client.get(
            "/api/books/%E7%94%9F%E6%88%90%E4%B9%A6/chapters/1",
            headers=headers,
        )
        self.assertEqual(chapter.status_code, 200, chapter.text)
        self.assertIn("第一段正文", chapter.json()["content"])

        with self.client.stream("GET", f"/api/tasks/{task_id}/events?token={token}") as stream:
            body = "".join(stream.iter_text())
        self.assertIn("event: completed", body)
        self.assertIn("生成完成", body)


if __name__ == "__main__":
    unittest.main()

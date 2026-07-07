from __future__ import annotations

import os
import re
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

import core.auth_manager as auth_module
from core.auth_manager import AuthManager
from web.server import create_app
from web.services import generation_params
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

    def configure_text_api(self) -> None:
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

    def test_books_crud_rename_and_delete(self):
        created = self.client.post("/api/books", headers=self.headers, json={"title": "Draft"})
        self.assertEqual(created.status_code, 200, created.text)
        renamed = self.client.patch("/api/books/Draft", headers=self.headers, json={"new_title": "Final"})
        self.assertEqual(renamed.status_code, 200, renamed.text)
        books = self.client.get("/api/books", headers=self.headers).json()["books"]
        self.assertEqual([item["title"] for item in books], ["Final"])
        meta = self.client.get("/api/books/Final/meta", headers=self.headers)
        self.assertEqual(meta.status_code, 200, meta.text)
        self.assertEqual(meta.json()["meta"]["title"], "Final")
        saved_meta = self.client.put(
            "/api/books/Final/meta",
            headers=self.headers,
            json={
                "protagonist_bio": "hero",
                "background_story": "world",
                "writing_demand": "demand",
                "author_plan": "plan",
                "genre": "fantasy",
                "style_tone": "cold",
                "xp_mode": True,
            },
        )
        self.assertEqual(saved_meta.status_code, 200, saved_meta.text)
        self.assertTrue(saved_meta.json()["meta"]["xp_mode"])
        self.assertEqual(saved_meta.json()["meta"]["protagonist_bio"], "hero")
        meta_reloaded = self.client.get("/api/books/Final/meta", headers=self.headers)
        self.assertEqual(meta_reloaded.status_code, 200, meta_reloaded.text)
        self.assertTrue(meta_reloaded.json()["meta"]["xp_mode"])
        missing_old = self.client.get("/api/books/Draft/meta", headers=self.headers)
        self.assertEqual(missing_old.status_code, 404)
        duplicate = self.client.post("/api/books", headers=self.headers, json={"title": "Other"})
        self.assertEqual(duplicate.status_code, 200, duplicate.text)
        conflict = self.client.patch("/api/books/Final", headers=self.headers, json={"new_title": "Other"})
        self.assertEqual(conflict.status_code, 400, conflict.text)
        deleted = self.client.delete("/api/books/Final", headers=self.headers)
        self.assertEqual(deleted.status_code, 200, deleted.text)
        books_after = self.client.get("/api/books", headers=self.headers).json()["books"]
        self.assertEqual([item["title"] for item in books_after], ["Other"])

    def test_sensitive_api_settings_markdown_role_and_diagnostics(self):
        denied = self.client.put(
            "/api/settings/api",
            headers=self.headers,
            json={"text": {"api_key": "key", "base_url": "http://example.invalid", "model": "m"}, "image": {"api_key": "image-key", "base_url": "http://image.invalid", "model": "img"}},
        )
        self.assertEqual(denied.status_code, 403)

        ticket = self.client.post(
            "/api/auth/confirm",
            headers=self.headers,
            json={"password": "pass123"},
        ).json()["sensitive_ticket"]
        partial_image = self.client.put(
            "/api/settings/api",
            headers={**self.headers, "X-Sensitive-Ticket": ticket},
            json={"image": {"base_url": "http://image.invalid"}},
        )
        self.assertEqual(partial_image.status_code, 400)
        saved = self.client.put(
            "/api/settings/api",
            headers={**self.headers, "X-Sensitive-Ticket": ticket},
            json={"text": {"api_key": "secret-key", "base_url": "http://example.invalid", "model": "fake-model"}, "image": {"api_key": "image-secret", "base_url": "http://image.invalid", "model": "fake-image"}},
        )
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertTrue(saved.json()["api"]["text"]["api_key_configured"])
        self.assertTrue(saved.json()["api"]["image"]["api_key_configured"])
        settings_api = self.client.get("/api/settings", headers=self.headers)
        self.assertEqual(settings_api.json()["api"]["image"]["base_url"], "http://image.invalid")
        self.assertEqual(settings_api.json()["api"]["image"]["model"], "fake-image")
        self.assertEqual(settings_api.json()["settings"]["last_model"], "fake-model")
        self.assertIn("fake-model", settings_api.json()["settings"]["custom_models"])
        switched_model = self.client.put(
            "/api/settings/model",
            headers=self.headers,
            json={"model": "web-current-model"},
        )
        self.assertEqual(switched_model.status_code, 200, switched_model.text)
        self.assertEqual(switched_model.json()["model"], "web-current-model")
        settings_after_model = self.client.get("/api/settings", headers=self.headers)
        self.assertEqual(settings_after_model.json()["api"]["text"]["model"], "web-current-model")
        self.assertEqual(settings_after_model.json()["settings"]["last_model"], "web-current-model")
        self.assertIn("web-current-model", settings_after_model.json()["settings"]["custom_models"])
        switched_params = generation_params(settings_after_model.json()["settings"], {"text": {"model": settings_after_model.json()["api"]["text"]["model"]}})
        self.assertEqual(switched_params["model"], "web-current-model")

        note = self.client.put(
            "/api/markdown/file",
            headers=self.headers,
            json={"path": "ideas/one.md", "content": "# Idea"},
        )
        self.assertEqual(note.status_code, 200, note.text)
        read_note = self.client.get("/api/markdown/file?path=ideas%2Fone.md", headers=self.headers)
        self.assertEqual(read_note.json()["content"], "# Idea")
        tree = self.client.get("/api/markdown/tree", headers=self.headers)
        self.assertIn("ideas/one.md", {item["path"] for item in tree.json()["items"]})

        folder = self.client.post("/api/markdown/folder", headers=self.headers, json={"path": "ideas/archive"})
        self.assertEqual(folder.status_code, 200, folder.text)
        renamed = self.client.post(
            "/api/markdown/rename",
            headers=self.headers,
            json={"path": "ideas/one.md", "new_path": "ideas/archive/two.md"},
        )
        self.assertEqual(renamed.status_code, 200, renamed.text)
        moved_note = self.client.get("/api/markdown/file?path=ideas%2Farchive%2Ftwo.md", headers=self.headers)
        self.assertEqual(moved_note.json()["content"], "# Idea")
        preview = self.client.get("/api/markdown/preview?path=ideas%2Farchive%2Ftwo.md", headers=self.headers)
        self.assertEqual(preview.status_code, 200, preview.text)
        self.assertIn("Idea", preview.json()["html"])
        exported_note = self.client.post(
            "/api/markdown/export",
            headers=self.headers,
            json={"path": "ideas/archive/two.md", "folder": False},
        )
        self.assertEqual(exported_note.status_code, 200, exported_note.text)
        self.assertIn("download", exported_note.json())
        exported_folder = self.client.post(
            "/api/markdown/export",
            headers=self.headers,
            json={"path": "ideas", "folder": True},
        )
        self.assertEqual(exported_folder.status_code, 200, exported_folder.text)
        self.assertIn("download", exported_folder.json())
        deleted = self.client.delete("/api/markdown/path?path=ideas%2Farchive%2Ftwo.md", headers=self.headers)
        self.assertEqual(deleted.status_code, 200, deleted.text)

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
        self.assertIn("task_summary", diag.json())
        diag_export = self.client.post("/api/diagnostics/export", headers=self.headers, json={})
        self.assertEqual(diag_export.status_code, 200, diag_export.text)
        self.assertIn("download", diag_export.json())


    def test_settings_data_package_and_password_flow(self):
        note = self.client.put(
            "/api/markdown/file",
            headers=self.headers,
            json={"path": "backup/idea.md", "content": "# Backup"},
        )
        self.assertEqual(note.status_code, 200, note.text)
        self.client.post("/api/books", headers=self.headers, json={"title": "BackupBook"})

        denied = self.client.post("/api/settings/data/export", headers=self.headers, json={})
        self.assertEqual(denied.status_code, 403)
        ticket = self.client.post(
            "/api/auth/confirm",
            headers=self.headers,
            json={"password": "pass123"},
        ).json()["sensitive_ticket"]
        exported = self.client.post(
            "/api/settings/data/export",
            headers={**self.headers, "X-Sensitive-Ticket": ticket},
            json={},
        )
        self.assertEqual(exported.status_code, 200, exported.text)
        download_id = exported.json()["download"]["download_id"]
        package_path = self.runtime.resolve_download("alice", download_id)["path"]
        with open(package_path, "rb") as handle:
            package_bytes = handle.read()
        self.assertGreater(len(package_bytes), 100)

        cleared = self.client.post(
            "/api/settings/data/clear",
            headers={**self.headers, "X-Sensitive-Ticket": ticket},
            json={},
        )
        self.assertEqual(cleared.status_code, 200, cleared.text)
        missing_note = self.client.get("/api/markdown/file?path=backup%2Fidea.md", headers=self.headers)
        self.assertEqual(missing_note.status_code, 404)

        imported = self.client.post(
            "/api/settings/data/import",
            headers={**self.headers, "X-Sensitive-Ticket": ticket},
            files={"file": ("alice_data.zip", package_bytes, "application/zip")},
        )
        self.assertEqual(imported.status_code, 200, imported.text)
        self.assertGreater(imported.json()["imported"], 0)
        restored_note = self.client.get("/api/markdown/file?path=backup%2Fidea.md", headers=self.headers)
        self.assertEqual(restored_note.status_code, 200, restored_note.text)
        self.assertEqual(restored_note.json()["content"], "# Backup")

        old_headers = dict(self.headers)
        changed = self.client.post(
            "/api/settings/password",
            headers=self.headers,
            json={"old_password": "pass123", "new_password": "pass456"},
        )
        self.assertEqual(changed.status_code, 200, changed.text)
        new_token = changed.json()["token"]
        self.assertTrue(new_token)
        self.assertNotEqual(new_token, old_headers["Authorization"].split(" ", 1)[1])
        denied_old_token = self.client.get("/api/books", headers=old_headers)
        self.assertEqual(denied_old_token.status_code, 401)
        self.headers = {"Authorization": f"Bearer {new_token}"}
        ok_new_token = self.client.get("/api/books", headers=self.headers)
        self.assertEqual(ok_new_token.status_code, 200, ok_new_token.text)

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
        completed_retry = self.client.post(f"/api/tasks/{task['task_id']}/retry", headers=self.headers, json={})
        self.assertEqual(completed_retry.status_code, 400, completed_retry.text)

        def failing_target(_handle):
            raise RuntimeError("boom")

        failed_task_id = self.runtime.start_task("alice", "Failing task", failing_target, retryable=True)
        failed_task = self.wait_task(failed_task_id)
        self.assertEqual(failed_task["status"], "failed", failed_task)
        retry = self.client.post(f"/api/tasks/{failed_task_id}/retry", headers=self.headers, json={})
        self.assertEqual(retry.status_code, 200, retry.text)
        retry_task = self.wait_task(retry.json()["task_id"])
        self.assertEqual(retry_task["status"], "failed", retry_task)

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



    def test_chapter_management_desktop_actions(self):
        self.configure_text_api()
        self.client.post("/api/books", headers=self.headers, json={"title": "ChapterDesk"})
        imported = self.client.post(
            "/api/continuation/import",
            headers=self.headers,
            json={"title": "ChapterDesk", "sections": [{"title": "One", "content": "old content"}]},
        )
        self.assertEqual(imported.status_code, 200, imported.text)
        self.assertEqual(self.wait_task(imported.json()["task_id"])["status"], "completed")

        tree = self.client.get("/api/books/ChapterDesk/chapter-tree", headers=self.headers)
        self.assertEqual(tree.status_code, 200, tree.text)
        tree_data = tree.json()
        self.assertTrue(tree_data["trees"])
        primary_tree = tree_data["active_tree_id"]
        nodes = [node for node in tree_data["nodes"] if not node.get("virtual")]
        self.assertEqual(len(nodes), 1)
        node_id = nodes[0]["id"]

        activated_tree = self.client.post(f"/api/books/ChapterDesk/chapter-trees/{primary_tree}/activate", headers=self.headers, json={})
        self.assertEqual(activated_tree.status_code, 200, activated_tree.text)
        activated_node = self.client.post(f"/api/books/ChapterDesk/nodes/{node_id}/activate", headers=self.headers, json={})
        self.assertEqual(activated_node.status_code, 200, activated_node.text)
        path = self.client.get(f"/api/books/ChapterDesk/nodes/{node_id}/path", headers=self.headers)
        self.assertEqual(path.status_code, 200, path.text)
        self.assertEqual(path.json()["nodes"][-1]["id"], node_id)
        record = self.client.get(f"/api/books/ChapterDesk/nodes/{node_id}/record", headers=self.headers)
        self.assertEqual(record.status_code, 200, record.text)
        self.assertIn("record", record.json())

        saved_summary = self.client.put(
            f"/api/books/ChapterDesk/nodes/{node_id}/summary",
            headers=self.headers,
            json={"summary": "manual summary"},
        )
        self.assertEqual(saved_summary.status_code, 200, saved_summary.text)
        self.assertEqual(saved_summary.json()["node"]["summary"], "manual summary")

        edited = self.client.put(
            f"/api/books/ChapterDesk/nodes/{node_id}/content",
            headers=self.headers,
            json={"title": "One edited", "content": "new edited content", "activate": True},
        )
        self.assertEqual(edited.status_code, 200, edited.text)
        new_node_id = edited.json()["node_id"]
        self.assertEqual(edited.json()["version"], 2)
        active_chapter = self.client.get("/api/books/ChapterDesk/chapters/1", headers=self.headers)
        self.assertEqual(active_chapter.status_code, 200, active_chapter.text)
        self.assertEqual(active_chapter.json()["content"], "new edited content")
        versions = self.client.get("/api/books/ChapterDesk/chapters/1/versions", headers=self.headers)
        self.assertEqual(versions.json()["active"], 2)

        polished = self.client.post(
            f"/api/books/ChapterDesk/nodes/{new_node_id}/variant",
            headers=self.headers,
            json={"mode": "polish", "requirement": "更细腻", "target_words": 0},
        )
        self.assertEqual(polished.status_code, 200, polished.text)
        polish_task = self.wait_task(polished.json()["task_id"])
        self.assertEqual(polish_task["status"], "completed", polish_task)
        self.assertEqual(polish_task["metadata"]["kind"], "chapter_polish")
        versions_after_polish = self.client.get("/api/books/ChapterDesk/chapters/1/versions", headers=self.headers)
        self.assertEqual(versions_after_polish.status_code, 200, versions_after_polish.text)
        self.assertEqual(versions_after_polish.json()["active"], 3)
        active_after_polish = self.client.get("/api/books/ChapterDesk/chapters/1", headers=self.headers)
        self.assertEqual(active_after_polish.status_code, 200, active_after_polish.text)
        self.assertEqual(active_after_polish.json()["content"], "正文")
        tree_after_polish = self.client.get("/api/books/ChapterDesk/chapter-tree", headers=self.headers)
        polished_node = next(node for node in tree_after_polish.json()["nodes"] if node.get("version") == 3)
        polished_record = self.client.get(f"/api/books/ChapterDesk/nodes/{polished_node['id']}/record", headers=self.headers)
        self.assertEqual(polished_record.status_code, 200, polished_record.text)
        self.assertEqual(polished_record.json()["record"].get("generation_mode"), "classic")
        self.assertEqual(polished_record.json()["record"].get("operation"), "chapter_polish")
        rewritten = self.client.post(
            f"/api/books/ChapterDesk/nodes/{polished_node['id']}/variant",
            headers=self.headers,
            json={"mode": "rewrite", "requirement": "换一种叙事节奏", "target_words": 0},
        )
        self.assertEqual(rewritten.status_code, 200, rewritten.text)
        rewrite_task = self.wait_task(rewritten.json()["task_id"])
        self.assertEqual(rewrite_task["status"], "completed", rewrite_task)
        self.assertEqual(rewrite_task["metadata"]["kind"], "chapter_rewrite")
        versions_after_rewrite = self.client.get("/api/books/ChapterDesk/chapters/1/versions", headers=self.headers)
        self.assertEqual(versions_after_rewrite.json()["active"], 4)
        tree_after_rewrite = self.client.get("/api/books/ChapterDesk/chapter-tree", headers=self.headers)
        rewritten_node = next(node for node in tree_after_rewrite.json()["nodes"] if node.get("version") == 4)
        rewritten_record = self.client.get(f"/api/books/ChapterDesk/nodes/{rewritten_node['id']}/record", headers=self.headers)
        self.assertEqual(rewritten_record.status_code, 200, rewritten_record.text)
        self.assertEqual(rewritten_record.json()["record"].get("operation"), "chapter_rewrite")
        exported = self.client.post(
            f"/api/books/ChapterDesk/nodes/{new_node_id}/export",
            headers=self.headers,
            json={"fmt": "txt"},
        )
        self.assertEqual(exported.status_code, 200, exported.text)
        export_task = self.wait_task(exported.json()["task_id"])
        self.assertEqual(export_task["status"], "completed", export_task)
        self.assertEqual(export_task["metadata"]["kind"], "node_export")

        deleted_v1 = self.client.delete("/api/books/ChapterDesk/chapters/1/versions/1", headers=self.headers)
        self.assertEqual(deleted_v1.status_code, 200, deleted_v1.text)
        remaining = self.client.get("/api/books/ChapterDesk/chapters/1/versions", headers=self.headers)
        self.assertEqual([item["v"] for item in remaining.json()["versions"]], [2, 3, 4])

    def test_world_bible_desktop_management_actions(self):
        self.client.post("/api/books", headers=self.headers, json={"title": "WorldDesk"})
        world_data = {
            "characters": [
                {"id": "char-a", "name": "A", "aliases": [], "traits": "leader", "importance": "major", "source_chapter": 1, "last_updated_chapter": 1},
                {"id": "char-b", "name": "B", "aliases": ["Bee"], "traits": "scout", "importance": "minor", "source_chapter": 1, "last_updated_chapter": 1},
                {"id": "char-c", "name": "C", "traits": "mage", "source_chapter": 1, "last_updated_chapter": 1},
                {"id": "char-d", "name": "D", "traits": "mage alias", "source_chapter": 1, "last_updated_chapter": 1},
            ],
            "locations": [{"id": "loc-a", "name": "City", "description": "base", "significance": "capital", "key_details": ["old wall"], "source_chapter": 1}, {"id": "loc-b", "name": "City Gate", "description": "gate", "significance": "entrance", "key_details": ["iron door"], "source_chapter": 2}],
            "active_plot_threads": [{"id": "plot-a", "name": "Main Thread", "description": "finish quest", "status": "active", "importance": "minor", "source_chapter": 1}],
            "key_worldbuilding_passages": [{"topic": "Magic", "passage": "mana law", "chapter": 1}],
            "global_foreshadowing": [{"id": "fs-a", "hint": "red moon", "relates_to": "quest", "status": "open", "introduced_chapter": 1, "last_touched_chapter": 1}],
            "facts": [{"id": "fact-a", "subject_id": "char-a", "predicate": "status", "value": "alive", "source_refs": [{"chapter": 1}]}],
            "duplicate_candidates": [
                {"id": "dup-ab", "entity_type": "character", "entity_ids": ["char-a", "char-b"], "names": ["A", "B"], "confidence": 0.8, "status": "pending"},
                {"id": "dup-cd", "entity_type": "character", "entity_ids": ["char-c", "char-d"], "names": ["C", "D"], "confidence": 0.9, "status": "pending"},
            ],
        }
        saved = self.client.put("/api/books/WorldDesk/world", headers=self.headers, json={"world": world_data})
        self.assertEqual(saved.status_code, 200, saved.text)

        self.client.post("/api/books", headers=self.headers, json={"title": "ScopeDesk"})
        from core.agent.changes import ChangeSetService
        from core.agent.repository import AgentRepository
        token = self.headers["Authorization"].split(" ", 1)[1]
        ctx = self.runtime.context_from_token(token)
        manifest = ctx.novel_manager.ensure_workspace("ScopeDesk")
        repo = AgentRepository(ctx.novel_manager.get_workspace("ScopeDesk"))
        change = ChangeSetService(ctx.novel_manager, "ScopeDesk", repo).propose_world_patch(
            "run-scope",
            manifest.book_id,
            [{
                "operation": "entity.create",
                "entity_type": "character",
                "entity_id": "char-web-scope",
                "payload": {"id": "char-web-scope", "name": "Web Scope", "traits": "approved"},
                "scope": "uncertain",
                "anchor_node_id": "",
                "reason": "web approval test",
            }],
        )
        confirmed_scope = self.client.post(
            "/api/books/ScopeDesk/agent/world/confirm-scopes",
            headers=self.headers,
            json={
                "change_set_id": change.change_set_id,
                "operations": [{
                    "operation": "entity.create",
                    "entity_type": "character",
                    "entity_id": "char-web-scope",
                    "payload": {"id": "char-web-scope", "name": "Web Scope", "traits": "approved"},
                    "scope": "global",
                    "anchor_node_id": "ignored-anchor",
                    "reason": "web approval test",
                }],
            },
        )
        self.assertEqual(confirmed_scope.status_code, 200, confirmed_scope.text)
        approved = self.client.post(
            "/api/books/ScopeDesk/agent/changes/approve",
            headers=self.headers,
            json={"change_set_id": change.change_set_id},
        )
        self.assertEqual(approved.status_code, 200, approved.text)
        world_after_approval = self.client.get("/api/books/ScopeDesk/world", headers=self.headers).json()["world"]
        self.assertTrue(any(item.get("id") == "char-web-scope" for item in world_after_approval.get("characters", [])))
        policies = self.client.get("/api/books/WorldDesk/context-policies", headers=self.headers)
        self.assertEqual(policies.status_code, 200, policies.text)
        entity_ids = {item["entity_id"] for item in policies.json()["entities"]}
        self.assertIn("char-a", entity_ids)
        saved_policies = self.client.put(
            "/api/books/WorldDesk/context-policies",
            headers=self.headers,
            json={
                "policies": {
                    "char-a": {"enabled": True, "load_mode": "resident", "priority": 120, "brief_description": "leader", "keywords": "hero、quest"},
                    "loc-a": {"enabled": False, "load_mode": "manual", "priority": -10, "keywords": ["city"]},
                    "plot-a": {"enabled": True, "load_mode": "bad", "priority": "bad"},
                }
            },
        )
        self.assertEqual(saved_policies.status_code, 200, saved_policies.text)
        stored = saved_policies.json()["policies"]
        self.assertEqual(stored["char-a"]["load_mode"], "resident")
        self.assertEqual(stored["char-a"]["priority"], 100)
        self.assertEqual(stored["char-a"]["keywords"], ["hero", "quest"])
        self.assertFalse(stored["loc-a"]["enabled"])
        self.assertEqual(stored["loc-a"]["priority"], 0)
        self.assertEqual(stored["plot-a"]["load_mode"], "auto")
        policies_after = self.client.get("/api/books/WorldDesk/context-policies", headers=self.headers)
        char_policy = next(item for item in policies_after.json()["entities"] if item["entity_id"] == "char-a")["policy"]
        self.assertEqual(char_policy["brief_description"], "leader")

        source = self.client.get("/api/books/WorldDesk/world/source?chapter=1", headers=self.headers)
        self.assertEqual(source.status_code, 200, source.text)
        self.assertEqual(len(source.json()["groups"]["characters"]), 4)
        facts = self.client.get("/api/books/WorldDesk/world/facts?entity_id=char-a", headers=self.headers)
        self.assertEqual(facts.status_code, 200, facts.text)
        self.assertEqual(facts.json()["facts"][0]["subject_id"], "char-a")
        preview = self.client.post(
            "/api/books/WorldDesk/world/retrieval-preview",
            headers=self.headers,
            json={"query": "A red moon quest", "token_budget": 800},
        )
        self.assertEqual(preview.status_code, 200, preview.text)
        self.assertIn("diagnostics", preview.json())

        hidden = self.client.post(
            "/api/books/WorldDesk/world/entity/state",
            headers=self.headers,
            json={"category": "characters", "index": 1, "field": "hidden", "value": True},
        )
        self.assertEqual(hidden.status_code, 200, hidden.text)
        self.assertTrue(hidden.json()["world"]["characters"][1]["hidden"])
        resolved = self.client.post("/api/books/WorldDesk/world/resolve", headers=self.headers, json={"query": "red moon"})
        self.assertEqual(resolved.status_code, 200, resolved.text)
        self.assertEqual(resolved.json()["world"]["global_foreshadowing"][0]["status"], "resolved")
        locked = self.client.post(
            "/api/books/WorldDesk/world/lock-setting",
            headers=self.headers,
            json={"topic": "Magic", "passage": "mana law updated"},
        )
        self.assertEqual(locked.status_code, 200, locked.text)
        self.assertTrue(locked.json()["setting"]["locked"])
        added = self.client.post(
            "/api/books/WorldDesk/world/foreshadowing",
            headers=self.headers,
            json={"hint": "silver key", "relates_to": "Magic", "next_step": "reveal later"},
        )
        self.assertEqual(added.status_code, 200, added.text)
        self.assertEqual(added.json()["foreshadowing"]["hint"], "silver key")
        low = self.client.post("/api/books/WorldDesk/world/hide-low-priority", headers=self.headers, json={})
        self.assertEqual(low.status_code, 200, low.text)

        duplicates = self.client.get("/api/books/WorldDesk/world/duplicates", headers=self.headers)
        self.assertEqual(duplicates.status_code, 200, duplicates.text)
        self.assertEqual(len(duplicates.json()["pending"]), 2)
        rejected = self.client.post("/api/books/WorldDesk/world/duplicates/reject", headers=self.headers, json={"candidate_id": "dup-ab"})
        self.assertEqual(rejected.status_code, 200, rejected.text)
        confirmed = self.client.post("/api/books/WorldDesk/world/duplicates/confirm", headers=self.headers, json={"candidate_id": "dup-cd"})
        self.assertEqual(confirmed.status_code, 200, confirmed.text)
        self.assertEqual(len(confirmed.json()["world"]["characters"]), 3)
        undone = self.client.post("/api/books/WorldDesk/world/merge/undo", headers=self.headers, json={"merge_id": ""})
        self.assertEqual(undone.status_code, 200, undone.text)
        self.assertEqual(len(undone.json()["world"]["characters"]), 4)

        merged = self.client.post(
            "/api/books/WorldDesk/world/characters/merge",
            headers=self.headers,
            json={"target_name": "A", "merge_names": ["B"]},
        )
        self.assertEqual(merged.status_code, 200, merged.text)
        self.assertEqual(len(merged.json()["world"]["characters"]), 3)
        self.assertIn("B", merged.json()["world"]["characters"][0]["aliases"])

        merged_location = self.client.post(
            "/api/books/WorldDesk/world/locations/merge",
            headers=self.headers,
            json={"target_name": "City", "merge_names": ["City Gate"]},
        )
        self.assertEqual(merged_location.status_code, 200, merged_location.text)
        self.assertEqual(len(merged_location.json()["world"]["locations"]), 1)
        location = merged_location.json()["world"]["locations"][0]
        self.assertIn("gate", location["description"])
        self.assertIn("iron door", location["key_details"])

    def test_continuation_static_desktop_shortcuts_present(self):
        root = os.path.dirname(os.path.dirname(__file__))
        with open(os.path.join(root, "web", "static", "index.html"), "r", encoding="utf-8") as handle:
            html = handle.read()
        with open(os.path.join(root, "web", "static", "app.js"), "r", encoding="utf-8") as handle:
            script = handle.read()

        for token in [
            "contChapterInfo",
            "contQuickAnalyzeBtn",
            "contQuickGenerateBtn",
            "contQuickDirectionsBtn",
            "contChapterMode",
            "continuationRunDetail",
            "applyRunSettingsBtn",
            "applyRunDirectionsBtn",
            "snapshotDetail",
            "snapshotStatusBtn",
            "snapshotMessage",
            "chapterGraph",
            "chapterGraphZoomOutBtn",
            "chapterGraphResetBtn",
            "chapterGraphZoomInBtn",
            "chapterGraphFitBtn",
            "createSnapshotBtn",
            "restoreSnapshotBtn",
            "deleteSnapshotBtn",
            "agentWebMethod",
            "agentWebTimeoutSeconds",
            "agentWebAuthHeader",
            "agentWebAuthPrefix",
            "agentWebQueryField",
            "agentWebResultsPath",
            "agentWebTitleField",
            "agentWebUrlField",
            "agentWebSnippetField",
            "saveAgentWebBtn",
            "testAgentWebBtn",
            "testEmbeddingBtn",
            "imageApiKey",
            "imageBaseUrl",
            "imageModel",
            "setCurrentPresetBtn",
            "editMemoryChangeBtn",
            "chatExportFormat",
            "currentModel",
            "saveCurrentModelBtn",
            "modelOptions",
            "tokenDownloadList",
            "taskDownloadList",
        ]:
            self.assertIn(token, html)
        for token in [
            "updateContinuationChapterInfo",
            "quickAnalyzeContinuation",
            "quickGenerateContinuation",
            "quickSuggestContinuation",
            "contQuickAnalyzeBtn",
            "contChapterMode",
            "showContinuationRun",
            "applyContinuationRunSettings",
            "applyContinuationRunDirections",
            "showSnapshotStatus",
            "renderChapterGraph",
            "selectGraphNode",
            "changeChapterGraphZoom",
            "fitChapterGraph",
            "createSnapshot",
            "restoreSelectedSnapshot",
            "deleteSelectedSnapshot",
            "testAgentWebSearch",
            "agent_web_method",
            "agent_web_timeout_seconds",
            "agent_web_auth_header",
            "agent_web_results_path",
            "testAgentWebBtn",
            "testEmbedding",
            "settings/embedding/test",
            "imageBaseUrl",
            "imageModel",
            "image:{api_key",
            "setCurrentPreset",
            "settings/presets/current",
            "editMemoryChangeBtn",
            "chatExportFormat",
            "currentModel",
            "saveCurrentModelBtn",
            "modelOptions",
            "tokenDownloadList",
            "taskDownloadList",
            "saveCurrentModel",
            "settings/model",
            "formatEstimatedCost",
            "estimated_cost",
        ]:
            self.assertIn(token, script)
    def test_web_interactive_controls_are_bound(self):
        root = os.path.dirname(os.path.dirname(__file__))
        with open(os.path.join(root, "web", "static", "index.html"), "r", encoding="utf-8") as handle:
            html = handle.read()
        with open(os.path.join(root, "web", "static", "app.js"), "r", encoding="utf-8") as handle:
            script = handle.read()

        interactive_ids = [
            match.group(2)
            for match in re.finditer(r'<(button|form|select)[^>]*\sid="([^"]+)"', html)
        ]
        missing = [
            control_id for control_id in interactive_ids
            if f'"{control_id}"' not in script and f'$("{control_id}")' not in script and f'#{control_id}' not in script
        ]
        self.assertEqual(missing, [])
    def test_continuation_full_web_flow_with_fake_model(self):
        self.configure_text_api()
        upload = self.client.post(
            "/api/continuation/uploads",
            headers=self.headers,
            files=[
                ("files", ("02.md", "# 第二章\n后续正文".encode("utf-8"), "text/markdown")),
                ("files", ("01.txt", "第一章正文".encode("utf-8"), "text/plain")),
            ],
        )
        self.assertEqual(upload.status_code, 200, upload.text)
        files = upload.json()["files"]
        self.assertEqual([item["filename"] for item in files], ["01.txt", "02.md"])

        segmented = self.client.post(
            "/api/continuation/segment-agent",
            headers=self.headers,
            json={"title": "ContBook", "text": "第一段\n\n第二段", "use_agent": True},
        )
        self.assertEqual(segmented.status_code, 200, segmented.text)
        self.assertGreaterEqual(len(segmented.json()["sections"]), 1)

        sections = [sec for item in files for sec in item["sections"]]
        analyzed = self.client.post(
            "/api/continuation/analyze",
            headers=self.headers,
            json={"title": "ContBook", "sections": sections, "source_text": "第一章正文\n后续正文"},
        )
        self.assertEqual(analyzed.status_code, 200, analyzed.text)
        analyze_task = self.wait_task(analyzed.json()["task_id"])
        self.assertEqual(analyze_task["status"], "completed", analyze_task)

        suggested = self.client.post(
            "/api/continuation/suggest",
            headers=self.headers,
            json={"title": "ContBook", "plot": "继续推进主线"},
        )
        self.assertEqual(suggested.status_code, 200, suggested.text)
        suggest_task = self.wait_task(suggested.json()["task_id"])
        self.assertEqual(suggest_task["status"], "completed", suggest_task)

        generated = self.client.post(
            "/api/continuation/generate",
            headers=self.headers,
            json={"title": "ContBook", "source_text": "第一章正文", "chapter_title": "新的起点", "requirement": "保持风格", "plot": "角色出发", "target_words": 100},
        )
        self.assertEqual(generated.status_code, 200, generated.text)
        generate_task = self.wait_task(generated.json()["task_id"])
        self.assertEqual(generate_task["status"], "completed", generate_task)

        chapters = self.client.get("/api/books/ContBook/chapters", headers=self.headers).json()["chapters"]
        self.assertGreaterEqual(len(chapters), 2)
        generated_chapter_num = max(item["chapter_num"] for item in chapters)
        generated_chapter = self.client.get(f"/api/books/ContBook/chapters/{generated_chapter_num}", headers=self.headers)
        self.assertEqual(generated_chapter.status_code, 200, generated_chapter.text)
        generated_tree = self.client.get("/api/books/ContBook/chapter-tree", headers=self.headers).json()
        active_ids = set(generated_tree.get("active_path") or [])
        generated_node = next(node for node in generated_tree["nodes"] if node.get("chapter_num") == generated_chapter_num and node.get("id") in active_ids)
        generated_record = self.client.get(f"/api/books/ContBook/nodes/{generated_node['id']}/record", headers=self.headers)
        self.assertEqual(generated_record.status_code, 200, generated_record.text)
        self.assertIn("supervision_report", generated_record.json()["record"])
        chapters_before_draft = self.client.get("/api/books/ContBook/chapters", headers=self.headers).json()["chapters"]
        drafted = self.client.post(
            "/api/continuation/generate",
            headers=self.headers,
            json={"title": "ContBook", "source_text": "第一章正文", "chapter_title": "草稿续写", "requirement": "只生成草稿", "plot": "暂不入库", "target_words": 100, "chapter_mode": False},
        )
        self.assertEqual(drafted.status_code, 200, drafted.text)
        draft_task = self.wait_task(drafted.json()["task_id"])
        self.assertEqual(draft_task["status"], "completed", draft_task)
        self.assertIn("'draft_only': True", draft_task["result_preview"])
        chapters_after_draft = self.client.get("/api/books/ContBook/chapters", headers=self.headers).json()["chapters"]
        self.assertEqual(len(chapters_after_draft), len(chapters_before_draft))
        runs = self.client.get("/api/continuation/runs?title=ContBook", headers=self.headers)
        self.assertEqual(runs.status_code, 200, runs.text)
        run_items = runs.json()["runs"]
        tasks = {item["task"] for item in run_items}
        self.assertIn("continuation_analyze", tasks)
        self.assertIn("continuation_direction", tasks)
        self.assertIn("continuation_generate", tasks)
        self.assertIn("continuation_draft", tasks)
        analyze_run = next(item for item in run_items if item["task"] == "continuation_analyze")
        detail = self.client.get(f"/api/continuation/runs/{analyze_run['run_id']}?title=ContBook", headers=self.headers)
        self.assertEqual(detail.status_code, 200, detail.text)
        detail_data = detail.json()["run"]
        self.assertEqual(detail_data["schema_version"], 1)
        self.assertEqual(detail_data["result"]["title"], "ContBook")
        self.assertIn("settings", detail_data["result"])
        direction_run = next(item for item in run_items if item["task"] == "continuation_direction")
        direction_detail = self.client.get(f"/api/continuation/runs/{direction_run['run_id']}?title=ContBook", headers=self.headers)
        self.assertEqual(direction_detail.status_code, 200, direction_detail.text)
        self.assertGreaterEqual(len(direction_detail.json()["run"]["result"]["directions"]), 1)

    def test_roleplay_chat_full_web_flow_with_fake_model(self):
        self.configure_text_api()
        created = self.client.post(
            "/api/roleplay/characters",
            headers=self.headers,
            json={"profile": {"name": "Hero", "identity": "protagonist", "identity_detail": "Calm strategist"}},
        )
        self.assertEqual(created.status_code, 200, created.text)
        profile = created.json()["profile"]
        character_id = profile["character_id"]

        updated = self.client.put(
            f"/api/roleplay/characters/{character_id}",
            headers=self.headers,
            json={"profile": {**profile, "name": "Heroine", "identity_detail": "Leads the scene"}},
        )
        self.assertEqual(updated.status_code, 200, updated.text)

        chat = self.client.post(
            "/api/roleplay/chat",
            headers=self.headers,
            json={
                "title": "Role Scene",
                "message": "Start the scene.",
                "character_ids": [character_id],
                "chat_type": "private",
                "sender_name": "Writer",
                "sender_profile": "Writes concise prompts",
                "required_responder_ids": [character_id],
                "reply_mode": "character",
                "narrator_enabled": True,
            },
        )
        self.assertEqual(chat.status_code, 200, chat.text)
        task = self.wait_task(chat.json()["task_id"])
        self.assertEqual(task["status"], "completed", task)
        conversations = self.client.get("/api/roleplay/conversations", headers=self.headers)
        self.assertEqual(conversations.status_code, 200, conversations.text)
        items = conversations.json()["conversations"]
        self.assertEqual(len(items), 1)
        conversation_id = items[0]["conversation_id"]

        conversation = self.client.get(f"/api/roleplay/conversations/{conversation_id}", headers=self.headers)
        self.assertEqual(conversation.status_code, 200, conversation.text)
        record = conversation.json()["conversation"]
        self.assertIn(character_id, record["participant_character_ids"])
        self.assertGreaterEqual(len(record["structured_messages"]), 2)

        sender = self.client.post(
            "/api/roleplay/senders",
            headers=self.headers,
            json={"profile": {"name": "Writer", "personality": "Direct", "notes": "Uses short prompts"}},
        )
        self.assertEqual(sender.status_code, 200, sender.text)
        sender_id = sender.json()["profile"]["sender_profile_id"]
        scene = self.client.post(
            "/api/roleplay/scenes",
            headers=self.headers,
            json={"preset": {"name": "Harbor", "scene": {"time": "night", "location": "Harbor", "present_character_ids": [character_id], "tags": ["tense"]}}},
        )
        self.assertEqual(scene.status_code, 200, scene.text)
        scene_id = scene.json()["preset"]["scene_preset_id"]
        controls = self.client.get(f"/api/roleplay/conversations/{conversation_id}/controls", headers=self.headers)
        self.assertEqual(controls.status_code, 200, controls.text)
        self.assertIn("sender_profiles", controls.json())
        saved_controls = self.client.put(
            f"/api/roleplay/conversations/{conversation_id}/controls",
            headers=self.headers,
            json={"state": {"sender_profile_id": sender_id, "scene_state": {"location": "Harbor", "present_character_ids": [character_id]}, "turn_policy": {"allowed_speaker_ids": [character_id], "max_speakers": 1}, "narrator_enabled": True}},
        )
        self.assertEqual(saved_controls.status_code, 200, saved_controls.text)
        self.assertEqual(saved_controls.json()["state"]["scene_state"]["location"], "Harbor")
        stored_controls = self.client.get(f"/api/roleplay/conversations/{conversation_id}", headers=self.headers).json()["conversation"]
        self.assertEqual(stored_controls["sender_profile_id"], sender_id)
        self.assertEqual(stored_controls["turn_policy"]["max_speakers"], 1)

        book_response = self.client.get("/api/roleplay/character-book", headers=self.headers)
        self.assertEqual(book_response.status_code, 200, book_response.text)
        self.assertTrue(any(item["character_id"] == character_id for item in book_response.json()["book"]["profiles"]))
        stored_controls["memory_change_sets"] = [
            {
                "change_set_id": "mem-web",
                "branch_id": "main",
                "source_message_ids": [record["structured_messages"][1]["message_id"]],
                "status": "pending",
                "changes": [
                    {
                        "change_id": "chg-web",
                        "character_id": character_id,
                        "field_name": "notes",
                        "old_value": "",
                        "new_value": "remembers harbor",
                        "risk": "high",
                        "reason": "web memory review",
                    }
                ],
            }
        ]
        saved_memory_seed = self.client.post("/api/roleplay/conversations", headers=self.headers, json={"record": stored_controls})
        self.assertEqual(saved_memory_seed.status_code, 200, saved_memory_seed.text)
        memory_state = self.client.get(f"/api/roleplay/conversations/{conversation_id}/memory", headers=self.headers)
        self.assertEqual(memory_state.status_code, 200, memory_state.text)
        self.assertEqual(memory_state.json()["memory_change_sets"][0]["change_set_id"], "mem-web")
        edited_memory = self.client.put(
            f"/api/roleplay/conversations/{conversation_id}/memory/mem-web",
            headers=self.headers,
            json={"changes": [{"change_id": "chg-web", "new_value": "remembers edited harbor", "reason": "web modified memory"}]},
        )
        self.assertEqual(edited_memory.status_code, 200, edited_memory.text)
        self.assertEqual(edited_memory.json()["memory_change_sets"][0]["changes"][0]["new_value"], "remembers edited harbor")
        applied_memory = self.client.post(f"/api/roleplay/conversations/{conversation_id}/memory/mem-web/apply", headers=self.headers, json={})
        self.assertEqual(applied_memory.status_code, 200, applied_memory.text)
        self.assertEqual(applied_memory.json()["memory_change_sets"][0]["status"], "applied")
        self.assertEqual(next(item for item in applied_memory.json()["book"]["profiles"] if item["character_id"] == character_id)["notes"], "remembers edited harbor")
        reverted_memory = self.client.post(f"/api/roleplay/conversations/{conversation_id}/memory/mem-web/revert", headers=self.headers, json={})
        self.assertEqual(reverted_memory.status_code, 200, reverted_memory.text)
        self.assertEqual(reverted_memory.json()["memory_change_sets"][0]["status"], "reverted")
        rejected_memory = self.client.post(f"/api/roleplay/conversations/{conversation_id}/memory/mem-web/reject", headers=self.headers, json={})
        self.assertEqual(rejected_memory.status_code, 200, rejected_memory.text)
        self.assertEqual(rejected_memory.json()["memory_change_sets"][0]["status"], "rejected")
        manual_book = rejected_memory.json()["book"]
        manual_book["profiles"][0]["notes"] = "manual edit"
        saved_book = self.client.put("/api/roleplay/character-book", headers=self.headers, json={"book": manual_book})
        self.assertEqual(saved_book.status_code, 200, saved_book.text)
        self.assertEqual(saved_book.json()["book"]["profiles"][0]["notes"], "manual edit")

        exported = self.client.post(
            f"/api/roleplay/conversations/{conversation_id}/export",
            headers=self.headers,
            json={"fmt": "md"},
        )
        self.assertEqual(exported.status_code, 200, exported.text)
        self.assertIn("download", exported.json())
        exported_download = self.runtime.resolve_download("alice", exported.json()["download"]["download_id"])
        self.assertTrue(exported_download["filename"].endswith(".md"))

        branches = self.client.get(f"/api/roleplay/conversations/{conversation_id}/branches", headers=self.headers)
        self.assertEqual(branches.status_code, 200, branches.text)
        self.assertEqual(branches.json()["active_branch_id"], "main")
        self.assertEqual(len(branches.json()["branches"]), 1)
        messages = self.client.get(f"/api/roleplay/conversations/{conversation_id}", headers=self.headers).json()["conversation"]["structured_messages"]
        message_info = self.client.get(f"/api/roleplay/conversations/{conversation_id}/messages/{messages[1]['message_id']}", headers=self.headers)
        self.assertEqual(message_info.status_code, 200, message_info.text)
        self.assertEqual(message_info.json()["memory_change_sets"][0]["change_set_id"], "mem-web")
        edited_message = self.client.put(
            f"/api/roleplay/conversations/{conversation_id}/messages/{messages[0]['message_id']}",
            headers=self.headers,
            json={"content": "Edited scene prompt"},
        )
        self.assertEqual(edited_message.status_code, 200, edited_message.text)
        self.assertEqual(edited_message.json()["conversation"]["structured_messages"][0]["content"], "Edited scene prompt")
        messages = edited_message.json()["conversation"]["structured_messages"]
        message_fork = self.client.post(
            f"/api/roleplay/conversations/{conversation_id}/messages/{messages[0]['message_id']}/fork",
            headers=self.headers,
            json={"title": "Message Fork"},
        )
        self.assertEqual(message_fork.status_code, 200, message_fork.text)
        message_branch_id = message_fork.json()["branch"]["branch_id"]
        switched_from_message = self.client.post(
            f"/api/roleplay/conversations/{conversation_id}/branches/main/activate",
            headers=self.headers,
            json={},
        )
        self.assertEqual(switched_from_message.status_code, 200, switched_from_message.text)
        deleted_message_branch = self.client.delete(f"/api/roleplay/conversations/{conversation_id}/branches/{message_branch_id}", headers=self.headers)
        self.assertEqual(deleted_message_branch.status_code, 200, deleted_message_branch.text)
        regenerate = self.client.post(
            f"/api/roleplay/conversations/{conversation_id}/messages/{messages[1]['message_id']}/regenerate",
            headers=self.headers,
            json={"requirement": "more concise"},
        )
        self.assertEqual(regenerate.status_code, 200, regenerate.text)
        regen_task = self.wait_task(regenerate.json()["task_id"])
        self.assertEqual(regen_task["status"], "completed", regen_task)
        regen_record = self.client.get(f"/api/roleplay/conversations/{conversation_id}", headers=self.headers).json()["conversation"]
        regen_branch = regen_record["active_branch_id"]
        self.assertNotEqual(regen_branch, "main")
        switched_after_regen = self.client.post(
            f"/api/roleplay/conversations/{conversation_id}/branches/main/activate",
            headers=self.headers,
            json={},
        )
        self.assertEqual(switched_after_regen.status_code, 200, switched_after_regen.text)
        deleted_regen_branch = self.client.delete(f"/api/roleplay/conversations/{conversation_id}/branches/{regen_branch}", headers=self.headers)
        self.assertEqual(deleted_regen_branch.status_code, 200, deleted_regen_branch.text)
        deleted_message = self.client.delete(f"/api/roleplay/conversations/{conversation_id}/messages/{messages[0]['message_id']}", headers=self.headers)
        self.assertEqual(deleted_message.status_code, 200, deleted_message.text)
        self.assertEqual(len(deleted_message.json()["conversation"]["structured_messages"]), len(messages) - 1)
        messages = self.client.get(f"/api/roleplay/conversations/{conversation_id}", headers=self.headers).json()["conversation"]["structured_messages"]
        fork = self.client.post(
            f"/api/roleplay/conversations/{conversation_id}/branches/fork",
            headers=self.headers,
            json={"message_id": messages[0]["message_id"], "title": "Alt"},
        )
        self.assertEqual(fork.status_code, 200, fork.text)
        branch_id = fork.json()["branch"]["branch_id"]
        self.assertEqual(fork.json()["active_branch_id"], branch_id)
        switched = self.client.post(
            f"/api/roleplay/conversations/{conversation_id}/branches/main/activate",
            headers=self.headers,
            json={},
        )
        self.assertEqual(switched.status_code, 200, switched.text)
        self.assertEqual(switched.json()["conversation"]["active_branch_id"], "main")
        deleted_branch = self.client.delete(f"/api/roleplay/conversations/{conversation_id}/branches/{branch_id}", headers=self.headers)
        self.assertEqual(deleted_branch.status_code, 200, deleted_branch.text)
        self.assertEqual(deleted_branch.json()["active_branch_id"], "main")

        deleted_conversation = self.client.delete(f"/api/roleplay/conversations/{conversation_id}", headers=self.headers)
        self.assertEqual(deleted_conversation.status_code, 200, deleted_conversation.text)
        deleted_sender = self.client.delete(f"/api/roleplay/senders/{sender_id}", headers=self.headers)
        self.assertEqual(deleted_sender.status_code, 200, deleted_sender.text)
        deleted_scene = self.client.delete(f"/api/roleplay/scenes/{scene_id}", headers=self.headers)
        self.assertEqual(deleted_scene.status_code, 200, deleted_scene.text)
        deleted_character = self.client.delete(f"/api/roleplay/characters/{character_id}", headers=self.headers)
        self.assertEqual(deleted_character.status_code, 200, deleted_character.text)

    def test_continuation_model_tasks_require_api_key(self):
        denied = self.client.post(
            "/api/continuation/generate",
            headers=self.headers,
            json={"title": "NoApi", "source_text": "旧文"},
        )
        self.assertEqual(denied.status_code, 400)
        self.assertIn("API", denied.text)

    def test_agent_advisor_preserves_fiction_context_and_manual_references(self):
        self.configure_text_api()
        self.client.post("/api/books", headers=self.headers, json={"title": "AdvisorDesk"})
        captured = {}

        def fake_ask(_service, request):
            from core.agent.advisor import AdvisorResult
            captured["request"] = request
            return AdvisorResult("run-web", "session-web", "answer", "completed", [], ["source-a"], [], "")

        with patch("core.agent.advisor.WritingAdvisorService.ask", fake_ask):
            response = self.client.post(
                "/api/books/AdvisorDesk/agent/advisor",
                headers=self.headers,
                json={
                    "message": "检查下一章冲突",
                    "manual_references": ["第1章结尾", "角色A动机"],
                    "fiction_context": False,
                },
            )
            self.assertEqual(response.status_code, 200, response.text)
            task = self.wait_task(response.json()["task_id"])

        self.assertEqual(task["status"], "completed", task)
        request = captured["request"]
        self.assertEqual(request.book_title, "AdvisorDesk")
        self.assertEqual(request.message, "检查下一章冲突")
        self.assertEqual(request.manual_references, ["第1章结尾", "角色A动机"])
        self.assertFalse(request.fiction_context)
        saved = self.client.post(
            "/api/books/AdvisorDesk/agent/advice",
            headers=self.headers,
            json={"run_id": "run-web", "text": "answer", "title": "冲突构思"},
        )
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertTrue(saved.json()["artifact_id"])
        state = self.client.get("/api/books/AdvisorDesk/agent/state", headers=self.headers)
        self.assertTrue(any(item.get("content") == "answer" for item in state.json()["advice"]))

        from core.agent.repository import AgentRepository
        token = self.headers["Authorization"].split(" ", 1)[1]
        ctx = self.runtime.context_from_token(token)
        manifest = ctx.novel_manager.ensure_workspace("AdvisorDesk")
        repo = AgentRepository(ctx.novel_manager.get_workspace("AdvisorDesk"))
        session = repo.create_session(manifest.book_id, "AdvisorDesk", "writing_advisor", "写作顾问")
        session.messages = [
            {"role": "user", "content": "历史问题", "at": "T1"},
            {"role": "assistant", "content": "历史回答", "at": "T2"},
        ]
        repo.save_session(session)
        history_state = self.client.get("/api/books/AdvisorDesk/agent/state", headers=self.headers)
        self.assertEqual([item["content"] for item in history_state.json()["advisor_history"]], ["历史问题", "历史回答"])
        deleted_history = self.client.delete("/api/books/AdvisorDesk/agent/advisor/history/0", headers=self.headers)
        self.assertEqual(deleted_history.status_code, 200, deleted_history.text)
        self.assertEqual([item["content"] for item in deleted_history.json()["advisor_history"]], ["历史回答"])
        missing_history = self.client.delete("/api/books/AdvisorDesk/agent/advisor/history/9", headers=self.headers)
        self.assertEqual(missing_history.status_code, 404)
        cleared_history = self.client.delete("/api/books/AdvisorDesk/agent/advisor/history", headers=self.headers)
        self.assertEqual(cleared_history.status_code, 200, cleared_history.text)
        self.assertEqual(cleared_history.json()["removed"], 1)
        self.assertEqual(cleared_history.json()["advisor_history"], [])

    def test_agent_workbench_sessions_run_events_and_controls(self):
        self.configure_text_api()
        self.client.post("/api/books", headers=self.headers, json={"title": "WorkbenchDesk"})
        created = self.client.post(
            "/api/books/WorkbenchDesk/agent/sessions",
            headers=self.headers,
            json={"agent_kind": "writing_advisor", "title": "Web Workbench"},
        )
        self.assertEqual(created.status_code, 200, created.text)
        session = created.json()["session"]
        self.assertEqual(session["title"], "Web Workbench")
        state = self.client.get("/api/books/WorkbenchDesk/agent/state", headers=self.headers)
        self.assertTrue(any(item["session_id"] == session["session_id"] for item in state.json()["sessions"]))
        loaded = self.client.get(f"/api/books/WorkbenchDesk/agent/sessions/{session['session_id']}", headers=self.headers)
        self.assertEqual(loaded.status_code, 200, loaded.text)

        from dataclasses import asdict
        from core.agent.backends import BackendStatus
        from core.agent.repository import AgentRepository
        from core.agent.types import AgentEvent, AgentRun
        token = self.headers["Authorization"].split(" ", 1)[1]
        ctx = self.runtime.context_from_token(token)
        repo = AgentRepository(ctx.novel_manager.get_workspace("WorkbenchDesk"))
        captured = {}

        def fake_build_agent_backend(**kwargs):
            sink = kwargs["event_sink"]
            class FakeBackend:
                def run(self, request):
                    captured["message"] = request.user_message
                    captured["manual_references"] = request.manual_references
                    event = AgentEvent("run-web-workbench", 1, "run_started", payload={"agent_kind": request.agent_kind})
                    repo.append_event(event.run_id, asdict(event))
                    sink(event)
                    return AgentRun(
                        event.run_id,
                        request.session_id,
                        request.book_id,
                        request.book_title,
                        request.agent_kind,
                        request.model,
                        status="completed",
                        messages=[{"role": "assistant", "content": "done"}],
                        tool_calls=[{"request": {"tool_name": "inspect"}, "result": {"success": True}}],
                    )
                def pause(self, run_id):
                    captured.setdefault("controls", []).append(("pause", run_id))
                    return True
                def resume(self, run_id, payload=None):
                    captured.setdefault("controls", []).append(("resume", run_id, payload))
                    return True
                def cancel(self, run_id):
                    captured.setdefault("controls", []).append(("cancel", run_id))
                    return True
            return FakeBackend(), BackendStatus("legacy", "legacy")

        with patch("core.agent.backends.build_agent_backend", fake_build_agent_backend):
            run_response = self.client.post(
                f"/api/books/WorkbenchDesk/agent/sessions/{session['session_id']}/run",
                headers=self.headers,
                json={"message": "分析工具调用", "manual_references": ["第一章", "角色A"]},
            )
            self.assertEqual(run_response.status_code, 200, run_response.text)
            task = self.wait_task(run_response.json()["task_id"])

        self.assertEqual(task["status"], "completed", task)
        self.assertEqual(task["metadata"]["kind"], "agent_workbench_run")
        self.assertEqual(captured["message"], "分析工具调用")
        self.assertEqual(captured["manual_references"], ["第一章", "角色A"])
        run_detail = self.client.get("/api/books/WorkbenchDesk/agent/runs/run-web-workbench", headers=self.headers)
        self.assertEqual(run_detail.status_code, 200, run_detail.text)
        self.assertEqual(run_detail.json()["run"]["tool_calls"][0]["request"]["tool_name"], "inspect")
        self.assertEqual(run_detail.json()["events"][0]["event_type"], "run_started")

        class ControlBackend:
            def pause(self, run_id):
                captured.setdefault("controls", []).append(("pause", run_id))
                return True
            def resume(self, run_id, payload=None):
                captured.setdefault("controls", []).append(("resume", run_id, payload))
                return True
            def cancel(self, run_id):
                captured.setdefault("controls", []).append(("cancel", run_id))
                return True
        self.runtime.register_agent_backend("alice", "run-active", ControlBackend())
        for action in ("pause", "resume", "cancel"):
            controlled = self.client.post(f"/api/books/WorkbenchDesk/agent/runs/run-active/{action}", headers=self.headers, json={})
            self.assertEqual(controlled.status_code, 200, controlled.text)
        self.runtime.unregister_agent_backend("run-active")
        self.assertEqual([item[0] for item in captured["controls"][-3:]], ["pause", "resume", "cancel"])
    def test_agent_extra_generation_creates_chapter_tree_node(self):
        self.configure_text_api()
        self.client.post("/api/books", headers=self.headers, json={"title": "ExtraDesk"})
        imported = self.client.post(
            "/api/continuation/import",
            headers=self.headers,
            json={"title": "ExtraDesk", "sections": [{"title": "One", "content": "第一章正文"}]},
        )
        self.assertEqual(imported.status_code, 200, imported.text)
        self.assertEqual(self.wait_task(imported.json()["task_id"])["status"], "completed")
        tree = self.client.get("/api/books/ExtraDesk/chapter-tree", headers=self.headers).json()
        start_node_id = next(node["id"] for node in tree["nodes"] if node.get("chapter_num") == 1)

        from dataclasses import asdict
        from core.agent.extra_generation import AgentExtraPlan, AgentExtraResult
        from core.agent.types import now_iso
        token = self.headers["Authorization"].split(" ", 1)[1]
        ctx = self.runtime.context_from_token(token)
        captured = {}

        def fake_prepare(service, request):
            captured["request"] = request
            plan = AgentExtraPlan(
                plan_id="extra-plan-web",
                extra_type=request.extra_type,
                chapter_goal="写一段前传",
                scenes=[{"title": "分歧", "purpose": "展示选择", "conflict": "旧决定被改写", "outcome": "进入新分支"}],
                character_states=[],
                foreshadowing_actions=[],
                selected_world_entities=[],
                selected_history=[],
                constraints=["不改主线事实"],
                insertion_report={"description": "从第一章后插入", "start_node_id": request.start_node_id, "end_node_id": request.end_node_id, "reference_node_id": request.reference_node_id},
                context_report={"preview": "fake", "content": "上下文"},
                selected_skills=[],
                created_at=now_iso(),
            )
            workspace = ctx.novel_manager.get_workspace(request.book_title)
            workspace.storage.write_json(
                f"{workspace.agent_root}/extra_runs/{plan.plan_id}.json",
                {"schema_version": 1, "status": "prepared", "request": asdict(request), "plan": plan.to_dict(), "created_at": now_iso()},
            )
            return plan

        def fake_generate(_service, request, plan):
            captured["generate"] = (request, plan)
            return AgentExtraResult(plan.plan_id, "请写前传正文", {"preview": "fake"})

        with patch("core.agent.extra_generation.AgentExtraGenerationService.prepare", fake_prepare), patch("core.agent.extra_generation.AgentExtraGenerationService.generate", fake_generate):
            planned = self.client.post(
                "/api/books/ExtraDesk/agent/extra/plan",
                headers=self.headers,
                json={"extra_type": "prequel", "start_node_id": start_node_id, "reference_node_id": start_node_id, "title": "另一条路", "plot": "如果当时没有出发", "requirement": "保持人物动机", "target_words": 100},
            )
            self.assertEqual(planned.status_code, 200, planned.text)
            plan_task = self.wait_task(planned.json()["task_id"])
            self.assertEqual(plan_task["status"], "completed", plan_task)
            generated = self.client.post(
                "/api/books/ExtraDesk/agent/extra/generate",
                headers=self.headers,
                json={"plan_id": "extra-plan-web"},
            )
            self.assertEqual(generated.status_code, 200, generated.text)
            generate_task = self.wait_task(generated.json()["task_id"])

        self.assertEqual(generate_task["status"], "completed", generate_task)
        self.assertEqual(captured["request"].extra_type, "prequel")
        self.assertEqual(captured["request"].start_node_id, start_node_id)
        tree_after = self.client.get("/api/books/ExtraDesk/chapter-tree", headers=self.headers).json()
        extra_nodes = [node for node in tree_after["nodes"] if node.get("storage_kind") == "extra_uuid"]
        self.assertEqual(len(extra_nodes), 1)
        extra_node = extra_nodes[0]
        self.assertEqual(extra_node.get("node_kind"), "prequel")
        extra_content = self.client.get(f"/api/books/ExtraDesk/nodes/{extra_node['id']}", headers=self.headers)
        self.assertEqual(extra_content.status_code, 200, extra_content.text)
        self.assertEqual(extra_content.json()["content"], "正文")
    def test_agent_world_maintenance_retry_task(self):
        self.configure_text_api()
        self.client.post("/api/books", headers=self.headers, json={"title": "MaintDesk"})
        token = self.headers["Authorization"].split(" ", 1)[1]
        ctx = self.runtime.context_from_token(token)
        ctx.novel_manager.ensure_workspace("MaintDesk")
        workspace = ctx.novel_manager.get_workspace("MaintDesk")
        task_id = "world_ch0001_v001"
        workspace.storage.write_json(
            f"{workspace.agent_root}/maintenance/pending/{task_id}.json",
            {
                "schema_version": 1,
                "task_id": task_id,
                "book_title": "MaintDesk",
                "chapter_num": 1,
                "version": 1,
                "model": "fake-model",
                "global_user_prompt": "",
                "xp_mode": False,
                "plan": {},
                "error": "extract failed",
                "created_at": "2026-01-01T00:00:00",
            },
        )
        state = self.client.get("/api/books/MaintDesk/agent/state", headers=self.headers)
        self.assertEqual(state.status_code, 200, state.text)
        pending = state.json()["pending_world_maintenance"]
        self.assertEqual(pending[0]["task_id"], task_id)
        self.assertEqual(pending[0]["error"], "extract failed")

        from core.agent.world_maintenance import WorldMaintenanceResult
        captured = {}

        def fake_retry(_service, client, book_title, retry_task_id):
            captured["book_title"] = book_title
            captured["task_id"] = retry_task_id
            captured["client"] = client
            return WorldMaintenanceResult(retry_task_id, "completed", 1, 1, added=[{"id": "char-ok"}])

        with patch("core.agent.world_maintenance.WorldBibleMaintenanceService.retry", fake_retry):
            retry = self.client.post(
                f"/api/books/MaintDesk/agent/world/maintenance/{task_id}/retry",
                headers=self.headers,
                json={},
            )
            self.assertEqual(retry.status_code, 200, retry.text)
            task = self.wait_task(retry.json()["task_id"])

        self.assertEqual(task["status"], "completed", task)
        self.assertEqual(task["metadata"]["kind"], "agent_world_maintenance_retry")
        self.assertEqual(captured["book_title"], "MaintDesk")
        self.assertEqual(captured["task_id"], task_id)
    def test_agent_embedding_settings_token_versions_and_snapshots(self):
        self.client.post("/api/books", headers=self.headers, json={"title": "Desk"})
        denied = self.client.put(
            "/api/settings/agent-embedding",
            headers=self.headers,
            json={"settings": {"embedding_api_key": "secret", "agent_web_api_key": "web-key"}},
        )
        self.assertEqual(denied.status_code, 403)

        ticket = self.client.post(
            "/api/auth/confirm",
            headers=self.headers,
            json={"password": "pass123"},
        ).json()["sensitive_ticket"]
        global_prompt = self.client.put(
            "/api/settings",
            headers=self.headers,
            json={"settings": {"global_user_prompt": "偏好：保留冷静克制的叙述"}},
        )
        self.assertEqual(global_prompt.status_code, 200, global_prompt.text)
        self.assertEqual(global_prompt.json()["settings"]["global_user_prompt"], "偏好：保留冷静克制的叙述")
        prefs_path = os.path.join(AuthManager.get_user_dir("alice"), "user_prefs.enc")
        prefs = AuthManager.decrypt_json(self.enc_key, prefs_path)
        self.assertIsNotNone(prefs)
        self.assertEqual(prefs["global_user_prompt"], "偏好：保留冷静克制的叙述")
        settings_reload = self.client.get("/api/settings", headers=self.headers)
        self.assertEqual(settings_reload.json()["settings"]["global_user_prompt"], "偏好：保留冷静克制的叙述")

        presets = self.client.get("/api/settings/presets", headers=self.headers)
        self.assertEqual(presets.status_code, 200, presets.text)
        default_names = presets.json()["default_names"]
        self.assertTrue(default_names)
        saved_preset = self.client.put(
            "/api/settings/presets/WebFast",
            headers=self.headers,
            json={"name": "WebFast", "preset": {"temp": 55, "top_p": 88, "fp": -5, "max_tokens": 4096}},
        )
        self.assertEqual(saved_preset.status_code, 200, saved_preset.text)
        self.assertEqual(saved_preset.json()["presets"]["WebFast"]["max_tokens"], 4096)
        saved_slow_preset = self.client.put(
            "/api/settings/presets/WebSlow",
            headers=self.headers,
            json={"name": "WebSlow", "preset": {"temp": 35, "top_p": 80, "fp": 0, "max_tokens": 8192}},
        )
        self.assertEqual(saved_slow_preset.status_code, 200, saved_slow_preset.text)
        set_current = self.client.put(
            "/api/settings/presets/current",
            headers=self.headers,
            json={"name": "WebFast"},
        )
        self.assertEqual(set_current.status_code, 200, set_current.text)
        self.assertEqual(set_current.json()["current_preset"], "WebFast")
        presets_after_current = self.client.get("/api/settings/presets", headers=self.headers)
        self.assertEqual(presets_after_current.json()["current_preset"], "WebFast")
        settings_for_params = self.client.get("/api/settings", headers=self.headers).json()["settings"]
        generated_params = generation_params(settings_for_params, {"text": {"model": "unit-model"}})
        self.assertEqual(generated_params["model"], "unit-model")
        self.assertAlmostEqual(generated_params["temperature"], 0.55)
        self.assertAlmostEqual(generated_params["top_p"], 0.88)
        self.assertAlmostEqual(generated_params["frequency_penalty"], -0.05)
        self.assertEqual(generated_params["max_tokens"], 4096)
        saved_long_preset = self.client.put(
            "/api/settings/presets/WebLong",
            headers=self.headers,
            json={"name": "WebLong", "preset": {"temp": 70, "top_p": 90, "fp": 0, "max_tokens": 65536}},
        )
        self.assertEqual(saved_long_preset.status_code, 200, saved_long_preset.text)
        long_settings = self.client.get("/api/settings", headers=self.headers).json()["settings"]
        long_params = generation_params(long_settings, {"text": {"model": "unit-model"}})
        self.assertEqual(long_params["max_tokens"], 65536)
        missing_current = self.client.put(
            "/api/settings/presets/current",
            headers=self.headers,
            json={"name": "MissingPreset"},
        )
        self.assertEqual(missing_current.status_code, 404)
        theme = self.client.put("/api/settings/theme", headers=self.headers, json={"theme": "light"})
        self.assertEqual(theme.status_code, 200, theme.text)
        self.assertEqual(theme.json()["theme"], "light")
        deleted_preset = self.client.delete("/api/settings/presets/WebFast", headers=self.headers)
        self.assertEqual(deleted_preset.status_code, 200, deleted_preset.text)
        denied_default_delete = self.client.delete(f"/api/settings/presets/{default_names[0]}", headers=self.headers)
        self.assertEqual(denied_default_delete.status_code, 400)
        reset_presets = self.client.post("/api/settings/presets/reset", headers=self.headers, json={})
        self.assertEqual(reset_presets.status_code, 200, reset_presets.text)
        self.assertIn(default_names[0], reset_presets.json()["presets"])

        saved = self.client.put(
            "/api/settings/agent-embedding",
            headers={**self.headers, "X-Sensitive-Ticket": ticket},
            json={"settings": {
                "novel_generation_mode": "agent",
                "agent_skills_enabled": True,
                "agent_runtime_backend": "legacy",
                "retrieval_backend": "classic",
                "embedding_model": "embed-test",
                "embedding_api_key": "secret",
                "agent_web_enabled": True,
                "agent_web_endpoint": "https://search.example/api",
                "agent_web_method": "GET",
                "agent_web_api_key": "web-key",
                "agent_web_auth_header": "X-Api-Key",
                "agent_web_auth_prefix": "Token",
                "agent_web_query_field": "q",
                "agent_web_results_path": "items",
                "agent_web_title_field": "name",
                "agent_web_url_field": "href",
                "agent_web_snippet_field": "summary",
                "agent_web_max_results": 3,
                "agent_web_timeout_seconds": 9,
            }},
        )
        self.assertEqual(saved.status_code, 200, saved.text)
        loaded = self.client.get("/api/settings/agent-embedding", headers=self.headers).json()["settings"]
        self.assertEqual(loaded["novel_generation_mode"], "agent")
        self.assertTrue(loaded["embedding_api_key_configured"])
        self.assertTrue(loaded["agent_web_api_key_configured"])
        self.assertEqual(loaded["agent_web_method"], "GET")
        self.assertEqual(loaded["agent_web_auth_header"], "X-Api-Key")
        self.assertEqual(loaded["agent_web_auth_prefix"], "Token")
        self.assertEqual(loaded["agent_web_query_field"], "q")
        self.assertEqual(loaded["agent_web_results_path"], "items")
        self.assertEqual(loaded["agent_web_title_field"], "name")
        self.assertEqual(loaded["agent_web_url_field"], "href")
        self.assertEqual(loaded["agent_web_snippet_field"], "summary")
        self.assertEqual(loaded["agent_web_max_results"], 3)
        self.assertEqual(loaded["agent_web_timeout_seconds"], 9)
        with patch("core.agent.web_search.WebSearchClient.search", return_value={"results": [{"title": "结果", "url": "https://example.com", "snippet": "摘要"}]}) as search:
            web_test = self.client.post(
                "/api/settings/agent-web/test",
                headers=self.headers,
                json={"query": "测试搜索"},
            )
        self.assertEqual(web_test.status_code, 200, web_test.text)
        self.assertEqual(web_test.json()["count"], 1)
        search.assert_called_once()

        classic_embedding = self.client.post("/api/settings/embedding/test", headers=self.headers, json={})
        self.assertEqual(classic_embedding.status_code, 400)

        hybrid = self.client.put(
            "/api/settings/agent-embedding",
            headers=self.headers,
            json={"settings": {"retrieval_backend": "hybrid"}},
        )
        self.assertEqual(hybrid.status_code, 200, hybrid.text)

        class FakeHybridBackend:
            backend_name = "hybrid"
            _embedder = SimpleNamespace(get_query_embedding=lambda _text: [0.1, 0.2, 0.3])

        with patch("core.retrieval.build_retrieval_backend", return_value=(FakeHybridBackend(), "")) as build_backend:
            embedding_test = self.client.post("/api/settings/embedding/test", headers=self.headers, json={})
        self.assertEqual(embedding_test.status_code, 200, embedding_test.text)
        self.assertEqual(embedding_test.json()["dimension"], 3)
        self.assertEqual(build_backend.call_args.args[1]["retrieval_backend"], "hybrid")

        reset_retrieval = self.client.put(
            "/api/settings/agent-embedding",
            headers=self.headers,
            json={"settings": {"retrieval_backend": "classic"}},
        )
        self.assertEqual(reset_retrieval.status_code, 200, reset_retrieval.text)
        imported = self.client.post(
            "/api/continuation/import",
            headers=self.headers,
            json={"title": "Desk", "sections": [{"title": "One", "content": "正文"}]},
        )
        self.assertEqual(self.wait_task(imported.json()["task_id"])["status"], "completed")
        versions = self.client.get("/api/books/Desk/chapters/1/versions", headers=self.headers)
        self.assertEqual(versions.status_code, 200, versions.text)
        self.assertEqual(versions.json()["active"], 1)

        snapshot = self.client.post("/api/books/Desk/snapshots?message=Manual", headers=self.headers, json={})
        self.assertEqual(snapshot.status_code, 200, snapshot.text)
        self.assertEqual(snapshot.json()["snapshot"]["message"], "Manual")
        snapshot_id = snapshot.json()["snapshot"]["snapshot_id"]
        status = self.client.get(f"/api/books/Desk/snapshots/{snapshot_id}/status", headers=self.headers)
        self.assertEqual(status.status_code, 200, status.text)
        deleted_snapshot = self.client.delete(f"/api/books/Desk/snapshots/{snapshot_id}", headers=self.headers)
        self.assertEqual(deleted_snapshot.status_code, 200, deleted_snapshot.text)
        snapshots_after_delete = self.client.get("/api/books/Desk/snapshots", headers=self.headers).json()["snapshots"]
        self.assertNotIn(snapshot_id, {item["snapshot_id"] for item in snapshots_after_delete})

        tokens = self.client.get("/api/token-log", headers=self.headers)
        self.assertEqual(tokens.status_code, 200, tokens.text)
        self.assertIn("summary", tokens.json())
        cleared = self.client.delete("/api/token-log", headers=self.headers)
        self.assertEqual(cleared.status_code, 200, cleared.text)

    def test_token_log_filters_grouping_and_export(self):
        token = self.headers["Authorization"].split(" ", 1)[1]
        ctx = self.runtime.context_from_token(token)
        ctx.token_log_manager.add_entry(
            operation="classic_generate",
            direction="send",
            strategy="Novel",
            model="model-a",
            content="alpha scene prompt",
            usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        )
        ctx.token_log_manager.add_entry(
            operation="roleplay_chat",
            direction="recv",
            strategy="Roleplay",
            model="model-b",
            content="beta role response",
            usage={"prompt_tokens": 20, "completion_tokens": 7, "total_tokens": 27},
        )
        ctx.token_log_manager.add_entry(
            operation="classic_generate",
            direction="recv",
            strategy="Novel",
            model="model-a",
            content="gamma scene response",
            usage={"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
        )

        all_tokens = self.client.get("/api/token-log", headers=self.headers)
        self.assertEqual(all_tokens.status_code, 200, all_tokens.text)
        all_data = all_tokens.json()
        self.assertEqual(all_data["total"], 3)
        self.assertEqual(all_data["overall_total"], 3)
        self.assertIn("model-a", all_data["facets"]["models"])
        self.assertIn("classic_generate", all_data["facets"]["operations"])
        self.assertEqual(all_data["summary"]["by_model"]["model-a"]["total_tokens"], 20)
        self.assertEqual(all_data["summary"]["by_operation"]["classic_generate"]["count"], 2)
        self.assertEqual(all_data["summary"]["estimated_cost"]["currency"], "USD")
        self.assertEqual(all_data["summary"]["estimated_cost"]["total_cost"], 0.0)
        self.assertEqual(all_data["summary"]["by_model"]["model-a"]["estimated_cost"]["model"], "model-a")

        filtered = self.client.get(
            "/api/token-log?model=model-a&operation=classic_generate&q=alpha",
            headers=self.headers,
        )
        self.assertEqual(filtered.status_code, 200, filtered.text)
        data = filtered.json()
        self.assertEqual(data["total"], 1)
        self.assertEqual(data["overall_total"], 3)
        self.assertEqual(data["entries"][0]["content_preview"], "alpha scene prompt")
        self.assertEqual(data["summary"]["totals"], {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15})
        self.assertEqual(data["filters"]["model"], "model-a")
        self.assertEqual(data["filters"]["operation"], "classic_generate")

        day = data["entries"][0]["timestamp"][:10]
        by_date = self.client.get(f"/api/token-log?date_from={day}&date_to={day}", headers=self.headers)
        self.assertEqual(by_date.status_code, 200, by_date.text)
        self.assertEqual(by_date.json()["summary"]["by_date"][day]["count"], 3)

        exported = self.client.post(
            "/api/token-log/export?model=model-a&operation=classic_generate&q=alpha",
            headers=self.headers,
            json={},
        )
        self.assertEqual(exported.status_code, 200, exported.text)
        download = self.client.get(f'{exported.json()["download"]["download_url"]}?token={token}')
        self.assertEqual(download.status_code, 200, download.text)
        exported_data = download.json()
        self.assertEqual(len(exported_data["entries"]), 1)
        self.assertEqual(exported_data["summary"]["totals"]["total_tokens"], 15)
        self.assertIn("estimated_cost", exported_data["summary"])
        self.assertEqual(exported_data["filters"]["q"], "alpha")
if __name__ == "__main__":
    unittest.main()

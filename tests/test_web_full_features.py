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
        self.assertEqual([item["v"] for item in remaining.json()["versions"]], [2])

    def test_world_bible_desktop_management_actions(self):
        self.client.post("/api/books", headers=self.headers, json={"title": "WorldDesk"})
        world_data = {
            "characters": [
                {"id": "char-a", "name": "A", "aliases": [], "traits": "leader", "importance": "major", "source_chapter": 1, "last_updated_chapter": 1},
                {"id": "char-b", "name": "B", "aliases": ["Bee"], "traits": "scout", "importance": "minor", "source_chapter": 1, "last_updated_chapter": 1},
                {"id": "char-c", "name": "C", "traits": "mage", "source_chapter": 1, "last_updated_chapter": 1},
                {"id": "char-d", "name": "D", "traits": "mage alias", "source_chapter": 1, "last_updated_chapter": 1},
            ],
            "locations": [{"id": "loc-a", "name": "City", "description": "base", "source_chapter": 1}],
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
        runs = self.client.get("/api/continuation/runs?title=ContBook", headers=self.headers)
        self.assertEqual(runs.status_code, 200, runs.text)


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

        exported = self.client.post(
            f"/api/roleplay/conversations/{conversation_id}/export",
            headers=self.headers,
            json={"fmt": "txt"},
        )
        self.assertEqual(exported.status_code, 200, exported.text)
        self.assertIn("download", exported.json())

        branches = self.client.get(f"/api/roleplay/conversations/{conversation_id}/branches", headers=self.headers)
        self.assertEqual(branches.status_code, 200, branches.text)
        self.assertEqual(branches.json()["active_branch_id"], "main")
        self.assertEqual(len(branches.json()["branches"]), 1)
        messages = record["structured_messages"]
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
        prefs = AuthManager.decrypt_json(self.enc_key, os.path.join(auth_module.USERS_DIR, "alice", "user_prefs.enc"))
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
                "agent_web_api_key": "web-key",
            }},
        )
        self.assertEqual(saved.status_code, 200, saved.text)
        loaded = self.client.get("/api/settings/agent-embedding", headers=self.headers).json()["settings"]
        self.assertEqual(loaded["novel_generation_mode"], "agent")
        self.assertTrue(loaded["embedding_api_key_configured"])

        imported = self.client.post(
            "/api/continuation/import",
            headers=self.headers,
            json={"title": "Desk", "sections": [{"title": "One", "content": "正文"}]},
        )
        self.assertEqual(self.wait_task(imported.json()["task_id"])["status"], "completed")
        versions = self.client.get("/api/books/Desk/chapters/1/versions", headers=self.headers)
        self.assertEqual(versions.status_code, 200, versions.text)
        self.assertEqual(versions.json()["active"], 1)

        snapshot = self.client.post("/api/books/Desk/snapshots", headers=self.headers, json={})
        self.assertEqual(snapshot.status_code, 200, snapshot.text)
        snapshot_id = snapshot.json()["snapshot"]["snapshot_id"]
        status = self.client.get(f"/api/books/Desk/snapshots/{snapshot_id}/status", headers=self.headers)
        self.assertEqual(status.status_code, 200, status.text)

        tokens = self.client.get("/api/token-log", headers=self.headers)
        self.assertEqual(tokens.status_code, 200, tokens.text)
        self.assertIn("summary", tokens.json())
        cleared = self.client.delete("/api/token-log", headers=self.headers)
        self.assertEqual(cleared.status_code, 200, cleared.text)
if __name__ == "__main__":
    unittest.main()



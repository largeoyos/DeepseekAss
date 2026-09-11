"""Real end-to-end smoke test for the packaged local control interface.

The test uses a copied distribution and an isolated encrypted user directory,
so it never reads or modifies the developer's real users or bookshelf.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import core.auth_manager as auth_module
from core.auth_manager import AuthManager
from core.control_auth import ControlGrantStore
from core.control_service import approve_change_with_password
from core.novel_manager import NovelManager
from core.settings_manager import SettingsManager
from core.world_bible import dict_to_world_bible


DEFAULT_DIST = ROOT / "dist" / "DeepseekAssControl"


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


@contextmanager
def _temporary_workspace():
    """Allow a packaged Windows child a moment to release loaded DLL files."""
    path = Path(tempfile.mkdtemp(prefix=".control-smoke-", dir=ROOT))
    try:
        yield path
    finally:
        for attempt in range(20):
            try:
                shutil.rmtree(path)
                break
            except PermissionError:
                if attempt == 19:
                    raise
                time.sleep(0.25)


@contextmanager
def _mock_tool_model():
    """Serve one local OpenAI-compatible tool-call response without external cost."""
    calls: list[dict] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0") or 0)
            request = json.loads(self.rfile.read(length).decode("utf-8"))
            calls.append(request)
            arguments = {
                "chapter_num": 2,
                "chapter_title": "钟楼的回声",
                "content": "林舟踏进废弃钟楼，雪水沿着生锈的齿轮滴落。他在停摆的钟面后找到了第二封信，上面的日期仍然来自十年后。",
                "parent_id": "ch0001_v001",
                "reason": "本地模拟写作 Agent 端到端验证",
            }
            response = {
                "id": "chatcmpl-smoke",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": "smoke-tool-model",
                "choices": [{
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [{
                            "id": "call-smoke-propose",
                            "type": "function",
                            "function": {"name": "chapter.propose", "arguments": json.dumps(arguments, ensure_ascii=False)},
                        }],
                    },
                    "finish_reason": "tool_calls",
                }],
                "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
            }
            body = json.dumps(response, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format, *_args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _run_json(
    executable: Path,
    username: str,
    token: str,
    arguments: list[str],
    *,
    stdin: str | None = None,
) -> dict:
    env = os.environ.copy()
    env["DEEPSEEKASS_CONTROL_TOKEN"] = token
    completed = subprocess.run(
        [str(executable), "--username", username, *arguments],
        input=stdin,
        text=True,
        encoding="utf-8",
        capture_output=True,
        env=env,
        check=False,
        timeout=30,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"CLI failed ({completed.returncode}): {' '.join(arguments)}\n"
            f"stdout={completed.stdout}\nstderr={completed.stderr}"
        )
    payload = json.loads(completed.stdout)
    _require(payload.get("ok") is True, f"CLI returned an error: {payload}")
    return payload["data"]


async def _exercise_mcp(executable: Path, username: str, token: str, title: str) -> dict:
    from mcp import Client
    from mcp.client.stdio import StdioServerParameters, stdio_client

    env = os.environ.copy()
    env["DEEPSEEKASS_CONTROL_TOKEN"] = token
    parameters = StdioServerParameters(
        command=str(executable),
        args=["--username", username, "mcp"],
        cwd=str(executable.parent),
        env=env,
    )
    async with Client(stdio_client(parameters), read_timeout_seconds=20) as client:
        tools = await client.list_tools()
        _require(len(tools.tools) == 15, f"expected 15 MCP tools, got {len(tools.tools)}")
        _require(any(item.name == "run_writing_agent" for item in tools.tools), "writing Agent tool was not exposed")
        resources = await client.list_resources()
        guide_uri = "deepseekass://ai-control-guide"
        _require(any(str(item.uri) == guide_uri for item in resources.resources), "AI guide MCP resource was not exposed")
        guide = await client.read_resource(guide_uri)
        guide_text = "\n".join(str(getattr(item, "text", "")) for item in guide.contents)
        _require("run_writing_agent" in guide_text, "AI guide resource is incomplete")
        control_settings = await client.call_tool("get_control_settings", {})
        _require(not control_settings.is_error, "MCP get_control_settings returned an error")
        _require(control_settings.structured_content["capabilities"]["writing_agent"] is True, "writing Agent capability is disabled")
        project = await client.call_tool("get_project", {"book": title})
        _require(not project.is_error, "MCP get_project returned an error")
        _require(project.structured_content["project"]["title"] == title, "MCP returned the wrong project")
        chapter = await client.call_tool(
            "read_chapter",
            {"book": title, "node_id": "ch0001_v001", "start": 0, "max_chars": 8},
        )
        _require(not chapter.is_error, "MCP read_chapter returned an error")
        return {
            "tools_discovered": len(tools.tools),
            "project_title": project.structured_content["project"]["title"],
            "chapter_page_chars": len(chapter.structured_content["content"]),
            "writing_agent_available": True,
            "ai_guide_resource_read": True,
        }


def run(dist_dir: Path) -> dict:
    _require((dist_dir / "DeepseekAssControl.exe").is_file(), f"missing packaged executable: {dist_dir}")
    with _temporary_workspace() as temp_dir:
        isolated_dist = temp_dir / "DeepseekAssControl"
        shutil.copytree(dist_dir, isolated_dist)
        executable = isolated_dist / "DeepseekAssControl.exe"

        data_root = temp_dir / "data"
        users_dir = data_root / "users"
        old_users_dir, old_users_db = auth_module.USERS_DIR, auth_module.USERS_DB
        old_data_root = os.environ.get("DEEPSEEKASS_DATA_DIR")
        os.environ["DEEPSEEKASS_DATA_DIR"] = str(data_root)
        auth_module.USERS_DIR = str(users_dir)
        auth_module.USERS_DB = str(users_dir / "users.json")
        try:
            username, password, title = "smoke_user", "Smoke-Only-Password-2026", "真实控制冒烟书"
            enc_key = AuthManager.register(username, password)
            manager = NovelManager(
                str(Path(AuthManager.get_user_dir(username)) / "bookshelf"),
                crypto=AuthManager,
                enc_key=enc_key,
            )
            manager.create_book(title)
            manager.save_chapter_version(title, 1, "雪夜来信", "雪落在旧城，林舟收到一封没有署名的信。", version=1)
            manager.save_chapter_version(
                title,
                1,
                "平行雪夜",
                "另一条时间线上，信封里只有一枚蓝色羽毛。",
                version=2,
                parent_id="ch0000_v000",
            )
            manager.switch_active_node(title, "ch0001_v001")
            manager.save_world_bible(
                title,
                dict_to_world_bible(
                    {
                        "characters": [{"id": "char-linzhou", "name": "林舟", "traits": "谨慎"}],
                        "locations": [{"id": "loc-oldcity", "name": "旧城", "description": "常年落雪的边城"}],
                    }
                ),
                force=True,
            )
            settings_manager = SettingsManager(AuthManager.get_user_dir(username), AuthManager, enc_key)
            settings = settings_manager.load()
            settings["control_agent_enabled"] = True
            settings_manager.save(settings)
            token, grant = ControlGrantStore.create(
                username,
                password,
                name="packaged-smoke-agent",
                scopes={"read", "propose", "generate"},
                expires_days=1,
            )

            books = _run_json(executable, username, token, ["books", "list"])
            _require(books["books"][0]["title"] == title, "packaged CLI could not list the encrypted book")
            tree = _run_json(executable, username, token, ["tree", "show", "--book", title, "--limit", "1"])
            _require(tree["page"]["next_offset"] is not None, "chapter tree pagination was not exercised")
            search = _run_json(
                executable,
                username,
                token,
                ["chapter", "search", "--book", title, "--query", "蓝色羽毛", "--scope", "all"],
            )
            _require(search["results"][0]["node_id"] == "ch0001_v002", "all-branch search missed the parallel version")
            world = _run_json(executable, username, token, ["world", "search", "--book", title, "--query", "谨慎"])
            _require(world["results"][0]["id"] == "char-linzhou", "world bible search failed")

            active_before = list(manager.ensure_chapter_tree(title).active_path)
            nodes_before = len(manager.ensure_chapter_tree(title).chapter_nodes)
            proposed = _run_json(
                executable,
                username,
                token,
                [
                    "chapter",
                    "propose-revision",
                    "--book",
                    title,
                    "--base-node-id",
                    "ch0001_v001",
                    "--title",
                    "雪夜来信修订",
                    "--reason",
                    "真实冒烟测试",
                    "--input",
                    "-",
                ],
                stdin="雪仍在下，林舟拆开信封，发现落款来自十年后的自己。",
            )
            change_id = proposed["change_set_id"]
            _require(proposed["applied"] is False, "proposal modified formal data before approval")
            _require(len(manager.ensure_chapter_tree(title).chapter_nodes) == nodes_before, "proposal changed the chapter tree")

            noninteractive_approval = subprocess.run(
                [
                    str(executable),
                    "--username",
                    username,
                    "changes",
                    "approve",
                    "--book",
                    title,
                    change_id,
                ],
                input=f"{password}\n{change_id}\n",
                text=True,
                encoding="utf-8",
                capture_output=True,
                check=False,
                timeout=30,
            )
            _require(noninteractive_approval.returncode == 3, "packaged CLI accepted a piped approval password")
            rejected_payload = json.loads(noninteractive_approval.stdout)
            _require(rejected_payload["error"]["code"] == "permission_denied", "wrong noninteractive approval error")
            approve_change_with_password(username, password, title, change_id)
            meta_after = manager.ensure_chapter_tree(title)
            _require(len(meta_after.chapter_nodes) == nodes_before + 1, "approval did not create exactly one revision")
            _require(meta_after.active_path == active_before, "approved revision unexpectedly changed the active path")

            agent_nodes_before = len(meta_after.chapter_nodes)
            with _mock_tool_model() as (base_url, model_calls):
                AuthManager.encrypt_json(enc_key, str(Path(AuthManager.get_user_dir(username)) / "config.enc"), {
                    "text": {"api_key": "smoke-key", "base_url": base_url, "model": "smoke-tool-model"},
                    "image": {},
                })
                agent_result = _run_json(
                    executable,
                    username,
                    token,
                    [
                        "agent", "run", "--book", title,
                        "--chapter-title", "钟楼的回声",
                        "--target-words", "1200", "--input", "-",
                    ],
                    stdin="读取现有上下文，续写下一章并提交待审批变更。",
                )
            _require(len(model_calls) == 1, "writing Agent did not call the configured model exactly once")
            _require(agent_result["status"] == "waiting_approval", "writing Agent did not stop for approval")
            _require(len(agent_result["change_set_ids"]) == 1, "writing Agent did not create a chapter proposal")
            _require(agent_result["requires_human_approval"] is True, "writing Agent bypassed approval")
            _require(len(manager.ensure_chapter_tree(title).chapter_nodes) == agent_nodes_before, "writing Agent changed formal chapters")

            mcp_result = asyncio.run(_exercise_mcp(executable, username, token, title))
            audit_dir = Path(AuthManager.get_user_dir(username)) / ".deepseekass" / "control_audit"
            audit_files = list(audit_dir.glob("*.json.enc"))
            _require(len(audit_files) >= 6, "encrypted control audit records were not written")
            raw_audit = b"".join(path.read_bytes() for path in audit_files)
            _require(token.encode("utf-8") not in raw_audit, "raw token leaked into audit storage")

            return {
                "ok": True,
                "packaged_executable": str(dist_dir / "DeepseekAssControl.exe"),
                "encrypted_book_read": True,
                "tree_pagination": True,
                "all_branch_search": True,
                "world_bible_search": True,
                "proposal_left_data_unchanged": True,
                "noninteractive_approval_blocked": True,
                "approval_created_inactive_revision": True,
                "writing_agent_created_pending_change": True,
                "writing_agent_left_formal_data_unchanged": True,
                "encrypted_audit_records": len(audit_files),
                "grant_id": grant["grant_id"],
                "mcp": mcp_result,
                "temporary_data_cleaned": True,
            }
        finally:
            auth_module.USERS_DIR, auth_module.USERS_DB = old_users_dir, old_users_db
            if old_data_root is None:
                os.environ.pop("DEEPSEEKASS_DATA_DIR", None)
            else:
                os.environ["DEEPSEEKASS_DATA_DIR"] = old_data_root


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the packaged control interface smoke test")
    parser.add_argument("--dist", type=Path, default=DEFAULT_DIST)
    args = parser.parse_args()
    try:
        result = run(args.dist.resolve())
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

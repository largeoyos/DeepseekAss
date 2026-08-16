"""DeepseekAss machine-readable CLI and local MCP stdio entry point."""
from __future__ import annotations

import argparse
import getpass
import json
import os
import sys

from core.control_auth import ControlAuthError, ControlGrant, ControlGrantStore
from core.control_service import (
    ControlConflictError,
    ControlError,
    ControlNotFoundError,
    ControlPermissionError,
    ControlService,
    ControlValidationError,
    approve_change_with_password,
    reject_change_with_password,
)


class CliUsageError(ValueError):
    pass


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise CliUsageError(message)


def _write_json(payload: dict, pretty: bool = False, *, stream=None) -> None:
    target = stream or sys.stdout
    text = json.dumps(payload, ensure_ascii=False, indent=2 if pretty else None, separators=None if pretty else (",", ":"), default=str)
    target.write(text + "\n")
    target.flush()


def _success(data: dict) -> dict:
    return {"ok": True, "schema_version": 1, "data": data}


def _error(code: str, message: str) -> dict:
    return {"ok": False, "schema_version": 1, "error": {"code": code, "message": str(message)}}


def _password(prompt: str = "DeepseekAss 密码: ") -> str:
    if not sys.stdin.isatty():
        raise ControlPermissionError("该操作需要交互终端输入密码")
    return getpass.getpass(prompt)


def _read_input(path: str) -> str:
    if path == "-":
        return sys.stdin.read()
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def _service(args) -> ControlService:
    username = str(getattr(args, "username", "") or "").strip()
    if not username:
        raise ControlValidationError("必须通过 --username 指定账号")
    env_name = str(getattr(args, "token_env", "") or "DEEPSEEKASS_CONTROL_TOKEN")
    token = os.environ.get(env_name, "")
    if not token:
        raise ControlPermissionError(f"环境变量 {env_name} 中没有自动化令牌")
    return ControlService(ControlGrantStore.resolve(username, token))


def _human_service(username: str, password: str) -> ControlService:
    from core.auth_manager import AuthManager
    ok, enc_key = AuthManager.authenticate(username, password)
    if not ok or enc_key is None:
        raise ControlPermissionError("用户名或密码错误")
    return ControlService(ControlGrant("human", username, "human", ("read", "propose"), "", "", enc_key))


def _tree_text(data: dict) -> str:
    nodes = list(data.get("nodes") or [])
    by_parent: dict[str, list[dict]] = {}
    ids = {str(item.get("id") or "") for item in nodes}
    for node in nodes:
        parent = str(node.get("parent_id") or "")
        by_parent.setdefault(parent, []).append(node)
    roots = [item for item in nodes if str(item.get("parent_id") or "") not in ids]
    active = set(data.get("active_path") or [])
    lines = []

    def walk(node: dict, depth: int) -> None:
        node_id = str(node.get("id") or "")
        marker = "*" if node_id in active else "-"
        label = node.get("display_label") or node.get("title") or node_id
        lines.append(f"{'  ' * depth}{marker} {label} [{node_id}]")
        for child in by_parent.get(node_id, []):
            walk(child, depth + 1)

    for root in roots:
        walk(root, 0)
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = JsonArgumentParser(description="DeepseekAss local AI control interface")
    parser.add_argument("--username", default=os.environ.get("DEEPSEEKASS_USERNAME", ""))
    parser.add_argument("--token-env", default="DEEPSEEKASS_CONTROL_TOKEN")
    parser.add_argument("--pretty", action="store_true")
    commands = parser.add_subparsers(dest="command", required=True, parser_class=JsonArgumentParser)

    auth = commands.add_parser("auth")
    auth_sub = auth.add_subparsers(dest="auth_command", required=True, parser_class=JsonArgumentParser)
    grant = auth_sub.add_parser("grant")
    grant.add_argument("--name", default="AI control")
    grant.add_argument("--expires-days", type=int, default=90)
    grant.add_argument("--read-only", action="store_true")
    auth_sub.add_parser("list")
    revoke = auth_sub.add_parser("revoke")
    revoke.add_argument("grant_id")

    books = commands.add_parser("books")
    books.add_subparsers(dest="books_command", required=True, parser_class=JsonArgumentParser).add_parser("list")

    project = commands.add_parser("project")
    project_show = project.add_subparsers(dest="project_command", required=True, parser_class=JsonArgumentParser).add_parser("show")
    project_show.add_argument("--book", required=True)

    tree = commands.add_parser("tree")
    tree_show = tree.add_subparsers(dest="tree_command", required=True, parser_class=JsonArgumentParser).add_parser("show")
    tree_show.add_argument("--book", required=True)
    tree_show.add_argument("--tree-id", default="")
    tree_show.add_argument("--offset", type=int, default=0)
    tree_show.add_argument("--limit", type=int, default=500)
    tree_show.add_argument("--include-summaries", action="store_true")
    tree_show.add_argument("--output", choices=("json", "tree"), default="json")

    chapter = commands.add_parser("chapter")
    chapter_sub = chapter.add_subparsers(dest="chapter_command", required=True, parser_class=JsonArgumentParser)
    chapter_read = chapter_sub.add_parser("read")
    chapter_read.add_argument("--book", required=True); chapter_read.add_argument("--node-id", required=True)
    chapter_read.add_argument("--start", type=int, default=0); chapter_read.add_argument("--max-chars", type=int, default=20000)
    chapter_search = chapter_sub.add_parser("search")
    chapter_search.add_argument("--book", required=True); chapter_search.add_argument("--query", required=True)
    chapter_search.add_argument("--scope", choices=("active", "all"), default="all"); chapter_search.add_argument("--limit", type=int, default=20)
    chapter_propose = chapter_sub.add_parser("propose-revision")
    chapter_propose.add_argument("--book", required=True); chapter_propose.add_argument("--base-node-id", required=True)
    chapter_propose.add_argument("--title", default=""); chapter_propose.add_argument("--input", required=True); chapter_propose.add_argument("--reason", default="")

    world = commands.add_parser("world")
    world_sub = world.add_subparsers(dest="world_command", required=True, parser_class=JsonArgumentParser)
    world_show = world_sub.add_parser("show")
    world_show.add_argument("--book", required=True); world_show.add_argument("--category", default="")
    world_show.add_argument("--offset", type=int, default=0); world_show.add_argument("--limit", type=int, default=100)
    world_search = world_sub.add_parser("search")
    world_search.add_argument("--book", required=True); world_search.add_argument("--query", required=True)
    world_search.add_argument("--entity-type", default=""); world_search.add_argument("--limit", type=int, default=20)
    world_audit = world_sub.add_parser("audit"); world_audit.add_argument("--book", required=True)
    world_patch = world_sub.add_parser("propose-patch")
    world_patch.add_argument("--book", required=True); world_patch.add_argument("--input", required=True); world_patch.add_argument("--reason", default="")

    changes = commands.add_parser("changes")
    changes_sub = changes.add_subparsers(dest="changes_command", required=True, parser_class=JsonArgumentParser)
    changes_list = changes_sub.add_parser("list"); changes_list.add_argument("--book", required=True)
    changes_show = changes_sub.add_parser("show"); changes_show.add_argument("--book", required=True); changes_show.add_argument("change_set_id")
    changes_approve = changes_sub.add_parser("approve"); changes_approve.add_argument("--book", required=True); changes_approve.add_argument("change_set_id")
    changes_reject = changes_sub.add_parser("reject"); changes_reject.add_argument("--book", required=True); changes_reject.add_argument("change_set_id")

    commands.add_parser("mcp")
    return parser


def execute(args) -> dict | None:
    username = str(args.username or "").strip()
    if args.command == "auth":
        if not username:
            raise ControlValidationError("必须通过 --username 指定账号")
        password = _password()
        if args.auth_command == "grant":
            scopes = {"read"} if args.read_only else {"read", "propose"}
            token, grant = ControlGrantStore.create(username, password, name=args.name, scopes=scopes, expires_days=args.expires_days)
            return {"grant": grant, "token": token, "token_shown_once": True}
        if args.auth_command == "list":
            return {"grants": ControlGrantStore.list(username, password)}
        return {"revoked": ControlGrantStore.revoke(username, password, args.grant_id), "grant_id": args.grant_id}

    if args.command == "changes" and args.changes_command in {"approve", "reject"}:
        if not username:
            raise ControlValidationError("必须通过 --username 指定账号")
        password = _password()
        preview = _human_service(username, password).get_change_set(args.book, args.change_set_id)
        _write_json({"approval_preview": preview}, pretty=True, stream=sys.stderr)
        confirmation = input(f"输入变更 ID {args.change_set_id} 以确认: ").strip()
        if confirmation != args.change_set_id:
            raise ControlValidationError("确认已取消")
        if args.changes_command == "approve":
            return approve_change_with_password(username, password, args.book, args.change_set_id)
        return reject_change_with_password(username, password, args.book, args.change_set_id)

    service = _service(args)
    if args.command == "mcp":
        from core.control_mcp import run_mcp_stdio
        run_mcp_stdio(service)
        return None
    if args.command == "books":
        return service.invoke("list_books")
    if args.command == "project":
        return service.invoke("get_project", {"book": args.book})
    if args.command == "tree":
        data = service.invoke("get_chapter_tree", {"book": args.book, "tree_id": args.tree_id, "offset": args.offset, "limit": args.limit, "include_summaries": args.include_summaries})
        if args.output == "tree":
            sys.stdout.write(_tree_text(data) + "\n")
            return None
        return data
    if args.command == "chapter":
        if args.chapter_command == "read":
            return service.invoke("read_chapter", {"book": args.book, "node_id": args.node_id, "start": args.start, "max_chars": args.max_chars})
        if args.chapter_command == "search":
            return service.invoke("search_chapters", {"book": args.book, "query": args.query, "scope": args.scope, "limit": args.limit})
        return service.invoke("propose_chapter_revision", {"book": args.book, "base_node_id": args.base_node_id, "title": args.title, "content": _read_input(args.input), "reason": args.reason})
    if args.command == "world":
        if args.world_command == "show":
            return service.invoke("get_world_bible", {"book": args.book, "category": args.category, "offset": args.offset, "limit": args.limit})
        if args.world_command == "search":
            return service.invoke("search_world_bible", {"book": args.book, "query": args.query, "entity_type": args.entity_type, "limit": args.limit})
        if args.world_command == "audit":
            return service.invoke("audit_world_bible", {"book": args.book})
        patch = json.loads(_read_input(args.input))
        operations = patch if isinstance(patch, list) else patch.get("operations")
        reason = args.reason or (patch.get("reason", "") if isinstance(patch, dict) else "")
        return service.invoke("propose_world_bible_patch", {"book": args.book, "operations": operations, "reason": reason})
    if args.command == "changes":
        if args.changes_command == "list":
            return service.invoke("list_pending_changes", {"book": args.book})
        if args.changes_command == "show":
            return service.invoke("get_change_set", {"book": args.book, "change_set_id": args.change_set_id})
        raise CliUsageError("不支持的 changes 子命令")
    raise CliUsageError("缺少命令")


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = build_parser()
    pretty = False
    try:
        args = parser.parse_args(argv)
        pretty = bool(args.pretty)
        result = execute(args)
        if result is not None:
            _write_json(_success(result), pretty)
        return 0
    except (CliUsageError, argparse.ArgumentError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        _write_json(_error("invalid_arguments", str(exc)), pretty)
        return 2
    except (ControlAuthError, ControlPermissionError) as exc:
        _write_json(_error("permission_denied", str(exc)), pretty)
        return 3
    except (ControlNotFoundError, ControlConflictError) as exc:
        _write_json(_error(getattr(exc, "code", "conflict"), str(exc)), pretty)
        return 4
    except (ControlValidationError, ControlError, OSError, ValueError) as exc:
        _write_json(_error(getattr(exc, "code", "validation_error"), str(exc)), pretty)
        return 2
    except Exception as exc:
        _write_json(_error("internal_error", str(exc)), pretty)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

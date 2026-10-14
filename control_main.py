"""DeepseekAss machine-readable CLI and local MCP stdio entry point."""
from __future__ import annotations

import argparse
import getpass
import json
import os
import sys

from core.control_auth import ControlAuthError, ControlGrant, ControlGrantStore
from core.auth_manager import AuthError, AuthManager
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


def _auth_scopes(args):
    if args.read_only and (args.allow_agent or args.allow_manage or args.allow_approve):
        raise ControlValidationError("--read-only 不能与写入、生成或审批授权同时使用")
    scopes = {"read"} if args.read_only else {"read", "propose"}
    for enabled, scope in ((args.allow_agent, "generate"), (args.allow_manage, "manage"), (args.allow_approve, "approve")):
        if enabled:
            scopes.add(scope)
    return scopes


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
    commands.add_parser("status", help="查看自动化权限和默认值")

    auth = commands.add_parser("auth")
    auth_sub = auth.add_subparsers(dest="auth_command", required=True, parser_class=JsonArgumentParser)
    grant = auth_sub.add_parser("grant")
    grant.add_argument("--name", default="AI control")
    grant.add_argument("--password-env", default="", help="从指定环境变量读取账号密码")
    grant.add_argument("--expires-days", type=int, default=90)
    grant.add_argument("--read-only", action="store_true")
    grant.add_argument("--allow-agent", action="store_true", help="allow invoking the configured writing Agent")
    grant.add_argument("--allow-manage", action="store_true", help="allow project settings and branch changes")
    grant.add_argument("--allow-approve", action="store_true", help="allow noninteractive changes apply")
    register = auth_sub.add_parser("register", help="注册独立账号并签发令牌")
    register.add_argument("--password-env", default="", help="从指定环境变量读取新账号密码，省略则交互输入")
    register.add_argument("--name", default="AI control")
    register.add_argument("--expires-days", type=int, default=1)
    register.add_argument("--read-only", action="store_true")
    register.add_argument("--allow-agent", action="store_true")
    register.add_argument("--allow-manage", action="store_true")
    register.add_argument("--allow-approve", action="store_true")
    auth_list = auth_sub.add_parser("list")
    auth_list.add_argument("--password-env", default="")
    revoke = auth_sub.add_parser("revoke")
    revoke.add_argument("grant_id")
    revoke.add_argument("--password-env", default="")

    books = commands.add_parser("books")
    books.add_subparsers(dest="books_command", required=True, parser_class=JsonArgumentParser).add_parser("list")

    project = commands.add_parser("project")
    project_sub = project.add_subparsers(dest="project_command", required=True, parser_class=JsonArgumentParser)
    project_show = project_sub.add_parser("show")
    project_show.add_argument("--book", required=True)
    project_create = project_sub.add_parser("create")
    project_create.add_argument("--title", required=True)
    project_create.add_argument("--input", help="UTF-8 JSON 设定文件，或 -")
    project_set = project_sub.add_parser("set")
    project_set.add_argument("--book", required=True)
    project_set.add_argument("--input", required=True)

    tree = commands.add_parser("tree")
    tree_sub = tree.add_subparsers(dest="tree_command", required=True, parser_class=JsonArgumentParser)
    tree_show = tree_sub.add_parser("show")
    tree_show.add_argument("--book", required=True)
    tree_show.add_argument("--tree-id", default="")
    tree_show.add_argument("--offset", type=int, default=0)
    tree_show.add_argument("--limit", type=int, default=None)
    tree_show.add_argument("--include-summaries", action=argparse.BooleanOptionalAction, default=None)
    tree_show.add_argument("--output", choices=("json", "tree"), default="json")
    activate = tree_sub.add_parser("activate")
    activate.add_argument("--book", required=True)
    branch = activate.add_mutually_exclusive_group(required=True)
    branch.add_argument("--node-id", default="")
    branch.add_argument("--tree-id", default="")

    chapter = commands.add_parser("chapter")
    chapter_sub = chapter.add_subparsers(dest="chapter_command", required=True, parser_class=JsonArgumentParser)
    chapter_read = chapter_sub.add_parser("read")
    chapter_read.add_argument("--book", required=True); chapter_read.add_argument("--node-id", required=True)
    chapter_read.add_argument("--start", type=int, default=0); chapter_read.add_argument("--max-chars", type=int, default=None)
    chapter_current = chapter_sub.add_parser("current", help="读取当前活跃路径最后一章")
    chapter_current.add_argument("--book", required=True)
    chapter_current.add_argument("--start", type=int, default=0)
    chapter_current.add_argument("--max-chars", type=int, default=None)
    chapter_search = chapter_sub.add_parser("search")
    chapter_search.add_argument("--book", required=True); chapter_search.add_argument("--query", required=True)
    chapter_search.add_argument("--scope", choices=("active", "all"), default=""); chapter_search.add_argument("--limit", type=int, default=None)
    chapter_propose = chapter_sub.add_parser("propose-revision")
    chapter_propose.add_argument("--book", required=True); chapter_propose.add_argument("--base-node-id", required=True)
    chapter_propose.add_argument("--title", default=""); chapter_propose.add_argument("--input", required=True); chapter_propose.add_argument("--reason", default="")
    new_chapter = chapter_sub.add_parser("propose", help="将已有完整正文注入新章提案，不调用模型")
    new_chapter.add_argument("--book", required=True)
    new_chapter.add_argument("--chapter-num", required=True, type=int)
    new_chapter.add_argument("--title", required=True)
    new_chapter.add_argument("--parent-id", default="")
    new_chapter.add_argument("--input", required=True)
    new_chapter.add_argument("--reason", default="")
    maintain = chapter_sub.add_parser("maintain")
    maintain.add_argument("--book", required=True)
    maintain.add_argument("--node-id", required=True)
    maintain.add_argument("--force", action="store_true", help="重新生成摘要并强制提取世界书；默认复用已完成步骤")

    world = commands.add_parser("world")
    world_sub = world.add_subparsers(dest="world_command", required=True, parser_class=JsonArgumentParser)
    world_show = world_sub.add_parser("show")
    world_show.add_argument("--book", required=True); world_show.add_argument("--category", default="")
    world_show.add_argument("--offset", type=int, default=0); world_show.add_argument("--limit", type=int, default=None)
    world_search = world_sub.add_parser("search")
    world_search.add_argument("--book", required=True); world_search.add_argument("--query", required=True)
    world_search.add_argument("--entity-type", default=""); world_search.add_argument("--limit", type=int, default=None)
    world_audit = world_sub.add_parser("audit"); world_audit.add_argument("--book", required=True)
    world_rebuild = world_sub.add_parser("rebuild"); world_rebuild.add_argument("--book", required=True)
    world_read = world_sub.add_parser("read", help="按实体 ID 读取完整世界书条目")
    world_read.add_argument("--book", required=True)
    world_read.add_argument("--entity-id", action="append", required=True)
    world_patch = world_sub.add_parser("propose-patch")
    world_patch.add_argument("--book", required=True); world_patch.add_argument("--input", required=True); world_patch.add_argument("--reason", default="")

    changes = commands.add_parser("changes")
    changes_sub = changes.add_subparsers(dest="changes_command", required=True, parser_class=JsonArgumentParser)
    changes_list = changes_sub.add_parser("list"); changes_list.add_argument("--book", required=True)
    changes_show = changes_sub.add_parser("show"); changes_show.add_argument("--book", required=True); changes_show.add_argument("change_set_id")
    changes_show.add_argument("--include-content", action="store_true", help="包含完整提案正文以便审批前检查")
    changes_approve = changes_sub.add_parser("approve"); changes_approve.add_argument("--book", required=True); changes_approve.add_argument("change_set_id")
    changes_reject = changes_sub.add_parser("reject"); changes_reject.add_argument("--book", required=True); changes_reject.add_argument("change_set_id")
    changes_apply = changes_sub.add_parser("apply", help="需要显式 approve 权限，应用指定提案")
    changes_apply.add_argument("--book", required=True)
    changes_apply.add_argument("change_set_id")
    changes_apply.add_argument("--operation-id", action="append", default=None)
    discard = changes_sub.add_parser("discard", help="使用显式 approve 权限拒绝提案")
    discard.add_argument("--book", required=True)
    discard.add_argument("change_set_id")

    agent = commands.add_parser("agent")
    agent_sub = agent.add_subparsers(dest="agent_command", required=True, parser_class=JsonArgumentParser)
    agent_run = agent_sub.add_parser("run")
    agent_run.add_argument("--book", required=True)
    agent_run.add_argument("--input", required=True, help="instruction file, or - for stdin")
    agent_run.add_argument("--chapter-title", default="")
    agent_run.add_argument("--target-words", type=int, default=None)
    agent_run.add_argument("--reference", action="append", default=[])
    plan = agent_sub.add_parser("plan")
    plan.add_argument("--book", required=True)
    plan_input = plan.add_mutually_exclusive_group(required=True)
    plan_input.add_argument("--instruction")
    plan_input.add_argument("--input")
    plan.add_argument("--chapter-title", default="")
    plan.add_argument("--target-words", type=int, default=1600)
    plan_show = agent_sub.add_parser("plan-show")
    plan_show.add_argument("--book", required=True)
    plan_show.add_argument("--plan-id", required=True)
    run_show = agent_sub.add_parser("run-show")
    run_show.add_argument("--book", required=True)
    run_show.add_argument("--run-id", required=True)
    runs = agent_sub.add_parser("runs")
    runs.add_argument("--book", required=True)
    runs.add_argument("--limit", type=int, default=20)

    models = commands.add_parser("models")
    model_sub = models.add_subparsers(dest="model_command", required=True, parser_class=JsonArgumentParser)
    model_sub.add_parser("status")
    model_config = model_sub.add_parser("configure")
    model_config.add_argument("--input", required=True, help="完整模型配置 JSON 文件或 -，不会回显密钥")
    control = commands.add_parser("control")
    generation = control.add_subparsers(dest="control_command", required=True, parser_class=JsonArgumentParser).add_parser("generation")
    generation.add_argument("--enabled", action=argparse.BooleanOptionalAction, required=True)

    # 面向外部 AI 的短命令入口。保留 agent run 作为兼容入口，
    # 但把“写作”作为顶层概念暴露出来，避免调用方必须了解内部 Agent 名称。
    writing = commands.add_parser("write", help="写作模式：注入写作要求并生成待审批章节提案")
    writing_sub = writing.add_subparsers(dest="write_command", required=True, parser_class=JsonArgumentParser)
    writing_chapter = writing_sub.add_parser("chapter", help="让内置写作总管读取项目上下文并提出新章")
    writing_chapter.add_argument("--book", required=True)
    writing_input = writing_chapter.add_mutually_exclusive_group(required=True)
    writing_input.add_argument("--instruction", help="直接注入写作信息")
    writing_input.add_argument("--input", help="写作信息文件，使用 - 表示标准输入")
    writing_chapter.add_argument("--chapter-title", default="")
    writing_chapter.add_argument("--target-words", type=int, default=None)
    writing_chapter.add_argument("--reference", action="append", default=[])
    writing_chapter.add_argument("--image", action="append", default=[], help="本地图片，可重复，先识别为写作参考")
    image = commands.add_parser("image", help="图片识别，不生成图片")
    image_read = image.add_subparsers(dest="image_command", required=True, parser_class=JsonArgumentParser).add_parser("describe")
    image_read.add_argument("--image", action="append", required=True)
    image_read.add_argument("--instruction", default="")
    image_read.add_argument("--book", default="")

    commands.add_parser("mcp")
    return parser


def execute(args) -> dict | None:
    username = str(args.username or "").strip()
    if args.command == "auth":
        if not username:
            raise ControlValidationError("必须通过 --username 指定账号")
        if args.auth_command == "register":
            scopes = _auth_scopes(args)
            if not 1 <= args.expires_days <= 3650:
                raise ControlValidationError("授权有效期必须在 1 到 3650 天之间")
            password = os.environ.get(args.password_env, "") if args.password_env else _password("新账号密码: ")
            if not password:
                raise ControlValidationError("新账号密码不能为空")
            AuthManager.register(username, password)
            token, grant = ControlGrantStore.create(username, password, name=args.name, scopes=scopes, expires_days=args.expires_days)
            return {"username": username, "grant": grant, "token": token, "token_shown_once": True}
        password_env = getattr(args, "password_env", "")
        password = os.environ.get(password_env, "") if password_env else _password()
        if not password:
            raise ControlValidationError("账号密码不能为空")
        if args.auth_command == "grant":
            scopes = _auth_scopes(args)
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
    if args.command == "models":
        return service.invoke("model_status") if args.model_command == "status" else service.invoke("configure_models", {"config": json.loads(_read_input(args.input))})
    if args.command == "control":
        return service.invoke("configure_generation", {"enabled": args.enabled})
    if args.command == "status":
        return service.invoke("get_control_settings")
    if args.command == "image":
        return service.invoke("describe_images", {"paths": args.image, "instruction": args.instruction, "book": args.book})
    if args.command == "mcp":
        from core.control_mcp import run_mcp_stdio
        run_mcp_stdio(service)
        return None
    if args.command == "books":
        return service.invoke("list_books")
    if args.command == "agent":
        if args.agent_command == "plan":
            return service.invoke("plan_chapter", {"book": args.book, "instruction": args.instruction if args.instruction is not None else _read_input(args.input), "chapter_title": args.chapter_title, "target_words": args.target_words})
        if args.agent_command == "plan-show":
            return service.invoke("read_plan", {"book": args.book, "plan_id": args.plan_id})
        if args.agent_command == "run-show":
            return service.invoke("read_agent_run", {"book": args.book, "run_id": args.run_id})
        if args.agent_command == "runs":
            return service.invoke("list_agent_runs", {"book": args.book, "limit": args.limit})
        return service.invoke("run_writing_agent", {
            "book": args.book,
            "instruction": _read_input(args.input),
            "chapter_title": args.chapter_title,
            "target_words": args.target_words,
            "manual_references": args.reference,
        })
    if args.command == "write":
        if args.write_command != "chapter":
            raise CliUsageError("不支持的 write 子命令")
        instruction = args.instruction if args.instruction is not None else _read_input(args.input)
        if not instruction.strip() or len(instruction) > 30000:
            raise ControlValidationError("写作信息不能为空且不得超过 30000 字符")
        if args.image:
            service._require("propose")
            reference = service.invoke("describe_images", {"paths": args.image, "instruction": instruction, "book": args.book})
            instruction += "\n\n【图片参考（非已发生剧情）】\n" + reference["content"]
        return service.invoke("run_writing_agent", {
            "book": args.book,
            "instruction": instruction,
            "chapter_title": args.chapter_title,
            "target_words": args.target_words,
            "manual_references": args.reference,
        })
    if args.command == "project":
        if args.project_command == "create":
            return service.invoke("create_project", {"title": args.title, "fields": json.loads(_read_input(args.input)) if args.input else None})
        if args.project_command == "set":
            return service.invoke("update_project", {"book": args.book, "fields": json.loads(_read_input(args.input))})
        return service.invoke("get_project", {"book": args.book})
    if args.command == "tree":
        if args.tree_command == "activate":
            return service.invoke("activate_branch", {"book": args.book, "node_id": args.node_id, "tree_id": args.tree_id})
        data = service.invoke("get_chapter_tree", {"book": args.book, "tree_id": args.tree_id, "offset": args.offset, "limit": args.limit, "include_summaries": args.include_summaries})
        if args.output == "tree":
            sys.stdout.write(_tree_text(data) + "\n")
            return None
        return data
    if args.command == "chapter":
        if args.chapter_command == "propose":
            return service.invoke("propose_new_chapter", {"book": args.book, "chapter_num": args.chapter_num, "chapter_title": args.title, "content": _read_input(args.input), "parent_id": args.parent_id, "reason": args.reason})
        if args.chapter_command == "maintain":
            return service.invoke("maintain_chapter", {"book": args.book, "node_id": args.node_id, "force": args.force})
        if args.chapter_command == "current":
            return service.invoke("read_current_chapter", {"book": args.book, "start": args.start, "max_chars": args.max_chars})
        if args.chapter_command == "read":
            return service.invoke("read_chapter", {"book": args.book, "node_id": args.node_id, "start": args.start, "max_chars": args.max_chars})
        if args.chapter_command == "search":
            return service.invoke("search_chapters", {"book": args.book, "query": args.query, "scope": args.scope, "limit": args.limit})
        return service.invoke("propose_chapter_revision", {"book": args.book, "base_node_id": args.base_node_id, "title": args.title, "content": _read_input(args.input), "reason": args.reason})
    if args.command == "world":
        if args.world_command == "rebuild":
            return service.invoke("rebuild_world", {"book": args.book})
        if args.world_command == "read":
            return service.invoke("read_world_entities", {"book": args.book, "entity_ids": args.entity_id})
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
        if args.changes_command == "discard":
            return service.invoke("discard_change", {"book": args.book, "change_set_id": args.change_set_id})
        if args.changes_command == "apply":
            return service.invoke("apply_change", {"book": args.book, "change_set_id": args.change_set_id, "operation_ids": args.operation_id})
        if args.changes_command == "list":
            return service.invoke("list_pending_changes", {"book": args.book})
        if args.changes_command == "show":
            return service.invoke("get_change_set", {"book": args.book, "change_set_id": args.change_set_id, "include_content": args.include_content})
        raise CliUsageError("不支持的 changes 子命令")
    raise CliUsageError("缺少命令")


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdin, "reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
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
    except (ControlValidationError, ControlError, AuthError, OSError, ValueError) as exc:
        payload = _error(getattr(exc, "code", "validation_error"), str(exc))
        if isinstance(getattr(exc, "details", None), dict):
            payload["error"]["details"] = exc.details
        _write_json(payload, pretty)
        return 2
    except Exception as exc:
        _write_json(_error("internal_error", str(exc)), pretty)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

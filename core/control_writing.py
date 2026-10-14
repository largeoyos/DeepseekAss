"""Writing management operations shared by authenticated CLI clients."""
import os
import re
from dataclasses import asdict


class WritingControlMixin:
    PROJECT_FIELDS = {"author", "protagonist_bio", "background_story", "writing_demand", "author_plan", "genre", "style_tone"}

    def _project_fields(self, fields):
        from core.control_service import ControlValidationError
        if not isinstance(fields, dict) or not fields or set(fields) - self.PROJECT_FIELDS:
            raise ControlValidationError("设定必须是非空 JSON 对象，仅允许：" + ", ".join(sorted(self.PROJECT_FIELDS)))
        if any(not isinstance(value, str) or len(value) > 100000 for value in fields.values()):
            raise ControlValidationError("每项设定必须是最多 100000 字符的字符串")
        return fields

    def create_project(self, title: str, fields: dict | None = None):
        from core.control_service import ControlConflictError
        self._require("manage")
        self._require("read")
        title = str(title or "").strip()
        values = self._project_fields(fields) if fields is not None else {}
        self.manager._validate_book_title(title)
        if title in self.manager.list_books():
            raise ControlConflictError("同名书籍已存在，拒绝覆盖")
        self.manager.create_book(title)
        if values:
            self.manager.save_meta(title, **values)
        return self.get_project(title)

    def update_project(self, book: str, fields: dict):
        self._require("manage")
        self._require("read")
        values = self._project_fields(fields)
        title, _meta = self._book(book)
        self.manager.snapshot_service(title).create("CLI 更新项目设定前", source="rollback_backup")
        self.manager.save_meta(title, **values)
        return self.get_project(book)

    def activate_branch(self, book: str, node_id: str = "", tree_id: str = ""):
        from core.app_services import ChapterBranchService
        from core.control_service import ControlValidationError, ControlNotFoundError
        self._require("manage")
        self._require("read")
        if bool(node_id) == bool(tree_id):
            raise ControlValidationError("node_id 和 tree_id 必须且只能指定一个")
        title, _meta = self._book(book)
        meta = self.manager.ensure_chapter_tree(title)
        if node_id and node_id not in meta.chapter_nodes:
            raise ControlNotFoundError("章节节点不存在")
        if tree_id and tree_id not in {item["tree_id"] for item in self.manager.list_chapter_trees(title)}:
            raise ControlNotFoundError("章节树不存在")
        snapshot = self.manager.snapshot_service(title).create("CLI 调整章节树前", source="rollback_backup")
        service = ChapterBranchService(self.manager)
        report = service.activate_node(title, node_id) if node_id else service.activate_tree(title, tree_id)
        if report is None:
            raise ControlNotFoundError("无法激活指定章节或章节树")
        return {**self.get_chapter_tree(book), "synchronization": report, "snapshot_id": snapshot.snapshot_id}

    def _require_generation(self):
        from core.control_service import ControlPermissionError
        self._require("generate")
        self._require("read")
        if not self.settings.get("control_agent_enabled", False):
            raise ControlPermissionError("AI 控制接口的生成开关尚未启用")

    def _writing_gateway(self):
        from core.auth_manager import AuthManager
        from core.model_config import ModelConfig
        from core.model_gateway import ModelGateway
        raw = AuthManager.decrypt_json(self.grant.enc_key, os.path.join(self.user_dir, "config.enc")) or {}
        config = ModelConfig.from_dict(raw, self.settings)

        def routes(title):
            data = self.manager.get_workspace(title).storage.read_json(".deepseekass/model_routes.json", default={}) or {}
            return dict(data.get("routes") or data)

        return ModelGateway(config, book_route_loader=routes)

    def _writing_user_prompt(self):
        from core.auth_manager import AuthManager
        prefs = AuthManager.decrypt_json(self.grant.enc_key, os.path.join(self.user_dir, "user_prefs.enc")) or {}
        return str(prefs.get("global_user_prompt") or "")

    def model_status(self):
        import ipaddress
        from urllib.parse import urlparse
        self._require("read")
        config = self._writing_gateway().config
        def local(model):
            provider = config.providers.get(model.provider_id)
            host = urlparse(provider.base_url).hostname if provider else ""
            if host == "localhost":
                return True
            try:
                return ipaddress.ip_address(host or "").is_loopback
            except ValueError:
                return False
        return {"schema_version": 1, "models": [{"model_id": model.model_id, "model": model.model,
            "provider_id": model.provider_id, "enabled": model.enabled,
            "provider_enabled": bool(config.providers.get(model.provider_id) and config.providers[model.provider_id].enabled),
            "capabilities": asdict(model.capabilities),
            "local_service": local(model),
            "api_key_configured": bool(config.providers.get(model.provider_id) and config.providers[model.provider_id].api_key)
        } for model in config.models.values()], "routes": {key: value.to_dict() for key, value in config.routes.items()}}

    def configure_models(self, config: dict):
        from core.auth_manager import AuthManager
        from core.model_config import ModelConfig, validate_provider
        from core.control_service import ControlValidationError
        self._require("manage")
        self._require("read")
        if not isinstance(config, dict) or not config:
            raise ControlValidationError("模型配置必须是非空 JSON 对象")
        parsed = ModelConfig.from_dict(config, self.settings)
        errors = [error for provider in parsed.providers.values() for error in validate_provider(provider)]
        if errors or not parsed.models:
            raise ControlValidationError("；".join(errors) or "至少配置一个模型")
        AuthManager.encrypt_json(self.grant.enc_key, os.path.join(self.user_dir, "config.enc"), parsed.to_dict())
        return self.model_status()

    def configure_generation(self, enabled: bool):
        from core.control_service import ControlValidationError
        self._require("manage")
        self._require("read")
        if not isinstance(enabled, bool):
            raise ControlValidationError("enabled 必须为布尔值")
        settings = self.settings_manager.load()
        settings["control_agent_enabled"] = enabled
        self.settings_manager.save(settings)
        self.settings = settings
        return self.get_control_settings()

    def plan_chapter(self, book: str, instruction: str, chapter_title: str = "", target_words: int = 1600):
        from core.agent.chapter_generation import AgentChapterGenerationService, AgentChapterRequest
        from core.control_service import ControlValidationError
        from core.model_types import TaskStage
        self._require_generation()
        if not isinstance(instruction, str) or not instruction.strip() or len(instruction) > 30000:
            raise ControlValidationError("规划信息必须为 1 到 30000 字符")
        if not 500 <= int(target_words) <= 50000:
            raise ControlValidationError("目标字数必须在 500 到 50000 之间")
        title, meta = self._book(book)
        target = self.manager.get_active_generation_target(title)
        gateway = self._writing_gateway()
        route = gateway.config.effective_route(TaskStage.PLANNING, gateway.book_route_loader(title))
        model = gateway.config.models[route.primary_model_id].model
        request = AgentChapterRequest(title, target["chapter_num"], chapter_title or f"第{target['chapter_num']}章", instruction,
                                      meta.writing_demand, int(target_words), model)
        planner = AgentChapterGenerationService(self.manager, gateway.compat_client("agent_chapter_plan", book_title=title))
        plan = planner.prepare(request)
        return {"schema_version": 1, "book": self._book_identity(title, meta), "plan": plan.to_dict(), "rendered": plan.render(), "target": target}

    def read_plan(self, book: str, plan_id: str):
        from core.control_service import ControlValidationError, ControlNotFoundError
        self._require("read")
        title, meta = self._book(book)
        if not re.fullmatch(r"chapter_plan_[0-9a-f]{32}", plan_id):
            raise ControlValidationError("plan_id 格式无效")
        workspace = self.manager.get_workspace(title)
        record = workspace.storage.read_json(f"{workspace.agent_root}/chapter_runs/{plan_id}.json", default=None)
        if record is None:
            raise ControlNotFoundError("章节计划不存在")
        return {"schema_version": 1, "book": self._book_identity(title, meta), "record": record}

    def maintain_chapter(self, book: str, node_id: str, force: bool = False):
        import hashlib
        from core.agent.world_maintenance import WorldBibleMaintenanceService
        from core.control_service import ControlNotFoundError, ControlError, ControlValidationError, ControlMaintenanceError
        from core.model_types import TaskStage
        self._require("manage")
        self._require_generation()
        if not isinstance(force, bool):
            raise ControlValidationError("force 必须为布尔值")
        title, meta = self._book(book)
        node = self.manager.ensure_chapter_tree(title).chapter_nodes.get(node_id)
        if not node or node.get("virtual") or node.get("storage_kind") == "extra_uuid":
            raise ControlNotFoundError("请选择正文章节节点")
        content = self.manager.read_chapter_node(title, node_id)
        if not content:
            raise ControlNotFoundError("章节正文不存在")
        gateway = self._writing_gateway()
        route = gateway.config.effective_route(TaskStage.EXTRACTION, gateway.book_route_loader(title))
        model = gateway.config.models[route.primary_model_id].model
        user_prompt = self._writing_user_prompt()
        workspace = self.manager.get_workspace(title)
        checkpoint_path = f"{workspace.agent_root}/maintenance/summaries/{node_id}.json"
        checkpoint = workspace.storage.read_json(checkpoint_path, default={}) or {}
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        summary = str(node.get("summary") or "").strip()
        summary_reused = bool(not force and summary and not summary.startswith("[摘要生成失败:")
                              and checkpoint.get("content_hash", content_hash) == content_hash)
        if not summary_reused:
            summary = self.manager.generate_summary(gateway.compat_client("chapter_tree_summary", book_title=title), content,
                        node["chapter_num"], node["title"], model=model, global_user_prompt=user_prompt,
                        xp_mode=meta.xp_mode, raise_on_error=True)
            if not summary.strip():
                raise ControlError("摘要模型返回空内容")
            self.manager.set_chapter_node_summary(title, node["chapter_num"], node["version"], summary)
        # Commit the successful phase before world maintenance takes its backup.
        workspace.storage.write_json(checkpoint_path, {"content_hash": content_hash})
        self.manager.rebuild_plot_summary_from_tree(title)
        world_route = gateway.config.effective_route(TaskStage.AGENT, gateway.book_route_loader(title))
        world_model = gateway.config.models[world_route.primary_model_id].model
        maintenance = WorldBibleMaintenanceService(self.manager)
        # A failed forced refresh must remain forced when resumed without --force.
        pending = next((item for item in maintenance.list_pending(title)
                        if item.get("chapter_num") == node["chapter_num"] and item.get("version") == node["version"]), {})
        result = maintenance.maintain(gateway.compat_client("agent_world_maintenance", book_title=title), title,
                    node["chapter_num"], node["version"], model=world_model, xp_mode=meta.xp_mode,
                    global_user_prompt=user_prompt, force=force or bool(pending.get("force")))
        payload = {"schema_version": 1, "book": self._book_identity(title, meta), "node_id": node_id,
                   "status": result.status, "summary": summary, "summary_reused": summary_reused,
                   "world_report": result.rebuild_report, "maintenance": asdict(result)}
        if result.status != "completed":
            raise ControlMaintenanceError("摘要已保存；世界书维护待重试：" + result.error, payload)
        return payload

    def list_agent_runs(self, book: str, limit: int = 20):
        from core.agent.repository import AgentRepository
        self._require("read")
        title, meta = self._book(book)
        repository = AgentRepository(self.manager.get_workspace(title))
        limit = max(1, min(100, int(limit)))
        runs = []
        seen = set()
        for session in repository.list_sessions():
            for run_id in reversed(session.run_ids):
                if run_id in seen:
                    continue
                seen.add(run_id)
                run = repository.load_run(run_id)
                if run is not None:
                    runs.append({"run_id": run.run_id, "status": run.status, "iteration": run.iteration,
                        "updated_at": run.updated_at, "terminal_reason": run.terminal_reason})
                if len(runs) == limit:
                    return {"schema_version": 1, "book": self._book_identity(title, meta), "runs": runs}
        return {"schema_version": 1, "book": self._book_identity(title, meta), "runs": runs}

    def read_agent_run(self, book: str, run_id: str):
        from core.agent.repository import AgentRepository
        from core.control_service import ControlValidationError, ControlNotFoundError
        self._require("read")
        title, meta = self._book(book)
        if not re.fullmatch(r"run_[0-9a-f]{32}", run_id):
            raise ControlValidationError("run_id 格式无效")
        run = AgentRepository(self.manager.get_workspace(title)).load_run(run_id)
        if run is None:
            raise ControlNotFoundError("Agent 运行记录不存在")
        return {"schema_version": 1, "book": self._book_identity(title, meta),
            "run_id": run.run_id, "status": run.status, "iteration": run.iteration,
            "terminal_reason": run.terminal_reason, "error": run.error, "usage": run.usage,
            "planning_only": run.planning_only, "change_set_ids": run.change_set_ids,
            "tool_calls": [{"tool_name": call["request"]["tool_name"],
                "arguments": call["request"]["arguments"], "success": call["result"]["success"],
                "error_code": call["result"].get("error_code", ""), "at": call.get("at", "")}
                for call in run.tool_calls]}

    def rebuild_world(self, book: str):
        from core.app_services import ChapterBranchService
        self._require("manage")
        self._require("read")
        title, meta = self._book(book)
        report = ChapterBranchService(self.manager).synchronize(title)
        return {"schema_version": 1, "book": self._book_identity(title, meta), "report": report}

    def apply_change(self, book: str, change_set_id: str, operation_ids: list[str] | None = None):
        from core.agent.changes import ChangeSetService, ChangeSetError
        from core.agent.repository import AgentRepository
        from core.control_service import ControlConflictError, ControlValidationError
        self._require("approve")
        self._require("read")
        title, meta = self._book(book)
        try:
            change = ChangeSetService(self.manager, title, AgentRepository(self.manager.get_workspace(title))).approve(change_set_id, operation_ids)
        except ChangeSetError as exc:
            if "已变化" in str(exc) or "过期" in str(exc):
                raise ControlConflictError(str(exc)) from exc
            raise ControlValidationError(str(exc)) from exc
        return {"schema_version": 1, "book": self._book_identity(title, meta), "change_set": asdict(change)}

    def propose_new_chapter(self, book: str, chapter_num: int, chapter_title: str, content: str, parent_id: str = "", reason: str = ""):
        import uuid
        from core.agent.changes import ChangeSetService, ChangeSetError
        from core.agent.repository import AgentRepository
        from core.control_service import ControlValidationError
        self._require("propose")
        title, meta = self._book(book)
        if not isinstance(content, str) or not content.strip() or len(content) > 2_000_000 or int(chapter_num) < 1:
            raise ControlValidationError("需要有效章号和 1 到 200 万字符的正文")
        repository = AgentRepository(self.manager.get_workspace(title))
        try:
            change = ChangeSetService(self.manager, title, repository).propose_chapter(
                f"external_{uuid.uuid4().hex}", meta.book_id, int(chapter_num), chapter_title, content, parent_id, reason)
        except ChangeSetError as exc:
            raise ControlValidationError(str(exc)) from exc
        change.validation_result.update(origin="external_control", grant_id=self.grant.grant_id)
        repository.save_change_set(change)
        return {"schema_version": 1, "book": self._book_identity(title, meta), "change_set_id": change.change_set_id,
            "operation_ids": [item.operation_id for item in change.operations], "applied": False,
            "requires_human_approval": "approve" not in self.grant.scopes}

    def discard_change(self, book: str, change_set_id: str):
        from core.agent.changes import ChangeSetService, ChangeSetError
        from core.agent.repository import AgentRepository
        from core.control_service import ControlValidationError
        self._require("approve")
        self._require("read")
        title, meta = self._book(book)
        try:
            change = ChangeSetService(self.manager, title, AgentRepository(self.manager.get_workspace(title))).reject(change_set_id)
        except ChangeSetError as exc:
            raise ControlValidationError(str(exc)) from exc
        return {"schema_version": 1, "book": self._book_identity(title, meta), "change_set": asdict(change)}

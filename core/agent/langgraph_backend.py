from __future__ import annotations

import base64
import json
import threading
import uuid
from dataclasses import asdict
from typing import Any, Iterator

from config import Config
from core.agent.context import AgentContextAssembler
from core.agent.middleware import SafetyMiddleware
from core.agent.profiles import build_system_prompt, get_agent_profile
from core.agent.repository import AgentRepository
from core.agent.skills import SkillService
from core.agent.tools import ToolContext
from core.agent.types import AgentEvent, AgentRun, AgentRunRequest, ToolCallRequest, ToolResult, now_iso


class EncryptedAgentCheckpointer:
    """LangGraph checkpointer that serializes every checkpoint through EncryptedStorage."""

    def __new__(cls, repository: AgentRepository):
        try:
            from langgraph.checkpoint.base import BaseCheckpointSaver
        except Exception as exc:
            raise RuntimeError("LangGraph Checkpoint 组件未安装") from exc

        class _Saver(BaseCheckpointSaver):
            def __init__(self, repo):
                super().__init__()
                self.repository = repo
                self._write_lock = threading.RLock()

            @staticmethod
            def _config_values(config) -> tuple[str, str, str]:
                configurable = dict((config or {}).get("configurable") or {})
                return (
                    str(configurable.get("thread_id", "")),
                    str(configurable.get("checkpoint_ns", "")),
                    str(configurable.get("checkpoint_id", "")),
                )

            def _root(self, thread_id: str, namespace: str = "") -> str:
                safe_ns = namespace.replace("/", "_") or "default"
                return f"{self.repository.root}/langgraph/{thread_id}/{safe_ns}"

            def _encode(self, value) -> dict:
                kind, payload = self.serde.dumps_typed(value)
                return {"kind": kind, "payload": base64.b64encode(payload).decode("ascii")}

            def _decode(self, value: dict):
                return self.serde.loads_typed((value["kind"], base64.b64decode(value["payload"])))

            def get_next_version(self, current, channel):
                if current is None:
                    current_value = 0
                elif isinstance(current, int):
                    current_value = current
                else:
                    current_value = int(str(current).split(".", 1)[0])
                return f"{current_value + 1:032}.{uuid.uuid4().int % (10 ** 16):016d}"
            def get_tuple(self, config):
                from langgraph.checkpoint.base import CheckpointTuple

                thread_id, namespace, checkpoint_id = self._config_values(config)
                root = self._root(thread_id, namespace)
                if not checkpoint_id:
                    latest = self.repository.storage.read_json(f"{root}/latest.json", default={}) or {}
                    checkpoint_id = str(latest.get("checkpoint_id", ""))
                if not checkpoint_id:
                    return None
                data = self.repository.storage.read_json(f"{root}/{checkpoint_id}.json")
                if not isinstance(data, dict):
                    return None
                saved_config = {
                    "configurable": {
                        "thread_id": thread_id,
                        "checkpoint_ns": namespace,
                        "checkpoint_id": checkpoint_id,
                    }
                }
                parent_id = str(data.get("parent_checkpoint_id", ""))
                parent_config = (
                    {"configurable": {"thread_id": thread_id, "checkpoint_ns": namespace, "checkpoint_id": parent_id}}
                    if parent_id else None
                )
                writes_data = self.repository.storage.read_json(
                    f"{root}/{checkpoint_id}.writes.json", default={}
                ) or {}
                pending = [
                    (item["task_id"], item["channel"], self._decode(item["value"]))
                    for item in writes_data.get("pending_writes", [])
                ]
                return CheckpointTuple(
                    config=saved_config,
                    checkpoint=self._decode(data["checkpoint"]),
                    metadata=self._decode(data["metadata"]),
                    parent_config=parent_config,
                    pending_writes=pending,
                )

            def put(self, config, checkpoint, metadata, new_versions):
                thread_id, namespace, parent_id = self._config_values(config)
                checkpoint_id = str(checkpoint.get("id") or uuid.uuid4().hex)
                root = self._root(thread_id, namespace)
                payload = {
                    "checkpoint_id": checkpoint_id,
                    "parent_checkpoint_id": parent_id,
                    "checkpoint": self._encode(checkpoint),
                    "metadata": self._encode(metadata),
                    "new_versions": self._encode(new_versions),
                    "saved_at": now_iso(),
                }
                self.repository.storage.write_json(f"{root}/{checkpoint_id}.json", payload)
                self.repository.storage.write_json(f"{root}/latest.json", {"checkpoint_id": checkpoint_id})
                return {"configurable": {"thread_id": thread_id, "checkpoint_ns": namespace, "checkpoint_id": checkpoint_id}}

            def put_writes(self, config, writes, task_id, task_path=""):
                thread_id, namespace, checkpoint_id = self._config_values(config)
                if not checkpoint_id:
                    return
                path = f"{self._root(thread_id, namespace)}/{checkpoint_id}.writes.json"
                with self._write_lock:
                    payload = self.repository.storage.read_json(path, default={}) or {}
                    pending = list(payload.get("pending_writes") or [])
                    pending.extend(
                        {"task_id": task_id, "task_path": task_path, "channel": channel, "value": self._encode(value)}
                        for channel, value in writes
                    )
                    payload["pending_writes"] = pending
                    self.repository.storage.write_json(path, payload)

            def list(self, config, *, filter=None, before=None, limit=None) -> Iterator:
                thread_id, namespace, _ = self._config_values(config or {})
                if not thread_id:
                    return iter(())
                root = self._root(thread_id, namespace)
                paths = [
                    path for path in self.repository.storage.list_files(root)
                    if path.endswith(".json") and not path.endswith("/latest.json") and not path.endswith(".writes.json")
                ]
                tuples = []
                for path in reversed(paths[-limit:] if limit else paths):
                    checkpoint_id = path.rsplit("/", 1)[-1][:-5]
                    item = self.get_tuple({"configurable": {
                        "thread_id": thread_id,
                        "checkpoint_ns": namespace,
                        "checkpoint_id": checkpoint_id,
                    }})
                    if item:
                        tuples.append(item)
                return iter(tuples)

        return _Saver(repository)


class LangGraphAgentBackend:
    backend_name = "langgraph"

    def __init__(
        self,
        *,
        novel_manager,
        client,
        tool_registry,
        event_sink=None,
        services: dict | None = None,
        skills_enabled: bool = True,
    ) -> None:
        try:
            from langchain.agents import create_agent  # noqa: F401
            from langchain_openai import ChatOpenAI  # noqa: F401
            from langgraph.types import Command, interrupt  # noqa: F401
        except Exception as exc:
            raise RuntimeError("LangChain、LangGraph 或 langchain-openai 未安装") from exc
        self.manager = novel_manager
        self.client = client
        self.tool_registry = tool_registry
        self.event_sink = event_sink or (lambda _event: None)
        self.services = services or {}
        self.skills_enabled = skills_enabled
        self.contexts = AgentContextAssembler(novel_manager)
        self.middleware = [SafetyMiddleware()]
        self._cancelled: set[str] = set()
        self._paused: set[str] = set()
        self._sequence: dict[str, int] = {}
        self._lock = threading.Lock()

    def create_session(self, book_title: str, agent_kind: str, title: str = ""):
        workspace = self.manager.get_workspace(book_title)
        manifest = self.manager.ensure_workspace(book_title)
        manifest.features["agent_runtime"] = True
        manifest.features["langgraph_runtime"] = True
        workspace.storage.write_json(workspace.manifest_path, asdict(manifest))
        return AgentRepository(workspace).create_session(
            manifest.book_id,
            book_title,
            agent_kind,
            title or get_agent_profile(agent_kind).display_name,
        )

    def run(self, request: AgentRunRequest) -> AgentRun:
        profile = get_agent_profile(request.agent_kind)
        repository = AgentRepository(self.manager.get_workspace(request.book_title))
        session = repository.load_session(request.session_id)
        if session is None:
            raise ValueError("Agent 会话不存在")
        run = AgentRun(
            f"run_{uuid.uuid4().hex}",
            request.session_id,
            request.book_id,
            request.book_title,
            request.agent_kind,
            request.model,
            status="running",
            mode=request.mode,
        )
        session.active_run_id = run.run_id
        session.run_ids.append(run.run_id)
        session.messages.append({"role": "user", "content": request.user_message, "at": now_iso()})
        skills = SkillService(repository).render_for_agent(request.agent_kind) if self.skills_enabled else ""
        context = self.contexts.assemble(request.book_title, request.manual_references)
        run.messages = [
            {"role": "system", "content": "当前书籍事实上下文：\n" + context.content},
            *[{"role": item["role"], "content": item["content"]} for item in session.messages],
        ]
        for middleware in self.middleware:
            middleware.before_run(run, request, context)
        repository.save_session(session)
        repository.save_run(run)
        self._emit(repository, run.run_id, "run_started", {"agent_kind": run.agent_kind, "backend": "langgraph"})
        try:
            graph = self._build_graph(run, repository, profile, skills)
            result = graph.invoke(
                {"messages": run.messages},
                config={"configurable": {"thread_id": run.run_id}},
            )
            self._apply_graph_result(run, repository, result)
        except Exception as exc:
            if self._is_interrupt(exc):
                run.status = "waiting_approval"
                run.terminal_reason = "approval_required"
                self._emit(repository, run.run_id, "approval_required", {"backend": "langgraph"})
            else:
                run.status = "failed"
                run.error = str(exc) or repr(exc)
                run.terminal_reason = "runtime_error"
                self._emit(repository, run.run_id, "run_failed", {"error": str(exc)})
        finally:
            repository.save_run(run)
            repository.save_checkpoint(run, "langgraph_terminal")
            if session.active_run_id == run.run_id and run.status != "waiting_approval":
                session.active_run_id = ""
            self._append_session_answer(session, run)
            repository.save_session(session)
        return run

    def resume(self, run_id: str, input: Any = None) -> AgentRun | None:
        found = self._find_run(run_id)
        if not found:
            return None
        run, repository = found
        profile = get_agent_profile(run.agent_kind)
        skills = SkillService(repository).render_for_agent(run.agent_kind) if self.skills_enabled else ""
        graph = self._build_graph(run, repository, profile, skills)
        from langgraph.types import Command

        with self._lock:
            self._paused.discard(run_id)
        result = graph.invoke(
            Command(resume=input if input is not None else {"resume": True}),
            config={"configurable": {"thread_id": run.run_id}},
        )
        self._apply_graph_result(run, repository, result)
        repository.save_run(run)
        repository.save_checkpoint(run, "langgraph_resumed")
        session = repository.load_session(run.session_id)
        if session:
            if session.active_run_id == run.run_id and run.status != "waiting_approval":
                session.active_run_id = ""
            self._append_session_answer(session, run)
            repository.save_session(session)
        return run

    def pause(self, run_id: str) -> bool:
        with self._lock:
            self._paused.add(run_id)
        return True

    def cancel(self, run_id: str) -> bool:
        with self._lock:
            self._cancelled.add(run_id)
        return True

    def _build_graph(self, run, repository, profile, skills):
        from langchain.agents import create_agent
        from langchain_core.tools import StructuredTool
        from langchain_openai import ChatOpenAI
        from langgraph.types import interrupt

        model = self.services.get("langchain_model")
        if model is None:
            raw_client = getattr(self.client, "raw_client", self.client)
            api_key = str(getattr(raw_client, "api_key", "") or Config.API_KEY)
            base_url = str(getattr(raw_client, "base_url", "") or Config.BASE_URL)
            model = ChatOpenAI(
                model=run.model,
                api_key=api_key,
                base_url=base_url,
                temperature=float(getattr(self.client, "temperature", 0.3) or 0.3),
            )
        tools = []
        for spec in self.tool_registry.specs_for(profile.allowed_tools):
            def invoke_tool(_spec=spec, **arguments):
                if run.run_id in self._cancelled:
                    raise RuntimeError("用户已取消 Agent Run")
                if run.run_id in self._paused:
                    interrupt({"run_id": run.run_id, "reason": "user_paused"})
                cached = next((
                    item for item in reversed(run.tool_calls)
                    if item.get("request", {}).get("tool_name") == _spec.name
                    and item.get("request", {}).get("arguments") == arguments
                ), None)
                if cached:
                    result = ToolResult(**cached["result"])
                else:
                    call = ToolCallRequest(f"call_{uuid.uuid4().hex}", _spec.name, arguments)
                    context = ToolContext(
                        run.run_id,
                        run.book_id,
                        run.book_title,
                        run.agent_kind,
                        profile.permission_level,
                        repository,
                        self.services,
                    )
                    self._emit(repository, run.run_id, "tool_started", {"tool_name": _spec.name})
                    result = self.tool_registry.execute(call, context, profile.allowed_tools)
                    run.tool_calls.append({"request": asdict(call), "result": asdict(result), "at": now_iso()})
                    repository.save_run(run)
                    self._emit(repository, run.run_id, "tool_completed", asdict(result))
                if result.structured_data.get("requires_approval"):
                    decision = interrupt({
                        "run_id": run.run_id,
                        "tool_name": _spec.name,
                        "arguments": arguments,
                        **result.structured_data,
                    })
                    if isinstance(decision, dict) and not decision.get("approved", True):
                        return "用户拒绝了该变更。"
                return result.content

            tools.append(StructuredTool.from_function(
                func=invoke_tool,
                name=spec.name,
                description=spec.description,
                args_schema=spec.parameters,
            ))
        return create_agent(
            model=model,
            tools=tools,
            system_prompt=build_system_prompt(profile, skills),
            checkpointer=EncryptedAgentCheckpointer(repository),
        )

    def _apply_graph_result(self, run: AgentRun, repository: AgentRepository, result: dict) -> None:
        interrupts = list((result or {}).get("__interrupt__") or [])
        if interrupts:
            run.status = "waiting_approval"
            run.terminal_reason = "approval_required"
            payload = getattr(interrupts[0], "value", interrupts[0])
            self._emit(repository, run.run_id, "approval_required", payload if isinstance(payload, dict) else {"detail": str(payload)})
            return
        self._consume_result(run, result)

    def _consume_result(self, run: AgentRun, result: dict) -> None:
        messages = list((result or {}).get("messages") or [])
        if messages:
            final = messages[-1]
            content = str(getattr(final, "content", "") or "")
            if content:
                run.messages.append({"role": "assistant", "content": content})
        run.status = "cancelled" if run.run_id in self._cancelled else "completed"
        run.terminal_reason = "user_cancelled" if run.status == "cancelled" else "model_completed"

    @staticmethod
    def _append_session_answer(session, run: AgentRun) -> None:
        if run.status != "completed":
            return
        if any(item.get("run_id") == run.run_id for item in session.messages):
            return
        assistants = [
            item for item in run.messages
            if item.get("role") == "assistant" and item.get("content")
        ]
        if assistants:
            session.messages.append({
                "role": "assistant",
                "content": assistants[-1]["content"],
                "run_id": run.run_id,
                "at": now_iso(),
            })
    def _find_run(self, run_id: str):
        for book_title in self.manager.list_books():
            repository = AgentRepository(self.manager.get_workspace(book_title))
            run = repository.load_run(run_id)
            if run:
                return run, repository
        return None

    def _emit(self, repository: AgentRepository, run_id: str, event_type: str, payload: dict) -> None:
        sequence = self._sequence.get(run_id, 0) + 1
        self._sequence[run_id] = sequence
        event = AgentEvent(run_id, sequence, event_type, payload=payload)
        repository.append_event(run_id, asdict(event))
        self.event_sink(event)

    @staticmethod
    def _is_interrupt(exc: Exception) -> bool:
        name = exc.__class__.__name__.lower()
        text = str(exc).lower()
        return "interrupt" in name or "interrupt" in text

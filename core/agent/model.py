from __future__ import annotations

import json
import copy
import hashlib
import re
import math
from dataclasses import dataclass, field

from core.agent.types import ToolCallRequest
from core.model_types import TaskStage


@dataclass
class ModelTurn:
    content: str = ""
    tool_calls: list[ToolCallRequest] = field(default_factory=list)
    usage: dict = field(default_factory=dict)
    planning_only: bool = False


class AgentModelAdapter:
    """OpenAI-compatible adapter with safe fallback when tools are unsupported."""

    def __init__(self, client, model: str, *, temperature: float = 0.3, max_tokens: int = 8192) -> None:
        if hasattr(client, "client_for"):
            self.client = client.client_for("agent_runtime", stage=TaskStage.AGENT)
        else:
            self.client = getattr(client, "raw_client", client)
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.tool_capability: bool | None = None

    def _fit_context(self, messages, tools):
        from core.agent.memory import fit_retrievable_context
        from core.model_gateway import estimate_text_tokens
        gateway = getattr(self.client, "gateway", None)
        if gateway is None:
            return messages
        book = getattr(self.client, "book_title", "")
        routes = gateway.book_route_loader(book) if book and gateway.book_route_loader else {}
        route = gateway.config.effective_route(self.client.stage, routes)
        profile = gateway.config.models[route.primary_model_id]
        window = max(512, min(int(route.overrides.context_window or profile.context_window), profile.context_window))
        output = min(self.max_tokens, int(route.overrides.max_output_tokens or self.max_tokens), profile.max_output_tokens, profile.context_window - 1)
        reserve = max(256, min(4096, math.ceil(window * 0.02)))
        schema_tokens = estimate_text_tokens(json.dumps(tools, ensure_ascii=False))
        return fit_retrievable_context(messages, max(1, window - output - reserve - schema_tokens))

    def complete(self, messages: list[dict], tools: list[dict], *, require_tool: bool = False, on_text=None, check_cancel=None) -> ModelTurn:
        emitted = False

        def forward(text):
            nonlocal emitted
            emitted = True
            on_text(text)

        def create(kwargs):
            if on_text is None:
                return self.client.chat.completions.create(**kwargs)
            from core.agent.model_stream import collect_chat_stream
            return collect_chat_stream(self.client, kwargs, forward, check_cancel)

        # Domain names use dots; OpenAI-compatible APIs require ASCII function
        # names. Translate both schemas and history without changing local IDs.
        aliases = {}
        occupied = {tool.get("function", {}).get("name", "") for tool in tools}
        for name in sorted(occupied):
            if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", name):
                stem = re.sub(r"[^a-zA-Z0-9_-]", "_", name)[:40] or "tool"
                digest = hashlib.sha256(name.encode()).hexdigest()
                alias = f"{stem}_{digest[:12]}"
                while alias in occupied:
                    digest = hashlib.sha256(digest.encode()).hexdigest()
                    alias = f"{stem}_{digest[:12]}"
                occupied.add(alias)
                aliases[name] = alias
        wire_tools = copy.deepcopy(tools)
        no_argument_tools = set()
        for tool in wire_tools:
            function = tool.get("function", {})
            name = function.get("name", "")
            function["name"] = aliases.get(name, name)
            schema = function.get("parameters", {})
            if schema.get("type") == "object" and not schema.get("properties"):
                # Some compatible models invent settings as arguments for {}.
                # Give them one explicit transport-only field; local tools
                # retain their original empty argument contract.
                no_argument_tools.add(name)
                schema["properties"] = {"_call_reason": {"type": "string", "description": "Brief reason for reading this information."}}
                schema["required"] = ["_call_reason"]
                schema["additionalProperties"] = False
        wire_messages = copy.deepcopy(messages)
        for message in wire_messages:
            for call in message.get("tool_calls") or []:
                function = call.get("function", {})
                name = function.get("name", "")
                function["name"] = aliases.get(name, name)
            if message.get("name") in aliases:
                message["name"] = aliases[message["name"]]
        wire_messages = self._fit_context(wire_messages, wire_tools)
        kwargs = {"model": self.model, "messages": wire_messages, "temperature": self.temperature, "max_tokens": self.max_tokens}
        if tools and self.tool_capability is not False:
            kwargs.update({"tools": wire_tools, "tool_choice": "required" if require_tool else "auto"})
        try:
            response = create(kwargs)
        except Exception as exc:
            if emitted:
                # Do not replay a partially displayed answer as a tool fallback.
                raise
            from core.model_gateway import ToolAuthorizationError
            if isinstance(exc, ToolAuthorizationError) and "tools" in kwargs:
                # The gateway rejected the whole tool batch before execution.
                # Permit one schema correction, keeping all authorization checks.
                kwargs["messages"] = wire_messages + [{"role": "system", "content":
                    "上一轮工具调用被校验拒绝，未执行任何工具。请严格按所提供工具的 JSON Schema 重试，"
                    "不要给工具添加 reasoning_effort 等模型参数，不要声称调用已完成。校验错误：" + str(exc)}]
                response = create(kwargs)
            elif "tools" not in kwargs or not self._looks_like_tool_request_rejected(exc):
                raise
            else:
                self.tool_capability = False
                kwargs.pop("tools", None)
                kwargs.pop("tool_choice", None)
                kwargs["messages"] = messages + [{"role": "system", "content": "当前模型不支持工具调用。只能提供分析和计划，不得声称已读取或修改项目数据。"}]
                return self._decode(create(kwargs), planning_only=True)
        turn = self._decode(response)
        reverse = {alias: name for name, alias in aliases.items()}
        for call in turn.tool_calls:
            call.tool_name = reverse.get(call.tool_name, call.tool_name)
            if call.tool_name in no_argument_tools:
                call.arguments.pop("_call_reason", None)
        if turn.tool_calls:
            self.tool_capability = True
        return turn

    @staticmethod
    def _decode(response, planning_only: bool = False) -> ModelTurn:
        choices = AgentModelAdapter._value(response, "choices", []) or []
        if not choices:
            raise ValueError("模型响应中没有 choices")
        message = AgentModelAdapter._value(choices[0], "message")
        if message is None:
            raise ValueError("模型响应中没有 message")
        calls = []
        for call in AgentModelAdapter._value(message, "tool_calls", []) or []:
            try:
                function = AgentModelAdapter._value(call, "function", {}) or {}
                raw_arguments = AgentModelAdapter._value(function, "arguments", "{}")
                arguments = raw_arguments if isinstance(raw_arguments, dict) else json.loads(raw_arguments or "{}")
            except (TypeError, json.JSONDecodeError):
                arguments = {}
            calls.append(ToolCallRequest(
                str(AgentModelAdapter._value(call, "id", "")),
                str(AgentModelAdapter._value(function, "name", "")),
                arguments,
            ))
        usage = AgentModelAdapter._value(response, "usage")
        if hasattr(usage, "model_dump"):
            usage = usage.model_dump()
        elif usage is None:
            usage = {}
        elif not isinstance(usage, dict):
            usage = {
                "prompt_tokens": AgentModelAdapter._value(usage, "prompt_tokens"),
                "completion_tokens": AgentModelAdapter._value(usage, "completion_tokens"),
                "total_tokens": AgentModelAdapter._value(usage, "total_tokens"),
            }
        return ModelTurn(AgentModelAdapter._message_text(message), calls, usage, planning_only)

    @staticmethod
    def _value(source, key: str, default=None):
        if isinstance(source, dict):
            return source.get(key, default)
        return getattr(source, key, default)

    @classmethod
    def _message_text(cls, message) -> str:
        """Extract display text from common OpenAI-compatible message shapes."""
        for key in ("content", "output_text", "text"):
            text = cls._content_text(cls._value(message, key))
            if text:
                return text
        parsed = cls._value(message, "parsed")
        if isinstance(parsed, str):
            return parsed.strip()
        if isinstance(parsed, dict):
            for key in ("answer", "content", "text", "output_text"):
                text = cls._content_text(parsed.get(key))
                if text:
                    return text
        return ""

    @classmethod
    def _content_text(cls, content) -> str:
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, (list, tuple)):
            parts = [cls._content_text(item) for item in content]
            return "\n".join(part for part in parts if part).strip()
        if isinstance(content, dict) or hasattr(content, "__dict__"):
            for key in ("text", "content", "value", "output_text"):
                text = cls._content_text(cls._value(content, key))
                if text:
                    return text
        return ""

    @staticmethod
    def _looks_like_tool_request_rejected(exc: Exception) -> bool:
        """Identify providers that reject an otherwise valid tool-call request.

        Some OpenAI-compatible gateways expose only a bare HTTP 400 instead of
        naming the unsupported ``tools`` or ``tool_choice`` parameter.  Retrying
        once without tools keeps the writing advisor usable as a text-only
        consultant, while any second failure is still surfaced unchanged.
        """
        text = str(exc).lower()
        if any(token in text for token in ("invalid name", "invalid schema", "invalid function", "invalid parameters")):
            return False
        return any(token in text for token in (
            "tool", "function calling", "unknown parameter", "unsupported", "not support",
            "error code: 400", "status code: 400", "status 400",
        ))

from __future__ import annotations

import json
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

    def complete(self, messages: list[dict], tools: list[dict], *, require_tool: bool = False) -> ModelTurn:
        kwargs = {"model": self.model, "messages": messages, "temperature": self.temperature, "max_tokens": self.max_tokens}
        if tools and self.tool_capability is not False:
            kwargs.update({"tools": tools, "tool_choice": "required" if require_tool else "auto"})
        try:
            response = self.client.chat.completions.create(**kwargs)
        except Exception as exc:
            if "tools" not in kwargs or not self._looks_like_tool_request_rejected(exc):
                raise
            self.tool_capability = False
            kwargs.pop("tools", None)
            kwargs.pop("tool_choice", None)
            kwargs["messages"] = messages + [{"role": "system", "content": "当前模型不支持工具调用。只能提供分析和计划，不得声称已读取或修改项目数据。"}]
            return self._decode(self.client.chat.completions.create(**kwargs), planning_only=True)
        turn = self._decode(response)
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
        return any(token in text for token in (
            "tool", "function calling", "unknown parameter", "unsupported", "not support",
            "error code: 400", "status code: 400", "status 400",
        ))

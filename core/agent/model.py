from __future__ import annotations

import json
from dataclasses import dataclass, field

from core.agent.types import ToolCallRequest


@dataclass
class ModelTurn:
    content: str = ""
    tool_calls: list[ToolCallRequest] = field(default_factory=list)
    usage: dict = field(default_factory=dict)
    planning_only: bool = False


class AgentModelAdapter:
    """OpenAI-compatible adapter with safe fallback when tools are unsupported."""

    def __init__(self, client, model: str, *, temperature: float = 0.3, max_tokens: int = 8192) -> None:
        self.client = getattr(client, "raw_client", client)
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.tool_capability: bool | None = None

    def complete(self, messages: list[dict], tools: list[dict]) -> ModelTurn:
        kwargs = {"model": self.model, "messages": messages, "temperature": self.temperature, "max_tokens": self.max_tokens}
        if tools and self.tool_capability is not False:
            kwargs.update({"tools": tools, "tool_choice": "auto"})
        try:
            response = self.client.chat.completions.create(**kwargs)
        except Exception as exc:
            if "tools" not in kwargs or not self._looks_like_tool_unsupported(exc):
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
        message = response.choices[0].message
        calls = []
        for call in getattr(message, "tool_calls", None) or []:
            try:
                arguments = json.loads(call.function.arguments or "{}")
            except json.JSONDecodeError:
                arguments = {}
            calls.append(ToolCallRequest(str(call.id), str(call.function.name), arguments))
        usage = getattr(response, "usage", None)
        if hasattr(usage, "model_dump"):
            usage = usage.model_dump()
        elif usage is None:
            usage = {}
        elif not isinstance(usage, dict):
            usage = {"prompt_tokens": getattr(usage, "prompt_tokens", None), "completion_tokens": getattr(usage, "completion_tokens", None), "total_tokens": getattr(usage, "total_tokens", None)}
        return ModelTurn(str(getattr(message, "content", "") or ""), calls, usage, planning_only)

    @staticmethod
    def _looks_like_tool_unsupported(exc: Exception) -> bool:
        text = str(exc).lower()
        return any(token in text for token in ("tool", "function calling", "unknown parameter", "unsupported", "not support"))

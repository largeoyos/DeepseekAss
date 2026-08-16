"""LangChain chat-model facade backed by the provider-neutral ModelGateway proxy."""
from __future__ import annotations

from typing import Any, Sequence

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import PrivateAttr


class GatewayLangChainChatModel(BaseChatModel):
    _client: Any = PrivateAttr()
    _model_name: str = PrivateAttr()
    _temperature: float = PrivateAttr()
    _tools: list[dict] = PrivateAttr(default_factory=list)
    _tool_choice: Any = PrivateAttr(default=None)

    def __init__(
        self,
        *,
        client,
        model: str,
        temperature: float = 0.3,
        tools: list[dict] | None = None,
        tool_choice: Any = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._client = client
        self._model_name = model
        self._temperature = temperature
        self._tools = list(tools or [])
        self._tool_choice = tool_choice

    @property
    def _llm_type(self) -> str:
        return "deepseekass-model-gateway"

    def bind_tools(
        self,
        tools: Sequence[dict | type | Any],
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ):
        converted = [convert_to_openai_tool(tool) for tool in tools]
        return GatewayLangChainChatModel(
            client=self._client,
            model=self._model_name,
            temperature=self._temperature,
            tools=converted,
            tool_choice=tool_choice,
        )

    @staticmethod
    def _message_dict(message: BaseMessage) -> dict:
        message_type = str(getattr(message, "type", "") or "")
        role = {
            "human": "user",
            "ai": "assistant",
            "system": "system",
            "tool": "tool",
        }.get(message_type, message_type or "user")
        result = {"role": role, "content": message.content}
        if role == "tool":
            result["tool_call_id"] = str(getattr(message, "tool_call_id", "") or "")
            if getattr(message, "name", None):
                result["name"] = message.name
        calls = list(getattr(message, "tool_calls", []) or [])
        if calls:
            result["tool_calls"] = [
                {
                    "id": str(call.get("id") or ""),
                    "type": "function",
                    "function": {
                        "name": str(call.get("name") or ""),
                        "arguments": __import__("json").dumps(call.get("args") or {}, ensure_ascii=False),
                    },
                }
                for call in calls
            ]
        return result

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager=None,
        **kwargs: Any,
    ) -> ChatResult:
        request = {
            "model": self._model_name,
            "messages": [self._message_dict(message) for message in messages],
            "temperature": self._temperature,
            "max_tokens": int(kwargs.get("max_tokens") or 8192),
        }
        if stop:
            request["stop"] = stop
        if self._tools:
            request["tools"] = self._tools
            request["tool_choice"] = self._tool_choice or "auto"
        response = self._client.chat.completions.create(**request)
        message = response.choices[0].message
        tool_calls = [
            {
                "name": str(call.function.name),
                "args": __import__("json").loads(call.function.arguments or "{}"),
                "id": str(call.id),
                "type": "tool_call",
            }
            for call in getattr(message, "tool_calls", []) or []
        ]
        ai_message = AIMessage(
            content=str(getattr(message, "content", "") or ""),
            tool_calls=tool_calls,
            response_metadata={
                "model_name": getattr(response, "model", self._model_name),
                "finish_reason": getattr(response.choices[0], "finish_reason", ""),
            },
        )
        usage = getattr(response, "usage", None)
        token_usage = usage.model_dump() if hasattr(usage, "model_dump") else {}
        return ChatResult(
            generations=[ChatGeneration(message=ai_message)],
            llm_output={"token_usage": token_usage, "model_name": getattr(response, "model", self._model_name)},
        )

"""Protocol adapters for OpenAI Chat, OpenAI Responses, and native Ollama."""
from __future__ import annotations

import json
import uuid
from abc import ABC, abstractmethod
from typing import Any, Iterator

import httpx
from openai import OpenAI

from core.model_config import sanitize_extra_body
from core.model_types import (
    Citation,
    ModelResult,
    PreparedRequest,
    ProviderProtocol,
    ReasoningLevel,
    StreamEvent,
    ToolCall,
    Usage,
    WebSearchPolicy,
)


class ModelAdapterError(RuntimeError):
    pass


class ModelCapabilityError(ModelAdapterError):
    pass


def _get(value: Any, key: str, default=None):
    if isinstance(value, dict):
        return value.get(key, default)
    return getattr(value, key, default)


def _usage_from_openai(usage: Any) -> Usage:
    if usage is None:
        return Usage()
    prompt = _get(usage, "prompt_tokens", _get(usage, "input_tokens"))
    completion = _get(usage, "completion_tokens", _get(usage, "output_tokens"))
    total = _get(usage, "total_tokens")
    details = _get(usage, "completion_tokens_details", _get(usage, "output_tokens_details", {})) or {}
    prompt_details = _get(usage, "prompt_tokens_details", _get(usage, "input_tokens_details", {})) or {}
    return Usage(
        prompt_tokens=prompt,
        completion_tokens=completion,
        total_tokens=total,
        reasoning_tokens=_get(details, "reasoning_tokens"),
        cached_tokens=_get(prompt_details, "cached_tokens"),
    )


def _tool_calls_from_chat(message: Any) -> list[ToolCall]:
    result: list[ToolCall] = []
    for call in _get(message, "tool_calls", []) or []:
        function = _get(call, "function", {}) or {}
        arguments = _get(function, "arguments", {})
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments or "{}")
            except json.JSONDecodeError:
                arguments = {}
        result.append(ToolCall(
            str(_get(call, "id", "") or f"call_{uuid.uuid4().hex}"),
            str(_get(function, "name", "")),
            dict(arguments or {}),
        ))
    return result


def _citations_from_annotations(annotations: Any) -> list[Citation]:
    citations: list[Citation] = []
    seen: set[str] = set()
    for annotation in annotations or []:
        candidate = _get(annotation, "url_citation", annotation)
        url = str(_get(candidate, "url", "") or "")
        if not url or url in seen:
            continue
        seen.add(url)
        citations.append(Citation(
            title=str(_get(candidate, "title", "") or url),
            url=url,
            snippet=str(_get(candidate, "snippet", "") or ""),
        ))
    return citations


def _provider_headers(request: PreparedRequest) -> dict[str, str]:
    provider = request.provider
    headers = dict(provider.headers or {})
    if provider.api_key and provider.auth_header:
        headers[provider.auth_header] = f"{provider.auth_prefix}{provider.api_key}".strip()
    return headers


class BaseModelAdapter(ABC):
    protocol: ProviderProtocol

    @abstractmethod
    def complete(self, request: PreparedRequest) -> ModelResult:
        raise NotImplementedError

    @abstractmethod
    def stream(self, request: PreparedRequest) -> Iterator[StreamEvent]:
        raise NotImplementedError

    def list_models(self, request: PreparedRequest) -> list[str]:
        return []


class OpenAIChatAdapter(BaseModelAdapter):
    protocol = ProviderProtocol.OPENAI_CHAT

    def __init__(self, client_factory=None) -> None:
        self.client_factory = client_factory or self._create_client

    @staticmethod
    def _create_client(request: PreparedRequest):
        provider = request.provider
        return OpenAI(
            api_key=provider.api_key or "local-no-key",
            base_url=provider.base_url,
            timeout=provider.timeout_seconds,
            max_retries=0,  # 重试由 ModelGateway 统一记录和分类
            default_headers=_provider_headers(request) or None,
        )

    @staticmethod
    def _kwargs(request: PreparedRequest, *, stream: bool) -> dict:
        model = request.model_profile
        support = model.parameter_support or {}
        kwargs: dict[str, Any] = {
            "model": model.model,
            "messages": [
                {key: value for key, value in item.items() if key in {
                    "role", "content", "name", "tool_calls", "tool_call_id", "reasoning_content"
                }}
                for item in request.messages
            ],
            "stream": stream,
        }
        if request.temperature is not None and support.get("temperature", True):
            kwargs["temperature"] = request.temperature
        if request.top_p is not None and support.get("top_p", True):
            kwargs["top_p"] = request.top_p
        if request.frequency_penalty is not None and support.get("frequency_penalty", True):
            kwargs["frequency_penalty"] = request.frequency_penalty
        if support.get("max_tokens", True):
            max_field = str(request.metadata.get("max_tokens_field") or "max_tokens")
            kwargs[max_field] = request.max_output_tokens
        extra_body = sanitize_extra_body(model.extra_body)
        reasoning = str(request.reasoning_level or ReasoningLevel.OFF.value)
        if model.template == "deepseek":
            extra_body["thinking"] = {"type": "disabled" if reasoning == "off" else "enabled"}
            if reasoning != "off":
                kwargs["reasoning_effort"] = "max" if reasoning in {"xhigh", "max"} else "high"
        elif model.capabilities.reasoning and reasoning != "off":
            kwargs["reasoning_effort"] = reasoning
        if request.tools:
            if not model.capabilities.tools:
                raise ModelCapabilityError(f"模型 {model.name} 未声明工具调用能力")
            kwargs["tools"] = request.tools
            if request.tool_choice is not None:
                kwargs["tool_choice"] = request.tool_choice
        if request.web_search != WebSearchPolicy.OFF.value and model.capabilities.native_web_search:
            kwargs["web_search_options"] = dict(request.metadata.get("web_search_options") or {})
        for key, value in dict(request.metadata.get("request_kwargs") or {}).items():
            if key in {"response_format", "stop", "seed"}:
                kwargs[key] = value
        extra_body.update(sanitize_extra_body(request.metadata.get("request_extra_body")))
        if stream:
            kwargs["stream_options"] = {"include_usage": True}
        if extra_body:
            kwargs["extra_body"] = extra_body
        return kwargs

    @staticmethod
    def _decode(response: Any, request: PreparedRequest) -> ModelResult:
        choices = _get(response, "choices", []) or []
        if not choices:
            message, finish = {}, ""
        else:
            message = _get(choices[0], "message", {}) or {}
            finish = str(_get(choices[0], "finish_reason", "") or "")
        return ModelResult(
            content=str(_get(message, "content", "") or ""),
            reasoning_content=str(_get(message, "reasoning_content", "") or ""),
            tool_calls=_tool_calls_from_chat(message),
            citations=_citations_from_annotations(_get(message, "annotations", [])),
            usage=_usage_from_openai(_get(response, "usage")),
            finish_reason=finish,
            raw=response,
        )

    def complete(self, request: PreparedRequest) -> ModelResult:
        response = self.client_factory(request).chat.completions.create(**self._kwargs(request, stream=False))
        return self._decode(response, request)

    def stream(self, request: PreparedRequest) -> Iterator[StreamEvent]:
        client = self.client_factory(request)
        try:
            stream = client.chat.completions.create(**self._kwargs(request, stream=True))
        except Exception:
            kwargs = self._kwargs(request, stream=True)
            kwargs.pop("stream_options", None)
            stream = client.chat.completions.create(**kwargs)
        content: list[str] = []
        reasoning: list[str] = []
        usage = Usage()
        finish_reason = ""
        try:
            for chunk in stream:
                if _get(chunk, "usage") is not None:
                    usage = _usage_from_openai(_get(chunk, "usage"))
                choices = _get(chunk, "choices", []) or []
                if not choices:
                    continue
                finish_reason = str(_get(choices[0], "finish_reason", finish_reason) or finish_reason)
                delta = _get(choices[0], "delta", {}) or {}
                reasoning_delta = str(_get(delta, "reasoning_content", "") or "")
                text_delta = str(_get(delta, "content", "") or "")
                if reasoning_delta:
                    reasoning.append(reasoning_delta)
                    yield StreamEvent("reasoning_delta", reasoning_delta)
                if text_delta:
                    content.append(text_delta)
                    yield StreamEvent("text_delta", text_delta)
        finally:
            close_stream = getattr(stream, "close", None)
            if callable(close_stream):
                close_stream()
        yield StreamEvent("completed", result=ModelResult(
            content="".join(content), reasoning_content="".join(reasoning),
            usage=usage, finish_reason=finish_reason,
        ))

    def list_models(self, request: PreparedRequest) -> list[str]:
        result = self.client_factory(request).models.list()
        return [str(_get(item, "id", "")) for item in _get(result, "data", []) or [] if _get(item, "id")]


def _responses_tools(tools: list[dict]) -> list[dict]:
    result = []
    for tool in tools:
        if tool.get("type") != "function":
            result.append(dict(tool))
            continue
        function = dict(tool.get("function") or {})
        result.append({
            "type": "function",
            "name": function.get("name", ""),
            "description": function.get("description", ""),
            "parameters": function.get("parameters") or {"type": "object", "properties": {}},
            "strict": bool(function.get("strict", False)),
        })
    return result


def _responses_input(messages: list[dict]) -> tuple[str, list[dict]]:
    instructions: list[str] = []
    inputs: list[dict] = []
    for item in messages:
        role = str(item.get("role") or "user")
        if role == "system":
            instructions.append(str(item.get("content") or ""))
            continue
        if role == "tool":
            inputs.append({
                "type": "function_call_output",
                "call_id": str(item.get("tool_call_id") or ""),
                "output": str(item.get("content") or ""),
            })
            continue
        content = str(item.get("content") or "")
        if content:
            inputs.append({"role": role, "content": content})
        for call in item.get("tool_calls") or []:
            function = dict(call.get("function") or {})
            inputs.append({
                "type": "function_call",
                "call_id": str(call.get("id") or ""),
                "name": str(function.get("name") or ""),
                "arguments": str(function.get("arguments") or "{}"),
            })
    return "\n\n".join(value for value in instructions if value.strip()), inputs


class OpenAIResponsesAdapter(BaseModelAdapter):
    protocol = ProviderProtocol.OPENAI_RESPONSES

    def __init__(self, client_factory=None) -> None:
        self.client_factory = client_factory or OpenAIChatAdapter._create_client

    @staticmethod
    def _kwargs(request: PreparedRequest, *, stream: bool) -> dict:
        instructions, inputs = _responses_input(request.messages)
        kwargs: dict[str, Any] = {
            "model": request.model_profile.model,
            "input": inputs,
            "store": False,
            "stream": stream,
        }
        if request.model_profile.parameter_support.get("max_tokens", True):
            kwargs["max_output_tokens"] = request.max_output_tokens
        if instructions:
            kwargs["instructions"] = instructions
        if request.temperature is not None and request.model_profile.parameter_support.get("temperature", True):
            kwargs["temperature"] = request.temperature
        if request.top_p is not None and request.model_profile.parameter_support.get("top_p", True):
            kwargs["top_p"] = request.top_p
        reasoning = str(request.reasoning_level or "off")
        if request.model_profile.capabilities.reasoning and reasoning != "off":
            kwargs["reasoning"] = {"effort": reasoning, "summary": "auto"}
        tools = _responses_tools(request.tools)
        if request.web_search != WebSearchPolicy.OFF.value and request.model_profile.capabilities.native_web_search:
            tools.append({"type": "web_search"})
        if tools:
            kwargs["tools"] = tools
            if request.tool_choice is not None:
                kwargs["tool_choice"] = request.tool_choice
            elif request.web_search == WebSearchPolicy.ALWAYS.value and len(tools) == 1:
                kwargs["tool_choice"] = "required"
        response_format = dict(request.metadata.get("request_kwargs") or {}).get("response_format")
        if isinstance(response_format, dict):
            response_type = str(response_format.get("type") or "")
            if response_type == "json_object":
                kwargs["text"] = {"format": {"type": "json_object"}}
            elif response_type == "json_schema" and response_format.get("json_schema"):
                schema = dict(response_format.get("json_schema") or {})
                kwargs["text"] = {"format": {"type": "json_schema", **schema}}
        extra_body = sanitize_extra_body(request.model_profile.extra_body)
        extra_body.update(sanitize_extra_body(request.metadata.get("request_extra_body")))
        if extra_body:
            kwargs["extra_body"] = extra_body
        return kwargs

    @staticmethod
    def _decode(response: Any, request: PreparedRequest) -> ModelResult:
        content = str(_get(response, "output_text", "") or "")
        reasoning: list[str] = []
        calls: list[ToolCall] = []
        citations: list[Citation] = []
        for item in _get(response, "output", []) or []:
            item_type = str(_get(item, "type", "") or "")
            if item_type == "function_call":
                arguments = _get(item, "arguments", "{}")
                if isinstance(arguments, str):
                    try:
                        arguments = json.loads(arguments or "{}")
                    except json.JSONDecodeError:
                        arguments = {}
                calls.append(ToolCall(
                    str(_get(item, "call_id", _get(item, "id", "")) or f"call_{uuid.uuid4().hex}"),
                    str(_get(item, "name", "")), dict(arguments or {}),
                ))
            elif item_type == "reasoning":
                for summary in _get(item, "summary", []) or []:
                    text = str(_get(summary, "text", "") or "")
                    if text:
                        reasoning.append(text)
            elif item_type == "message":
                for part in _get(item, "content", []) or []:
                    citations.extend(_citations_from_annotations(_get(part, "annotations", [])))
                    if not content and str(_get(part, "type", "")) in {"output_text", "text"}:
                        content += str(_get(part, "text", "") or "")
        return ModelResult(
            content=content,
            reasoning_content="\n".join(reasoning),
            tool_calls=calls,
            citations=citations,
            usage=_usage_from_openai(_get(response, "usage")),
            finish_reason=str(_get(response, "status", "") or ""),
            raw=response,
        )

    def complete(self, request: PreparedRequest) -> ModelResult:
        response = self.client_factory(request).responses.create(**self._kwargs(request, stream=False))
        return self._decode(response, request)

    def stream(self, request: PreparedRequest) -> Iterator[StreamEvent]:
        stream = self.client_factory(request).responses.create(**self._kwargs(request, stream=True))
        content: list[str] = []
        reasoning: list[str] = []
        completed = None
        for event in stream:
            event_type = str(_get(event, "type", "") or "")
            delta = str(_get(event, "delta", "") or "")
            if event_type == "response.output_text.delta" and delta:
                content.append(delta)
                yield StreamEvent("text_delta", delta)
            elif "reasoning" in event_type and event_type.endswith(".delta") and delta:
                reasoning.append(delta)
                yield StreamEvent("reasoning_delta", delta)
            elif event_type == "response.completed":
                completed = _get(event, "response")
        result = self._decode(completed, request) if completed is not None else ModelResult()
        if not result.content:
            result.content = "".join(content)
        if not result.reasoning_content:
            result.reasoning_content = "".join(reasoning)
        yield StreamEvent("completed", result=result)

    def list_models(self, request: PreparedRequest) -> list[str]:
        result = self.client_factory(request).models.list()
        return [str(_get(item, "id", "")) for item in _get(result, "data", []) or [] if _get(item, "id")]


class OllamaNativeAdapter(BaseModelAdapter):
    protocol = ProviderProtocol.OLLAMA_NATIVE

    def __init__(self, client_factory=None) -> None:
        self.client_factory = client_factory or self._create_client

    @staticmethod
    def _create_client(request: PreparedRequest):
        return httpx.Client(
            base_url=request.provider.base_url.rstrip("/"),
            headers=_provider_headers(request),
            timeout=request.provider.timeout_seconds,
        )

    @staticmethod
    def _payload(request: PreparedRequest, *, stream: bool) -> dict:
        model = request.model_profile
        messages = []
        for item in request.messages:
            normalized = {key: value for key, value in item.items() if key in {
                "role", "content", "thinking", "tool_calls", "tool_name"
            }}
            if item.get("role") == "tool" and item.get("tool_call_id"):
                normalized.setdefault("tool_name", str(item.get("name") or item.get("tool_call_id")))
            messages.append(normalized)
        payload: dict[str, Any] = {
            "model": model.model,
            "messages": messages,
            "stream": stream,
            "options": {},
        }
        if model.parameter_support.get("max_tokens", True):
            payload["options"]["num_predict"] = request.max_output_tokens
        if request.temperature is not None and model.parameter_support.get("temperature", True):
            payload["options"]["temperature"] = request.temperature
        if request.top_p is not None and model.parameter_support.get("top_p", True):
            payload["options"]["top_p"] = request.top_p
        if model.runtime_context:
            payload["options"]["num_ctx"] = int(model.runtime_context)
        reasoning = str(request.reasoning_level or "off")
        payload["think"] = False if reasoning == "off" else reasoning
        if request.tools:
            if not model.capabilities.tools:
                raise ModelCapabilityError(f"模型 {model.name} 未声明工具调用能力")
            payload["tools"] = request.tools
        response_format = dict(request.metadata.get("request_kwargs") or {}).get("response_format")
        if isinstance(response_format, dict) and response_format.get("type") == "json_object":
            payload["format"] = "json"
        extra_body = sanitize_extra_body(model.extra_body)
        extra_body.update(sanitize_extra_body(request.metadata.get("request_extra_body")))
        for key, value in extra_body.items():
            if key == "options" and isinstance(value, dict):
                protected = {"num_predict", "num_ctx", "temperature", "top_p"}
                payload["options"].update({name: option for name, option in value.items() if name not in protected})
            else:
                payload[key] = value
        return payload

    @staticmethod
    def _decode(data: dict, request: PreparedRequest) -> ModelResult:
        message = dict(data.get("message") or {})
        calls: list[ToolCall] = []
        for index, call in enumerate(message.get("tool_calls") or []):
            function = dict(call.get("function") or {})
            arguments = function.get("arguments") or {}
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except json.JSONDecodeError:
                    arguments = {}
            calls.append(ToolCall(
                str(call.get("id") or f"ollama_{index}_{uuid.uuid4().hex[:8]}"),
                str(function.get("name") or ""), dict(arguments or {}),
            ))
        prompt = data.get("prompt_eval_count")
        completion = data.get("eval_count")
        total = prompt + completion if isinstance(prompt, int) and isinstance(completion, int) else None
        return ModelResult(
            content=str(message.get("content") or ""),
            reasoning_content=str(message.get("thinking") or ""),
            tool_calls=calls,
            usage=Usage(prompt_tokens=prompt, completion_tokens=completion, total_tokens=total),
            finish_reason=str(data.get("done_reason") or ""),
            raw=data,
        )

    def complete(self, request: PreparedRequest) -> ModelResult:
        with self.client_factory(request) as client:
            response = client.post("/api/chat", json=self._payload(request, stream=False))
            response.raise_for_status()
            return self._decode(response.json(), request)

    def stream(self, request: PreparedRequest) -> Iterator[StreamEvent]:
        content: list[str] = []
        reasoning: list[str] = []
        final_data: dict = {}
        with self.client_factory(request) as client:
            with client.stream("POST", "/api/chat", json=self._payload(request, stream=True)) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    if not line.strip():
                        continue
                    data = json.loads(line)
                    final_data = data
                    message = dict(data.get("message") or {})
                    thinking = str(message.get("thinking") or "")
                    text = str(message.get("content") or "")
                    if thinking:
                        reasoning.append(thinking)
                        yield StreamEvent("reasoning_delta", thinking)
                    if text:
                        content.append(text)
                        yield StreamEvent("text_delta", text)
        result = self._decode(final_data, request)
        if not result.content:
            result.content = "".join(content)
        if not result.reasoning_content:
            result.reasoning_content = "".join(reasoning)
        yield StreamEvent("completed", result=result)

    def list_models(self, request: PreparedRequest) -> list[str]:
        with self.client_factory(request) as client:
            response = client.get("/api/tags")
            response.raise_for_status()
            return [str(item.get("name") or item.get("model") or "") for item in response.json().get("models", []) if item.get("name") or item.get("model")]


def default_adapter_registry() -> dict[ProviderProtocol, BaseModelAdapter]:
    return {
        ProviderProtocol.OPENAI_CHAT: OpenAIChatAdapter(),
        ProviderProtocol.OPENAI_RESPONSES: OpenAIResponsesAdapter(),
        ProviderProtocol.OLLAMA_NATIVE: OllamaNativeAdapter(),
    }

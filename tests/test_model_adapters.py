import unittest
from types import SimpleNamespace

from core.model_adapters import OllamaNativeAdapter, OpenAIChatAdapter, OpenAIResponsesAdapter
from core.model_types import (
    ModelCapabilities,
    ModelProfile,
    PreparedRequest,
    ProviderProfile,
    ProviderProtocol,
    TaskStage,
)


def prepared(protocol: ProviderProtocol) -> PreparedRequest:
    provider = ProviderProfile("provider", "Provider", protocol, "http://127.0.0.1:1", allow_insecure_http=True)
    model = ModelProfile(
        "model", "provider", "Model", "real-model",
        capabilities=ModelCapabilities(
            streaming=True, tools=True, native_web_search=protocol == ProviderProtocol.OPENAI_RESPONSES,
            reasoning=True, supported_reasoning_levels=["off", "high"],
        ),
    )
    return PreparedRequest(
        "chat", TaskStage.INTERACTIVE, provider, model,
        [{"role": "user", "content": "hello"}], 0.7, 0.9, 0.0,
        256, "high", "off",
    )


class _ChatCompletions:
    def create(self, **kwargs):
        if kwargs.get("stream"):
            return iter([
                SimpleNamespace(choices=[SimpleNamespace(
                    delta=SimpleNamespace(content="A", reasoning_content="think-"), finish_reason=None,
                )], usage=None),
                SimpleNamespace(choices=[SimpleNamespace(
                    delta=SimpleNamespace(content="B", reasoning_content="more"), finish_reason="stop",
                )], usage=None),
                SimpleNamespace(choices=[], usage=SimpleNamespace(
                    prompt_tokens=3, completion_tokens=4, total_tokens=7,
                    completion_tokens_details=SimpleNamespace(reasoning_tokens=2),
                    prompt_tokens_details=None,
                )),
            ])
        message = SimpleNamespace(
            content="answer", reasoning_content="thought",
            tool_calls=[SimpleNamespace(
                id="call-1", function=SimpleNamespace(name="lookup", arguments='{"id": 1}')
            )],
            annotations=[{"url_citation": {"title": "Doc", "url": "https://example.test", "snippet": "fact"}}],
        )
        return SimpleNamespace(
            choices=[SimpleNamespace(message=message, finish_reason="stop")],
            usage={"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5},
        )


class _ChatClient:
    def __init__(self):
        self.chat = SimpleNamespace(completions=_ChatCompletions())


class _ResponsesApi:
    def create(self, **kwargs):
        response = SimpleNamespace(
            output_text="response-answer",
            output=[
                SimpleNamespace(type="reasoning", summary=[SimpleNamespace(text="reason")]),
                SimpleNamespace(type="function_call", call_id="call-2", name="lookup", arguments='{"q":"x"}'),
                SimpleNamespace(type="message", content=[SimpleNamespace(
                    type="output_text", text="response-answer",
                    annotations=[{"type": "url_citation", "title": "Source", "url": "https://example.test/source"}],
                )]),
            ],
            usage=SimpleNamespace(input_tokens=5, output_tokens=6, total_tokens=11, output_tokens_details={"reasoning_tokens": 2}),
            status="completed",
        )
        if not kwargs.get("stream"):
            return response
        return iter([
            SimpleNamespace(type="response.reasoning_summary_text.delta", delta="rea"),
            SimpleNamespace(type="response.output_text.delta", delta="response-"),
            SimpleNamespace(type="response.output_text.delta", delta="answer"),
            SimpleNamespace(type="response.completed", response=response),
        ])


class _ResponsesClient:
    def __init__(self):
        self.responses = _ResponsesApi()


class _HttpResponse:
    def __init__(self, data=None, lines=None):
        self.data = data or {}
        self.lines = list(lines or [])

    def raise_for_status(self):
        return None

    def json(self):
        return self.data

    def iter_lines(self):
        return iter(self.lines)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class _OllamaClient:
    final = {
        "message": {
            "content": "ollama-answer", "thinking": "ollama-thought",
            "tool_calls": [{"function": {"name": "lookup", "arguments": {"id": 2}}}],
        },
        "prompt_eval_count": 7, "eval_count": 8, "done_reason": "stop",
    }

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def post(self, _path, json=None):
        return _HttpResponse(self.final)

    def stream(self, *_args, **_kwargs):
        return _HttpResponse(lines=[
            '{"message":{"thinking":"think-","content":""},"done":false}',
            '{"message":{"thinking":"","content":"local"},"done":false}',
            '{"message":{"content":""},"prompt_eval_count":7,"eval_count":8,"done":true,"done_reason":"stop"}',
        ])


class ModelAdapterSimulationTests(unittest.TestCase):
    def test_openai_chat_complete_and_stream(self):
        adapter = OpenAIChatAdapter(client_factory=lambda _request: _ChatClient())
        result = adapter.complete(prepared(ProviderProtocol.OPENAI_CHAT))
        self.assertEqual("answer", result.content)
        self.assertEqual("lookup", result.tool_calls[0].name)
        self.assertEqual("https://example.test", result.citations[0].url)
        events = list(adapter.stream(prepared(ProviderProtocol.OPENAI_CHAT)))
        self.assertEqual("AB", events[-1].result.content)
        self.assertEqual(7, events[-1].result.usage.total_tokens)

    def test_openai_responses_complete_and_sse(self):
        adapter = OpenAIResponsesAdapter(client_factory=lambda _request: _ResponsesClient())
        result = adapter.complete(prepared(ProviderProtocol.OPENAI_RESPONSES))
        self.assertEqual("response-answer", result.content)
        self.assertEqual("lookup", result.tool_calls[0].name)
        self.assertEqual(11, result.usage.total_tokens)
        events = list(adapter.stream(prepared(ProviderProtocol.OPENAI_RESPONSES)))
        self.assertEqual("response-answer", events[-1].result.content)
        self.assertTrue(any(event.event_type == "reasoning_delta" for event in events))

    def test_ollama_complete_and_ndjson_stream(self):
        adapter = OllamaNativeAdapter(client_factory=lambda _request: _OllamaClient())
        result = adapter.complete(prepared(ProviderProtocol.OLLAMA_NATIVE))
        self.assertEqual("ollama-answer", result.content)
        self.assertEqual("ollama-thought", result.reasoning_content)
        self.assertEqual(15, result.usage.total_tokens)
        events = list(adapter.stream(prepared(ProviderProtocol.OLLAMA_NATIVE)))
        self.assertEqual("local", events[-1].result.content)
        self.assertEqual("think-", events[-1].result.reasoning_content)
        self.assertEqual(15, events[-1].result.usage.total_tokens)


if __name__ == "__main__":
    unittest.main()

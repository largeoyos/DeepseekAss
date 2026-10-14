import unittest
from types import SimpleNamespace
from unittest.mock import Mock

import httpx

from core.model_adapters import OpenAIChatAdapter
from core.model_config import stage_for_operation
from core.model_gateway import ModelGateway, is_fallback_error
from core.model_types import ModelRequest, ModelResult, ProviderProtocol, RoutePolicy, StreamEvent, TaskStage
from tests.test_model_gateway import config_with_models
from tests.test_model_adapters import prepared


class StreamAdapter:
    def __init__(self, outcomes):
        self.outcomes = iter(outcomes)
        self.requests = []

    def stream(self, request):
        self.requests.append(request)
        for item in next(self.outcomes):
            if isinstance(item, Exception):
                raise item
            yield item


class StreamRecoveryTests(unittest.TestCase):
    def test_agent_prose_uses_drafting_model_even_when_agent_model_differs(self):
        protocol = ProviderProtocol.OPENAI_CHAT
        config = config_with_models(protocol, protocol)
        config.routes[TaskStage.DRAFTING.value] = RoutePolicy("m1")
        config.routes[TaskStage.AGENT.value] = RoutePolicy("m0")
        adapter = StreamAdapter([[StreamEvent("completed", result=ModelResult(content="ok"))]])
        gateway = ModelGateway(config, adapters={protocol: adapter})
        client = gateway.compat_client("agent_novel_chapter", stage=stage_for_operation("agent_novel_chapter"))
        list(client.chat.completions.create(model="m0", messages=[], stream=True))
        self.assertEqual("m1", adapter.requests[0].model_profile.model_id)

    def test_remote_read_errors_retry_before_any_output(self):
        for error_type in (httpx.RemoteProtocolError, httpx.ReadError):
            with self.subTest(error=error_type):
                protocol = ProviderProtocol.OPENAI_CHAT
                config = config_with_models(protocol)
                adapter = StreamAdapter([[error_type("disconnect")], [StreamEvent("completed", result=ModelResult(content="ok"))]])
                gateway = ModelGateway(config, adapters={protocol: adapter})
                events = list(gateway.stream(ModelRequest("novel_chapter", TaskStage.DRAFTING, [])))
                self.assertEqual("ok", events[-1].result.content)
                self.assertEqual(2, len(adapter.requests))

    def test_partial_prose_or_reasoning_does_not_restart_or_use_fallback(self):
        for event_type in ("text_delta", "reasoning_delta"):
            with self.subTest(event=event_type):
                protocol = ProviderProtocol.OPENAI_CHAT
                config = config_with_models(protocol, protocol)
                adapter = StreamAdapter([[StreamEvent(event_type, "partial"), httpx.RemoteProtocolError("disconnect")]])
                gateway = ModelGateway(config, adapters={protocol: adapter})
                stream = gateway.stream(ModelRequest("novel_chapter", TaskStage.DRAFTING, []))
                self.assertEqual("partial", next(stream).text)
                with self.assertRaises(httpx.RemoteProtocolError):
                    next(stream)
                self.assertEqual(1, len(adapter.requests))

    def test_exhausted_retries_use_configured_fallback(self):
        protocol = ProviderProtocol.OPENAI_CHAT
        config = config_with_models(protocol, protocol)
        adapter = StreamAdapter([
            [httpx.ReadError("first")], [httpx.ReadError("second")],
            [StreamEvent("completed", result=ModelResult(content="fallback"))],
        ])
        gateway = ModelGateway(config, adapters={protocol: adapter})
        events = list(gateway.stream(ModelRequest("novel_chapter", TaskStage.DRAFTING, [])))
        self.assertEqual("fallback", events[-1].result.content)
        self.assertEqual(["m0", "m0", "m1"], [r.model_profile.model_id for r in adapter.requests])

    def test_local_errors_and_auth_failures_are_not_retried(self):
        error = RuntimeError("unauthorized")
        error.status_code = 401
        self.assertFalse(is_fallback_error(error))
        self.assertFalse(is_fallback_error(httpx.LocalProtocolError("invalid request")))

    def test_adapter_does_not_hide_connection_retry(self):
        create = Mock(side_effect=httpx.ConnectError("failed"))
        adapter = OpenAIChatAdapter(client_factory=lambda request: SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))))
        with self.assertRaises(httpx.ConnectError):
            list(adapter.stream(prepared(ProviderProtocol.OPENAI_CHAT)))
        create.assert_called_once()

    def test_adapter_removes_rejected_stream_options(self):
        error = RuntimeError("unsupported stream_options")
        error.status_code = 400
        create = Mock(side_effect=[error, iter([])])
        adapter = OpenAIChatAdapter(client_factory=lambda request: SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))))
        list(adapter.stream(prepared(ProviderProtocol.OPENAI_CHAT)))
        self.assertEqual(2, create.call_count)
        self.assertNotIn("stream_options", create.call_args.kwargs)


if __name__ == "__main__":
    unittest.main()

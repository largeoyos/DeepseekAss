import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from core.model_adapters import OllamaNativeAdapter, OpenAIChatAdapter, OpenAIResponsesAdapter
from core.model_config import ModelConfig, create_provider_template, migrate_legacy_config, sanitize_extra_body, validate_provider
from core.model_gateway import ContextBudgetExceeded, ModelGateway, ToolAuthorizationError, compact_messages
from core.model_types import (
    ModelCapabilities,
    ModelDefaults,
    ModelProfile,
    ModelRequest,
    ModelResult,
    PreparedRequest,
    ProviderProfile,
    ProviderProtocol,
    RoutePolicy,
    TaskStage,
    ToolCall,
    Usage,
)
from web.services import masked_model_config


class FakeAdapter:
    def __init__(self, protocol, outcomes):
        self.protocol = protocol
        self.outcomes = list(outcomes)
        self.requests = []

    def complete(self, request):
        self.requests.append(request)
        value = self.outcomes.pop(0)
        if isinstance(value, Exception):
            raise value
        return value

    def stream(self, request):
        raise NotImplementedError

    def list_models(self, request):
        return [request.model_profile.model]


def config_with_models(*models):
    providers = {}
    profiles = {}
    for index, protocol in enumerate(models):
        provider_id = f"p{index}"
        model_id = f"m{index}"
        providers[provider_id] = ProviderProfile(
            provider_id, provider_id, protocol, "http://127.0.0.1:1", allow_insecure_http=True
        )
        profiles[model_id] = ModelProfile(
            model_id, provider_id, model_id, model_id,
            capabilities=ModelCapabilities(reasoning=True, supported_reasoning_levels=["off", "high"]),
            defaults=ModelDefaults(max_output_tokens=128),
        )
    routes = {stage.value: RoutePolicy("m0", [f"m{i}" for i in range(1, len(models))]) for stage in TaskStage}
    return ModelConfig(providers=providers, models=profiles, routes=routes)


class ModelConfigTests(unittest.TestCase):
    def test_legacy_text_config_migrates_to_six_routes(self):
        config = migrate_legacy_config(
            {"text": {"api_key": "key", "base_url": "https://api.deepseek.com", "model": "deepseek-v4-pro"}},
            {"favorite_models": ["deepseek-v4-flash"]},
        )
        self.assertEqual(2, config.schema_version)
        self.assertEqual(6, len(config.routes))
        self.assertEqual("deepseek-v4-pro", config.legacy_text_config()["model"])
        profile = next(item for item in config.models.values() if item.model == "deepseek-v4-pro")
        self.assertEqual("deepseek", profile.template)
        self.assertEqual(["off", "high", "max"], profile.capabilities.supported_reasoning_levels)

    def test_pro_body_legacy_route_is_preserved(self):
        config = migrate_legacy_config(
            {"text": {"api_key": "key", "base_url": "https://api.deepseek.com", "model": "deepseek-v4-flash"}},
            {"model_routing_mode": "pro_body_flash_aux"},
        )
        draft = config.models[config.routes[TaskStage.DRAFTING.value].primary_model_id]
        extract = config.models[config.routes[TaskStage.EXTRACTION.value].primary_model_id]
        self.assertEqual("deepseek-v4-pro", draft.model)
        self.assertEqual("deepseek-v4-flash", extract.model)

    def test_reserved_extra_body_fields_are_removed(self):
        value = sanitize_extra_body({"model": "bad", "messages": [], "thinking": {"type": "enabled"}})
        self.assertNotIn("model", value)
        self.assertNotIn("messages", value)
        self.assertNotIn("thinking", value)

    def test_non_local_plain_http_requires_explicit_confirmation(self):
        provider = ProviderProfile("p", "remote", ProviderProtocol.OPENAI_CHAT, "http://192.0.2.10/v1")
        self.assertTrue(any("不安全传输" in item for item in validate_provider(provider)))
        provider.allow_insecure_http = True
        self.assertFalse(validate_provider(provider))

    def test_ollama_template_allows_empty_key(self):
        provider, _model = create_provider_template("ollama")
        self.assertEqual("", provider.api_key)
        self.assertFalse(validate_provider(provider))

    def test_book_route_inherits_or_overrides_global(self):
        config = config_with_models(ProviderProtocol.OPENAI_CHAT, ProviderProtocol.OLLAMA_NATIVE)
        inherited = config.effective_route(TaskStage.DRAFTING, {
            "drafting": {"inherit": True, "primary_model_id": "m1"},
        })
        self.assertEqual("m0", inherited.primary_model_id)
        overridden = config.effective_route(TaskStage.DRAFTING, {
            "drafting": {"inherit": False, "primary_model_id": "m1"},
        })
        self.assertEqual("m1", overridden.primary_model_id)

    def test_model_center_mask_hides_keys_headers_and_advanced_json(self):
        config = config_with_models(ProviderProtocol.OPENAI_CHAT)
        config.providers["p0"].api_key = "secret-api-key"
        config.providers["p0"].headers = {"X-Secret": "value"}
        config.models["m0"].extra_body = {"vendor_secret": "hidden"}
        masked = masked_model_config(config)
        self.assertNotIn("secret-api-key", str(masked))
        self.assertEqual("***", masked["providers"]["p0"]["headers"]["X-Secret"])
        self.assertEqual({}, masked["models"]["m0"]["extra_body"])


class AdapterMappingTests(unittest.TestCase):
    def _prepared(self, protocol, template="generic", reasoning="off"):
        provider = ProviderProfile("p", "provider", protocol, "http://127.0.0.1:1", allow_insecure_http=True)
        model = ModelProfile(
            "m", "p", "model", "model", template=template,
            capabilities=ModelCapabilities(reasoning=True, tools=True, supported_reasoning_levels=["off", "high", "max"]),
        )
        return PreparedRequest(
            "chat", TaskStage.INTERACTIVE, provider, model,
            [{"role": "user", "content": "hello"}], 0.7, 0.9, 0.0,
            1024, reasoning, "off",
        )

    def test_deepseek_reasoning_uses_thinking_extra_body(self):
        kwargs = OpenAIChatAdapter._kwargs(self._prepared(ProviderProtocol.OPENAI_CHAT, "deepseek", "max"), stream=False)
        self.assertEqual("max", kwargs["reasoning_effort"])
        self.assertEqual({"type": "enabled"}, kwargs["extra_body"]["thinking"])

    def test_responses_uses_responses_shape_and_store_false(self):
        kwargs = OpenAIResponsesAdapter._kwargs(self._prepared(ProviderProtocol.OPENAI_RESPONSES, "openai_responses", "high"), stream=False)
        self.assertFalse(kwargs["store"])
        self.assertIn("input", kwargs)
        self.assertEqual("high", kwargs["reasoning"]["effort"])
        self.assertNotIn("messages", kwargs)

    def test_ollama_native_maps_context_and_thinking(self):
        request = self._prepared(ProviderProtocol.OLLAMA_NATIVE, "ollama", "high")
        request.model_profile.runtime_context = 65536
        payload = OllamaNativeAdapter._payload(request, stream=False)
        self.assertEqual("high", payload["think"])
        self.assertEqual(65536, payload["options"]["num_ctx"])
        self.assertEqual(1024, payload["options"]["num_predict"])


class GatewayTests(unittest.TestCase):
    def test_retryable_failure_uses_explicit_fallback(self):
        config = config_with_models(ProviderProtocol.OPENAI_CHAT, ProviderProtocol.OLLAMA_NATIVE)
        for provider in config.providers.values():
            provider.max_retries = 0
        first = FakeAdapter(ProviderProtocol.OPENAI_CHAT, [TimeoutError("timeout")])
        second = FakeAdapter(ProviderProtocol.OLLAMA_NATIVE, [ModelResult(content="fallback", usage=Usage(total_tokens=3))])
        gateway = ModelGateway(config, adapters={
            ProviderProtocol.OPENAI_CHAT: first,
            ProviderProtocol.OLLAMA_NATIVE: second,
        })
        # Built-in TimeoutError is normalized to a gateway timeout in production;
        # use httpx timeout here to exercise the classifier.
        import httpx
        first.outcomes = [httpx.ReadTimeout("timeout")]
        result = gateway.complete(ModelRequest("chat", TaskStage.INTERACTIVE, [{"role": "user", "content": "hello"}]))
        self.assertEqual("fallback", result.content)
        self.assertEqual(2, len(result.attempts))
        self.assertFalse(result.attempts[0].success)
        self.assertTrue(result.attempts[1].success)

    def test_auth_error_does_not_fallback(self):
        config = config_with_models(ProviderProtocol.OPENAI_CHAT, ProviderProtocol.OLLAMA_NATIVE)
        error = RuntimeError("unauthorized")
        error.status_code = 401
        first = FakeAdapter(ProviderProtocol.OPENAI_CHAT, [error])
        second = FakeAdapter(ProviderProtocol.OLLAMA_NATIVE, [ModelResult(content="must not run")])
        gateway = ModelGateway(config, adapters={ProviderProtocol.OPENAI_CHAT: first, ProviderProtocol.OLLAMA_NATIVE: second})
        with self.assertRaises(RuntimeError):
            gateway.complete(ModelRequest("chat", TaskStage.INTERACTIVE, [{"role": "user", "content": "hello"}]))
        self.assertFalse(second.requests)

    def test_current_service_retries_before_fallback(self):
        config = config_with_models(ProviderProtocol.OPENAI_CHAT)
        config.providers["p0"].max_retries = 1
        adapter = FakeAdapter(ProviderProtocol.OPENAI_CHAT, [TimeoutError("once"), ModelResult(content="retried")])
        gateway = ModelGateway(config, adapters={ProviderProtocol.OPENAI_CHAT: adapter})
        result = gateway.complete(ModelRequest("chat", TaskStage.INTERACTIVE, [{"role": "user", "content": "hello"}]))
        self.assertEqual("retried", result.content)
        self.assertEqual(2, len(adapter.requests))

    def test_on_demand_external_search_uses_bounded_read_only_tool(self):
        config = config_with_models(ProviderProtocol.OPENAI_CHAT)
        config.providers["p0"].max_retries = 0
        adapter = FakeAdapter(ProviderProtocol.OPENAI_CHAT, [
            ModelResult(tool_calls=[ToolCall("call-1", "web_search", {"query": "source"})]),
            ModelResult(content="answer"),
        ])
        searches = []
        gateway = ModelGateway(
            config,
            adapters={ProviderProtocol.OPENAI_CHAT: adapter},
            external_search=lambda query, limit: searches.append((query, limit)) or {
                "results": [{"title": "Doc", "url": "https://example.test/doc", "snippet": "fact"}]
            },
        )
        result = gateway.complete(ModelRequest(
            "chat", TaskStage.INTERACTIVE, [{"role": "user", "content": "hello"}],
            web_search="on_demand",
        ))
        self.assertEqual("answer", result.content)
        self.assertEqual([("source", 5)], searches)
        self.assertEqual("https://example.test/doc", result.citations[0].url)
        self.assertEqual("external", result.search_source)

    def test_undeclared_tool_is_rejected(self):
        config = config_with_models(ProviderProtocol.OPENAI_CHAT)
        config.providers["p0"].max_retries = 0
        adapter = FakeAdapter(ProviderProtocol.OPENAI_CHAT, [
            ModelResult(tool_calls=[ToolCall("call-1", "delete_book", {})]),
        ])
        gateway = ModelGateway(config, adapters={ProviderProtocol.OPENAI_CHAT: adapter})
        with self.assertRaises(ToolAuthorizationError):
            gateway.complete(ModelRequest(
                "agent_advisor", TaskStage.AGENT, [{"role": "user", "content": "hello"}],
                tools=[{"type": "function", "function": {"name": "read_book", "parameters": {"type": "object"}}}],
                tool_executor=lambda _call: "ok",
            ))

    def test_context_compaction_keeps_system_and_recent(self):
        messages = [{"role": "system", "content": "不可删除的系统约束"}]
        messages.extend({"role": "user", "content": f"old-{i} " * 300} for i in range(20))
        compacted, changed, removed = compact_messages(messages, 1800)
        self.assertTrue(changed)
        self.assertGreater(removed, 0)
        self.assertIn("不可删除的系统约束", compacted[0]["content"])
        self.assertIn("old-19", compacted[-1]["content"])

    def test_context_compaction_keeps_manual_reference(self):
        messages = [{"role": "system", "content": "system"}]
        messages.append({"role": "user", "content": "MANUAL-REFERENCE", "manual_reference": True})
        messages.extend({"role": "user", "content": f"old-{i} " * 300} for i in range(20))
        compacted, changed, _removed = compact_messages(messages, 1800)
        self.assertTrue(changed)
        self.assertTrue(any(item.get("content") == "MANUAL-REFERENCE" for item in compacted))

    def test_irreducible_context_is_blocked_before_adapter(self):
        config = config_with_models(ProviderProtocol.OPENAI_CHAT)
        config.models["m0"].context_window = 512
        config.models["m0"].max_output_tokens = 256
        adapter = FakeAdapter(ProviderProtocol.OPENAI_CHAT, [ModelResult(content="no")])
        gateway = ModelGateway(config, adapters={ProviderProtocol.OPENAI_CHAT: adapter})
        request = ModelRequest(
            "chat", TaskStage.INTERACTIVE,
            [{"role": "system", "content": "约束" * 3000}, {"role": "user", "content": "必须保留" * 1000}],
            max_output_tokens=128,
        )
        with self.assertRaises(ContextBudgetExceeded):
            gateway.complete(request)
        self.assertFalse(adapter.requests)


class GatewayStaticRegressionTests(unittest.TestCase):
    def test_text_generation_does_not_import_provider_sdks_outside_adapters(self):
        root = Path(__file__).resolve().parents[1]
        adapter = (root / "core" / "model_adapters.py").resolve()
        offenders = []
        for path in (root / "core").rglob("*.py"):
            if path.resolve() == adapter:
                continue
            source = path.read_text(encoding="utf-8")
            if "from openai import" in source or "import openai" in source or "ChatOpenAI" in source:
                offenders.append(str(path.relative_to(root)))
        self.assertEqual([], offenders)

    def test_production_code_no_longer_uses_deprecated_raw_client_calls(self):
        root = Path(__file__).resolve().parents[1]
        offenders = []
        for folder in ("core", "ui", "utils", "web", "strategies"):
            for path in (root / folder).rglob("*.py"):
                if "raw_client.chat.completions" in path.read_text(encoding="utf-8"):
                    offenders.append(str(path.relative_to(root)))
        self.assertEqual([], offenders)


if __name__ == "__main__":
    unittest.main()

import base64
import io
import unittest
from unittest.mock import patch

from core.image_reference import ImageReferenceService, image_parts
from core.model_adapters import ModelCapabilityError, OpenAIChatAdapter, OpenAIResponsesAdapter, OllamaNativeAdapter
from core.model_config import ModelConfig
from core.model_gateway import ModelGateway, estimate_messages_tokens
from core.model_types import ModelCapabilities, ModelProfile, ModelResult, ProviderProfile, RoutePolicy, TaskStage
from tests.test_model_adapters import prepared
from core.model_types import ProviderProtocol


PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=")


class Adapter:
    def __init__(self):
        self.requests = []

    def complete(self, request):
        self.requests.append(request)
        return ModelResult(content="白色墙面，光线柔和。")


class ImageReferenceTests(unittest.TestCase):
    def gateway(self, vision=True):
        model = ModelProfile("m", "p", "测试模型", "visual-model", capabilities=ModelCapabilities(vision=vision))
        config = ModelConfig(providers={"p": ProviderProfile("p", "服务", base_url="https://test.invalid/v1")}, models={"m": model}, routes={stage.value: RoutePolicy(primary_model_id="m") for stage in TaskStage})
        self.adapter = Adapter()
        return ModelGateway(config, adapters={ProviderProtocol.OPENAI_CHAT: self.adapter})

    def describe(self, gateway):
        with patch("pathlib.Path.open", return_value=io.BytesIO(PNG)):
            return ImageReferenceService(gateway).describe(["reference.png"])

    def test_native_vision_uses_current_model_and_preserves_config(self):
        gateway = self.gateway()
        gateway.config.image = {"base_url": "https://fallback.invalid", "model": "other"}
        before = gateway.config.to_dict()
        result = self.describe(gateway)
        self.assertEqual("current_model", result["source"])
        self.assertEqual("visual-model", result["model"])
        self.assertEqual(before, gateway.config.to_dict())
        self.assertTrue(self.adapter.requests[0].messages[-1]["content"][-1]["image_url"]["url"].startswith("data:image/png;base64,"))

    def test_text_model_uses_only_configured_visual_api(self):
        gateway = self.gateway(False)
        gateway.config.image = {"base_url": "https://fallback.invalid/v1", "api_key": "test-key", "model": "vision-backup"}
        result = self.describe(gateway)
        self.assertEqual("image_api", result["source"])
        self.assertEqual("vision-backup", result["model"])
        self.assertEqual("https://fallback.invalid/v1", self.adapter.requests[0].provider.base_url)

    def test_missing_visual_api_fails_before_model_call(self):
        gateway = self.gateway(False)
        with self.assertRaisesRegex(ValueError, "看图"):
            self.describe(gateway)
        self.assertEqual([], self.adapter.requests)

    def test_native_failure_does_not_send_images_to_backup(self):
        gateway = self.gateway()
        gateway.config.image = {"base_url": "https://fallback.invalid", "model": "backup"}
        with patch.object(self.adapter, "complete", side_effect=ValueError("bad request")) as call:
            with self.assertRaises(Exception):
                self.describe(gateway)
        self.assertEqual(1, call.call_count)

    def test_invalid_files_and_limits(self):
        for data in (b"plain text", b"x" * (10 * 1024 * 1024 + 1)):
            with patch("pathlib.Path.open", return_value=io.BytesIO(data)), self.assertRaises(ValueError):
                image_parts(["fake.png"])
        for paths in ([], ["a"] * 5):
            with self.assertRaises(ValueError):
                image_parts(paths)

    def test_image_payloads_are_converted_for_each_protocol(self):
        with patch("pathlib.Path.open", return_value=io.BytesIO(PNG)):
            parts = [{"type": "text", "text": "读图"}, *image_parts(["a.png"])]
        for protocol in ProviderProtocol:
            request = prepared(protocol)
            request.messages = [{"role": "user", "content": parts}]
            if protocol == ProviderProtocol.OPENAI_CHAT:
                self.assertEqual(parts, OpenAIChatAdapter._kwargs(request, stream=False)["messages"][0]["content"])
            elif protocol == ProviderProtocol.OPENAI_RESPONSES:
                content = OpenAIResponsesAdapter._kwargs(request, stream=False)["input"][0]["content"]
                self.assertEqual("input_image", content[1]["type"])
                self.assertEqual(parts[1]["image_url"]["url"], content[1]["image_url"])
            else:
                content = OllamaNativeAdapter._payload(request, stream=False)["messages"][0]
                self.assertEqual("读图", content["content"])
                self.assertEqual(PNG, base64.b64decode(content["images"][0]))

    def test_base64_is_not_counted_as_text_tokens(self):
        msg = [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "data:image/png;base64," + "a" * 1000000}}]}]
        self.assertLess(estimate_messages_tokens(msg), 5000)

    def test_book_route_selects_visual_model(self):
        gateway = self.gateway(False)
        gateway.config.models["v"] = ModelProfile("v", "p", "书籍视觉模型", "book-vision", capabilities=ModelCapabilities(vision=True))
        gateway.book_route_loader = lambda _: {"interactive": {"primary_model_id": "v", "inherit": False}}
        routed, source = ImageReferenceService(gateway)._gateway(TaskStage.INTERACTIVE, "书籍")
        self.assertEqual("current_model", source)
        self.assertEqual("v", routed.config.routes["interactive"].primary_model_id)


if __name__ == "__main__":
    unittest.main()

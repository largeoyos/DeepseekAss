"""Versioned multi-provider configuration and six-stage routing."""
from __future__ import annotations

import hashlib
import ipaddress
from dataclasses import dataclass, field
from urllib.parse import urlparse

from config import Config
from core.model_types import (
    ModelCapabilities,
    ModelDefaults,
    ModelProfile,
    ProviderProfile,
    ProviderProtocol,
    ReasoningLevel,
    RoutePolicy,
    TaskStage,
    WebSearchPolicy,
)

MODEL_CONFIG_SCHEMA_VERSION = 2


STAGE_LABELS = {
    TaskStage.INTERACTIVE: "互动对话",
    TaskStage.DRAFTING: "正文生成",
    TaskStage.PLANNING: "规划分析",
    TaskStage.EXTRACTION: "摘要提取",
    TaskStage.REVIEW: "审校润色",
    TaskStage.AGENT: "Agent 工具",
}


OPERATION_STAGES: dict[str, TaskStage] = {
    "chat": TaskStage.INTERACTIVE,
    "roleplay_chat": TaskStage.INTERACTIVE,
    "roleplay_regenerate": TaskStage.INTERACTIVE,
    "novel_chapter": TaskStage.DRAFTING,
    "continuation": TaskStage.DRAFTING,
    "chapter_regenerate": TaskStage.DRAFTING,
    "chapter_tree_rewrite": TaskStage.DRAFTING,
    "agent_extra_generation": TaskStage.DRAFTING,
    "supplement": TaskStage.DRAFTING,
    "initial_novel_settings": TaskStage.PLANNING,
    "continuation_import_analysis": TaskStage.PLANNING,
    "batch_import_analysis": TaskStage.PLANNING,
    "continuation_segment": TaskStage.PLANNING,
    "agent_continuation_segment": TaskStage.PLANNING,
    "continuation_suggest": TaskStage.PLANNING,
    "agent_chapter_plan": TaskStage.PLANNING,
    "agent_chapter_plan_revision": TaskStage.PLANNING,
    "agent_extra_plan": TaskStage.PLANNING,
    "agent_extra_prompt": TaskStage.PLANNING,
    "agent_story_director": TaskStage.PLANNING,
    "novel_summary": TaskStage.EXTRACTION,
    "continuation_summary": TaskStage.EXTRACTION,
    "chapter_tree_summary": TaskStage.EXTRACTION,
    "novel_context_summary": TaskStage.EXTRACTION,
    "continuation_context_summary": TaskStage.EXTRACTION,
    "agent_extra_summary": TaskStage.EXTRACTION,
    "world_bible_update": TaskStage.EXTRACTION,
    "chapter_tree_world_bible": TaskStage.EXTRACTION,
    "agent_extra_world_bible": TaskStage.EXTRACTION,
    "character_book_update": TaskStage.EXTRACTION,
    "style_profile_extract": TaskStage.EXTRACTION,
    "chapter_tree_polish": TaskStage.REVIEW,
    "agent_chapter_polish": TaskStage.REVIEW,
    "agent_chapter_polish_plan": TaskStage.REVIEW,
    "agent_chapter_polish_review": TaskStage.REVIEW,
    "world_bible_agent_detail": TaskStage.AGENT,
    "agent_advisor": TaskStage.AGENT,
    "agent_advisor_library": TaskStage.AGENT,
    "agent_advisor_history": TaskStage.AGENT,
    "agent_advisor_save": TaskStage.AGENT,
    "agent_world_maintenance": TaskStage.AGENT,
    "agent_world_maintenance_retry": TaskStage.AGENT,
    "agent_chapter_prompt": TaskStage.AGENT,
}


PREFIX_OPERATION_STAGES: tuple[tuple[str, TaskStage], ...] = (
    ("agent_", TaskStage.AGENT),
    ("world_bible_", TaskStage.EXTRACTION),
    ("chapter_tree_world_bible", TaskStage.EXTRACTION),
    ("chapter_tree_summary", TaskStage.EXTRACTION),
    ("novel_summary", TaskStage.EXTRACTION),
    ("continuation_summary", TaskStage.EXTRACTION),
    ("novel_context_summary", TaskStage.EXTRACTION),
    ("continuation_context_summary", TaskStage.EXTRACTION),
    ("chapter_tree_polish", TaskStage.REVIEW),
    ("chapter_tree_rewrite", TaskStage.DRAFTING),
)


def stage_for_operation(operation: str, default: TaskStage | None = None) -> TaskStage:
    operation = str(operation or "").strip()
    if operation in OPERATION_STAGES:
        return OPERATION_STAGES[operation]
    if "continuity" in operation or "supervision" in operation or "style_rerank" in operation:
        return TaskStage.REVIEW
    for prefix, stage in PREFIX_OPERATION_STAGES:
        if operation.startswith(prefix):
            return stage
    if default is not None:
        return default
    raise ValueError(f"未登记的模型操作：{operation or '(empty)'}")


def _stable_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("\x1f".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:12]
    return f"{prefix}_{digest}"


def _is_loopback_host(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def validate_provider(provider: ProviderProfile) -> list[str]:
    errors: list[str] = []
    parsed = urlparse(provider.base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        errors.append("调用地址必须是完整的 http/https URL")
    if parsed.scheme == "http" and parsed.hostname and not _is_loopback_host(parsed.hostname):
        if not provider.allow_insecure_http:
            errors.append("非本机 HTTP 连接必须显式允许不安全传输")
    if provider.protocol != ProviderProtocol.OLLAMA_NATIVE and not provider.api_key.strip():
        # OpenAI-compatible local servers commonly ignore the key, so this is a warning
        # represented by UI state instead of a hard validation failure.
        pass
    if provider.timeout_seconds <= 0:
        errors.append("超时必须大于 0 秒")
    if provider.max_retries < 0:
        errors.append("重试次数不能为负数")
    return errors


RESERVED_EXTRA_BODY_KEYS = {
    "model", "messages", "input", "instructions", "stream", "stream_options",
    "tools", "tool_choice", "max_tokens", "max_completion_tokens", "max_output_tokens",
    "temperature", "top_p", "frequency_penalty", "reasoning", "reasoning_effort",
    "thinking", "think", "web_search_options", "store", "authorization", "headers",
}


def sanitize_extra_body(extra_body: dict | None) -> dict:
    return {
        str(key): value
        for key, value in dict(extra_body or {}).items()
        if str(key) not in RESERVED_EXTRA_BODY_KEYS
    }


@dataclass
class ModelConfig:
    schema_version: int = MODEL_CONFIG_SCHEMA_VERSION
    providers: dict[str, ProviderProfile] = field(default_factory=dict)
    models: dict[str, ModelProfile] = field(default_factory=dict)
    routes: dict[str, RoutePolicy] = field(default_factory=dict)
    image: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: dict | None, settings: dict | None = None) -> "ModelConfig":
        data = dict(raw or {})
        if int(data.get("schema_version") or 0) < MODEL_CONFIG_SCHEMA_VERSION or "providers" not in data:
            return migrate_legacy_config(data, settings or {})
        providers = {
            key: ProviderProfile.from_dict({"provider_id": key, **dict(value or {})})
            for key, value in dict(data.get("providers") or {}).items()
        }
        models = {
            key: ModelProfile.from_dict({"model_id": key, **dict(value or {})})
            for key, value in dict(data.get("models") or {}).items()
        }
        routes = {
            stage.value: RoutePolicy.from_dict(dict(data.get("routes") or {}).get(stage.value))
            for stage in TaskStage
        }
        config = cls(MODEL_CONFIG_SCHEMA_VERSION, providers, models, routes, dict(data.get("image") or {}))
        config.ensure_valid_routes()
        return config

    def to_dict(self) -> dict:
        return {
            "schema_version": MODEL_CONFIG_SCHEMA_VERSION,
            "providers": {key: value.to_dict() for key, value in self.providers.items()},
            "models": {key: value.to_dict() for key, value in self.models.items()},
            "routes": {key: value.to_dict() for key, value in self.routes.items()},
            "image": dict(self.image or {}),
        }

    def ensure_valid_routes(self) -> None:
        enabled = [model.model_id for model in self.models.values() if model.enabled]
        fallback_default = enabled[0] if enabled else ""
        for stage in TaskStage:
            route = self.routes.setdefault(stage.value, RoutePolicy(primary_model_id=fallback_default))
            if route.primary_model_id not in self.models:
                route.primary_model_id = fallback_default
            route.fallback_model_ids = [
                model_id for model_id in route.fallback_model_ids
                if model_id in self.models and model_id != route.primary_model_id
            ]

    def effective_route(self, stage: TaskStage, book_routes: dict | None = None) -> RoutePolicy:
        global_route = self.routes.get(stage.value, RoutePolicy())
        raw_book = dict(book_routes or {}).get(stage.value)
        if not raw_book:
            return RoutePolicy.from_dict(global_route.to_dict())
        book_route = RoutePolicy.from_dict(raw_book)
        if book_route.inherit or not book_route.primary_model_id:
            return RoutePolicy.from_dict(global_route.to_dict())
        return book_route

    def legacy_text_config(self) -> dict:
        route = self.routes.get(TaskStage.INTERACTIVE.value, RoutePolicy())
        model = self.models.get(route.primary_model_id)
        provider = self.providers.get(model.provider_id) if model else None
        if not model or not provider:
            return {"api_key": "", "base_url": "", "model": ""}
        return {"api_key": provider.api_key, "base_url": provider.base_url, "model": model.model}


def _profile_for_model(provider_id: str, model_name: str, base_url: str, *, protocol: ProviderProtocol) -> ModelProfile:
    lower_url = base_url.lower()
    lower_model = model_name.lower()
    is_deepseek = "deepseek" in lower_url or lower_model.startswith("deepseek-")
    if protocol == ProviderProtocol.OPENAI_RESPONSES:
        template = "openai_responses"
        capabilities = ModelCapabilities(
            streaming=True, tools=True, native_web_search=True, reasoning=True,
            structured_output=True,
            supported_reasoning_levels=[item.value for item in ReasoningLevel],
        )
    elif protocol == ProviderProtocol.OLLAMA_NATIVE:
        template = "ollama"
        capabilities = ModelCapabilities(
            streaming=True, tools=True, native_web_search=False, reasoning=True,
            structured_output=True,
            supported_reasoning_levels=["off", "low", "medium", "high", "max"],
        )
    elif is_deepseek:
        template = "deepseek"
        capabilities = ModelCapabilities(
            streaming=True, tools=True, native_web_search=False, reasoning=True,
            structured_output=True,
            supported_reasoning_levels=["off", "high", "max"],
        )
    else:
        template = "generic"
        capabilities = ModelCapabilities(
            streaming=True, tools=True, native_web_search=False, reasoning=False,
            structured_output=True, supported_reasoning_levels=["off"],
        )
    context_window = 1_000_000 if is_deepseek and "v4" in lower_model else 32768
    defaults = ModelDefaults(
        temperature=0.7, top_p=0.9, frequency_penalty=0.0,
        max_output_tokens=32768,
        reasoning_level="high" if is_deepseek and "v4" in lower_model else "off",
        web_search=WebSearchPolicy.OFF.value,
    )
    support = {
        "temperature": True,
        "top_p": True,
        "frequency_penalty": not is_deepseek and protocol != ProviderProtocol.OLLAMA_NATIVE,
        "max_tokens": True,
    }
    return ModelProfile(
        model_id=_stable_id("model", provider_id, model_name),
        provider_id=provider_id,
        name=model_name,
        model=model_name,
        context_window=context_window,
        max_output_tokens=32768,
        template=template,
        capabilities=capabilities,
        defaults=defaults,
        parameter_support=support,
        runtime_context=32768 if protocol == ProviderProtocol.OLLAMA_NATIVE else None,
    )


def migrate_legacy_config(raw: dict | None, settings: dict | None = None) -> ModelConfig:
    data = dict(raw or {})
    settings = dict(settings or {})
    if "text" in data:
        text = dict(data.get("text") or {})
        image = dict(data.get("image") or {})
    else:
        text = {
            "api_key": data.get("api_key", Config.API_KEY),
            "base_url": data.get("base_url", Config.BASE_URL),
            "model": data.get("model") or settings.get("last_model") or Config.MODEL_V4_FLASH,
        }
        image = {
            "api_key": data.get("image_api_key", Config.IMAGE_API_KEY),
            "base_url": data.get("image_base_url", Config.IMAGE_BASE_URL),
            "model": data.get("image_model", Config.IMAGE_MODEL),
        }
    base_url = str(text.get("base_url") or Config.BASE_URL).rstrip("/")
    provider_id = _stable_id("provider", base_url, "openai_chat")
    provider_name = "DeepSeek" if "deepseek" in base_url.lower() else "原有模型服务"
    provider = ProviderProfile(
        provider_id=provider_id,
        name=provider_name,
        protocol=ProviderProtocol.OPENAI_CHAT,
        base_url=base_url,
        api_key=str(text.get("api_key") or ""),
        timeout_seconds=Config.API_TIMEOUT_SECONDS,
        max_retries=Config.API_MAX_RETRIES,
        allow_insecure_http=bool(urlparse(base_url).hostname and _is_loopback_host(urlparse(base_url).hostname or "")),
    )
    names: list[str] = []
    for value in [
        text.get("model"), settings.get("last_model"),
        *(settings.get("favorite_models") or []), *(settings.get("custom_models") or []),
    ]:
        name = str(value or "").strip()
        if name and name not in names:
            names.append(name)
    if not names:
        names.append(Config.MODEL_V4_FLASH)
    if str(settings.get("model_routing_mode") or "") == "pro_body_flash_aux":
        for name in (Config.MODEL_V4_FLASH, Config.MODEL_V4_PRO):
            if name not in names:
                names.append(name)
    models = {}
    by_name = {}
    for name in names:
        profile = _profile_for_model(provider_id, name, base_url, protocol=ProviderProtocol.OPENAI_CHAT)
        models[profile.model_id] = profile
        by_name[name] = profile.model_id
    selected_name = str(text.get("model") or settings.get("last_model") or names[0])
    selected_id = by_name.get(selected_name, next(iter(models)))
    routes = {stage.value: RoutePolicy(primary_model_id=selected_id) for stage in TaskStage}
    if str(settings.get("model_routing_mode") or "") == "pro_body_flash_aux":
        flash_id = by_name.get(Config.MODEL_V4_FLASH, selected_id)
        pro_id = by_name.get(Config.MODEL_V4_PRO, selected_id)
        routes = {stage.value: RoutePolicy(primary_model_id=flash_id) for stage in TaskStage}
        routes[TaskStage.DRAFTING.value] = RoutePolicy(primary_model_id=pro_id, fallback_model_ids=[flash_id])
    return ModelConfig(MODEL_CONFIG_SCHEMA_VERSION, {provider_id: provider}, models, routes, image)


def create_provider_template(template: str) -> tuple[ProviderProfile, ModelProfile]:
    template = str(template or "generic").strip().lower()
    if template == "ollama":
        protocol = ProviderProtocol.OLLAMA_NATIVE
        base_url, provider_name, model_name = "http://127.0.0.1:11434", "本地 Ollama", "qwen3"
        api_key = ""
    elif template == "openai_responses":
        protocol = ProviderProtocol.OPENAI_RESPONSES
        base_url, provider_name, model_name = "https://api.openai.com/v1", "OpenAI Responses", "gpt-5"
        api_key = ""
    elif template == "deepseek":
        protocol = ProviderProtocol.OPENAI_CHAT
        base_url, provider_name, model_name = "https://api.deepseek.com", "DeepSeek", Config.MODEL_V4_FLASH
        api_key = ""
    else:
        protocol = ProviderProtocol.OPENAI_CHAT
        base_url, provider_name, model_name = "https://api.example.com/v1", "OpenAI 兼容服务", "model-name"
        api_key = ""
    provider_id = _stable_id("provider", base_url, protocol.value, provider_name)
    provider = ProviderProfile(
        provider_id, provider_name, protocol, base_url, api_key,
        allow_insecure_http=(protocol == ProviderProtocol.OLLAMA_NATIVE),
    )
    return provider, _profile_for_model(provider_id, model_name, base_url, protocol=protocol)

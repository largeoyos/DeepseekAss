"""Provider-neutral model request, response, and routing contracts."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Callable, Iterator


class ProviderProtocol(str, Enum):
    OPENAI_CHAT = "openai_chat"
    OPENAI_RESPONSES = "openai_responses"
    OLLAMA_NATIVE = "ollama_native"


class TaskStage(str, Enum):
    INTERACTIVE = "interactive"
    DRAFTING = "drafting"
    PLANNING = "planning"
    EXTRACTION = "extraction"
    REVIEW = "review"
    AGENT = "agent"


class ReasoningLevel(str, Enum):
    OFF = "off"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    XHIGH = "xhigh"
    MAX = "max"


class WebSearchPolicy(str, Enum):
    OFF = "off"
    ON_DEMAND = "on_demand"
    ALWAYS = "always"


@dataclass
class ProviderProfile:
    provider_id: str
    name: str
    protocol: ProviderProtocol = ProviderProtocol.OPENAI_CHAT
    base_url: str = ""
    api_key: str = ""
    auth_header: str = "Authorization"
    auth_prefix: str = "Bearer "
    headers: dict[str, str] = field(default_factory=dict)
    timeout_seconds: float = 180.0
    max_retries: int = 1
    enabled: bool = True
    allow_insecure_http: bool = False

    @classmethod
    def from_dict(cls, data: dict) -> "ProviderProfile":
        raw = dict(data or {})
        try:
            raw["protocol"] = ProviderProtocol(raw.get("protocol", ProviderProtocol.OPENAI_CHAT.value))
        except ValueError:
            raw["protocol"] = ProviderProtocol.OPENAI_CHAT
        allowed = cls.__dataclass_fields__.keys()
        return cls(**{key: raw[key] for key in allowed if key in raw})

    def to_dict(self) -> dict:
        value = asdict(self)
        value["protocol"] = self.protocol.value
        return value


@dataclass
class ModelCapabilities:
    streaming: bool = True
    tools: bool = True
    native_web_search: bool = False
    reasoning: bool = False
    structured_output: bool = True
    supported_reasoning_levels: list[str] = field(default_factory=lambda: [ReasoningLevel.OFF.value])
    verified: bool = False

    @classmethod
    def from_dict(cls, data: dict | None) -> "ModelCapabilities":
        raw = dict(data or {})
        allowed = cls.__dataclass_fields__.keys()
        return cls(**{key: raw[key] for key in allowed if key in raw})


@dataclass
class ModelDefaults:
    temperature: float | None = 0.7
    top_p: float | None = 0.9
    frequency_penalty: float | None = 0.0
    max_output_tokens: int = 16384
    reasoning_level: str = ReasoningLevel.OFF.value
    web_search: str = WebSearchPolicy.OFF.value

    @classmethod
    def from_dict(cls, data: dict | None) -> "ModelDefaults":
        raw = dict(data or {})
        allowed = cls.__dataclass_fields__.keys()
        return cls(**{key: raw[key] for key in allowed if key in raw})


@dataclass
class ModelProfile:
    model_id: str
    provider_id: str
    name: str
    model: str
    context_window: int = 32768
    max_output_tokens: int = 32768
    template: str = "generic"
    capabilities: ModelCapabilities = field(default_factory=ModelCapabilities)
    defaults: ModelDefaults = field(default_factory=ModelDefaults)
    parameter_support: dict[str, bool] = field(default_factory=lambda: {
        "temperature": True,
        "top_p": True,
        "frequency_penalty": True,
        "max_tokens": True,
    })
    extra_body: dict[str, Any] = field(default_factory=dict)
    runtime_context: int | None = None
    enabled: bool = True

    @classmethod
    def from_dict(cls, data: dict) -> "ModelProfile":
        raw = dict(data or {})
        raw["capabilities"] = ModelCapabilities.from_dict(raw.get("capabilities"))
        raw["defaults"] = ModelDefaults.from_dict(raw.get("defaults"))
        allowed = cls.__dataclass_fields__.keys()
        return cls(**{key: raw[key] for key in allowed if key in raw})

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class RouteOverrides:
    reasoning_level: str | None = None
    web_search: str | None = None
    context_window: int | None = None
    max_output_tokens: int | None = None
    temperature: float | None = None
    top_p: float | None = None
    frequency_penalty: float | None = None

    @classmethod
    def from_dict(cls, data: dict | None) -> "RouteOverrides":
        raw = dict(data or {})
        allowed = cls.__dataclass_fields__.keys()
        return cls(**{key: raw[key] for key in allowed if key in raw})


@dataclass
class RoutePolicy:
    primary_model_id: str = ""
    fallback_model_ids: list[str] = field(default_factory=list)
    overrides: RouteOverrides = field(default_factory=RouteOverrides)
    inherit: bool = False

    @classmethod
    def from_dict(cls, data: dict | None) -> "RoutePolicy":
        raw = dict(data or {})
        raw["overrides"] = RouteOverrides.from_dict(raw.get("overrides"))
        allowed = cls.__dataclass_fields__.keys()
        return cls(**{key: raw[key] for key in allowed if key in raw})

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ToolCall:
    call_id: str
    name: str
    arguments: dict = field(default_factory=dict)


@dataclass
class Citation:
    title: str = ""
    url: str = ""
    snippet: str = ""
    source_type: str = "web"


@dataclass
class Usage:
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    reasoning_tokens: int | None = None
    cached_tokens: int | None = None

    def to_dict(self) -> dict:
        return {key: value for key, value in asdict(self).items() if value is not None}


@dataclass
class AttemptRecord:
    model_id: str
    provider_id: str
    model: str
    protocol: str
    success: bool
    error: str = ""
    fallback_reason: str = ""


@dataclass
class ContextBudgetReport:
    context_window: int
    max_output_tokens: int
    safety_reserve: int
    input_budget: int
    estimated_input_tokens: int
    compacted: bool = False
    removed_messages: int = 0
    blocked: bool = False
    message: str = ""


ToolExecutor = Callable[[ToolCall], Any]


@dataclass
class ModelRequest:
    operation: str
    stage: TaskStage
    messages: list[dict]
    book_title: str = ""
    temperature: float | None = None
    top_p: float | None = None
    frequency_penalty: float | None = None
    max_output_tokens: int | None = None
    reasoning_level: str | None = None
    web_search: str | None = None
    tools: list[dict] = field(default_factory=list)
    tool_choice: Any = None
    tool_executor: ToolExecutor | None = None
    max_tool_rounds: int = 3
    required_capabilities: set[str] = field(default_factory=set)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class PreparedRequest:
    operation: str
    stage: TaskStage
    provider: ProviderProfile
    model_profile: ModelProfile
    messages: list[dict]
    temperature: float | None
    top_p: float | None
    frequency_penalty: float | None
    max_output_tokens: int
    reasoning_level: str
    web_search: str
    tools: list[dict] = field(default_factory=list)
    tool_choice: Any = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ModelResult:
    content: str = ""
    reasoning_content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    citations: list[Citation] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    finish_reason: str = ""
    provider_id: str = ""
    model_id: str = ""
    model: str = ""
    protocol: str = ""
    stage: str = ""
    operation: str = ""
    reasoning_level: str = ""
    web_search: str = "off"
    search_source: str = ""
    attempts: list[AttemptRecord] = field(default_factory=list)
    context_report: ContextBudgetReport | None = None
    raw: Any = None


@dataclass
class StreamEvent:
    event_type: str
    text: str = ""
    result: ModelResult | None = None
    data: dict[str, Any] = field(default_factory=dict)


StreamFactory = Callable[[ModelRequest], Iterator[StreamEvent]]

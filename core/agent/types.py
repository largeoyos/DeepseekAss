from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Literal

AGENT_SCHEMA_VERSION = 1
AgentKind = Literal[
    "writing_orchestrator",
    "writing_advisor",
    "chapter_supervisor",
    "world_bible_manager",
    "continuity_editor",
    "roleplay_director",
    "project_maintainer",
]
AgentRunStatus = Literal["queued", "running", "waiting_approval", "paused", "completed", "failed", "cancelled"]
PermissionLevel = Literal["read_only", "draft_write", "confirmed_write"]


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


@dataclass(frozen=True)
class AgentProfile:
    agent_kind: AgentKind
    display_name: str
    system_prompt_id: str
    allowed_tools: list[str]
    permission_level: PermissionLevel
    max_iterations: int = 30
    context_budget: int = 60000
    model_profile_id: str = "openai_compatible"


@dataclass
class AgentRunRequest:
    book_id: str
    session_id: str
    agent_kind: AgentKind
    user_message: str
    manual_references: list[str] = field(default_factory=list)
    model: str = ""
    mode: str = "agent"
    book_title: str = ""
    request_id: str = field(default_factory=lambda: f"request_{uuid.uuid4().hex}")


@dataclass
class AgentEvent:
    run_id: str
    sequence: int
    event_type: str
    timestamp: str = field(default_factory=now_iso)
    payload: dict = field(default_factory=dict)


@dataclass
class ToolCallRequest:
    call_id: str
    tool_name: str
    arguments: dict = field(default_factory=dict)


@dataclass
class ToolResult:
    call_id: str
    success: bool
    content: str = ""
    structured_data: dict = field(default_factory=dict)
    error_code: str | None = None
    artifact_id: str | None = None


@dataclass
class ChangeOperation:
    operation_id: str
    operation: str
    target_type: str
    target_id: str
    payload: dict = field(default_factory=dict)
    expected_checksum: str = ""
    status: str = "pending"
    error: str = ""


@dataclass
class ChangeSet:
    change_set_id: str
    run_id: str
    book_id: str
    operations: list[ChangeOperation] = field(default_factory=list)
    validation_result: dict = field(default_factory=dict)
    status: str = "pending"
    reason: str = ""
    created_at: str = field(default_factory=now_iso)
    schema_version: int = AGENT_SCHEMA_VERSION


@dataclass
class AgentSession:
    session_id: str
    book_id: str
    book_title: str
    agent_kind: AgentKind
    title: str
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)
    active_run_id: str = ""
    run_ids: list[str] = field(default_factory=list)
    messages: list[dict] = field(default_factory=list)
    epochs: list[dict] = field(default_factory=list)
    schema_version: int = AGENT_SCHEMA_VERSION


def append_request_user_message(session: AgentSession, request: AgentRunRequest) -> bool:
    """Append a request's user message once, including across backend fallback."""
    if any(
        item.get("role") == "user"
        and item.get("request_id") == request.request_id
        for item in session.messages
    ):
        return False
    session.messages.append({
        "role": "user",
        "content": request.user_message,
        "request_id": request.request_id,
        "at": now_iso(),
    })
    return True


@dataclass
class AgentRun:
    run_id: str
    session_id: str
    book_id: str
    book_title: str
    agent_kind: AgentKind
    model: str
    status: AgentRunStatus = "queued"
    mode: str = "agent"
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)
    iteration: int = 0
    planning_only: bool = False
    messages: list[dict] = field(default_factory=list)
    todo: list[dict] = field(default_factory=list)
    tool_calls: list[dict] = field(default_factory=list)
    artifact_ids: list[str] = field(default_factory=list)
    change_set_ids: list[str] = field(default_factory=list)
    usage: dict = field(default_factory=dict)
    error: str = ""
    terminal_reason: str = ""
    schema_version: int = AGENT_SCHEMA_VERSION


def to_dict(value) -> dict:
    return asdict(value)

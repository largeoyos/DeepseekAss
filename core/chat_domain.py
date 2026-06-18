"""Structured role-play chat domain models and compatibility helpers."""
import copy
import json
import os
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def filter_fields(cls, data: dict) -> dict:
    return {key: value for key, value in (data or {}).items() if key in cls.__dataclass_fields__}


@dataclass
class ChatMessage:
    message_id: str = ""
    branch_id: str = "main"
    role: str = "assistant"
    speaker_id: str = ""
    speaker_name: str = ""
    content: str = ""
    action: str = ""
    turn_index: int = 0
    created_at: str = ""
    source_message_id: str = ""


@dataclass
class CharacterKnowledge:
    knowledge_id: str = ""
    character_id: str = ""
    fact: str = ""
    awareness: str = "witnessed"  # witnessed/heard/inferred/unknown
    source_message_id: str = ""
    confidence: float = 1.0
    learned_at: str = ""
    branch_id: str = "main"


@dataclass
class RelationshipState:
    source_character_id: str = ""
    target_id: str = ""
    trust: int = 0
    affection: int = 0
    hostility: int = 0
    vigilance: int = 0
    description: str = ""
    reason: str = ""
    source_message_id: str = ""
    updated_at: str = ""


@dataclass
class MemoryChange:
    change_id: str = ""
    character_id: str = ""
    field_name: str = ""
    old_value: object = None
    new_value: object = None
    risk: str = "low"  # low/high
    reason: str = ""


@dataclass
class MemoryChangeSet:
    change_set_id: str = ""
    branch_id: str = "main"
    source_message_ids: list[str] = field(default_factory=list)
    changes: list[MemoryChange] = field(default_factory=list)
    status: str = "pending"  # pending/applied/rejected/reverted
    created_at: str = ""
    applied_at: str = ""


@dataclass
class SceneState:
    time: str = ""
    location: str = ""
    weather: str = ""
    present_character_ids: list[str] = field(default_factory=list)
    objective: str = ""
    description: str = ""
    tags: list[str] = field(default_factory=list)


@dataclass
class TurnPolicy:
    required_speaker_ids: list[str] = field(default_factory=list)
    allowed_speaker_ids: list[str] = field(default_factory=list)
    blocked_speaker_ids: list[str] = field(default_factory=list)
    speaker_order: list[str] = field(default_factory=list)
    max_speakers: int = 0
    mention_only_ids: list[str] = field(default_factory=list)


@dataclass
class SenderProfile:
    sender_profile_id: str = ""
    name: str = ""
    identity: str = ""
    personality: str = ""
    appearance: str = ""
    background: str = ""
    relationships: str = ""
    knowledge_state: str = ""
    notes: str = ""
    created_at: str = ""
    updated_at: str = ""


@dataclass
class ConversationBranch:
    branch_id: str = "main"
    title: str = "主线"
    parent_branch_id: str = ""
    fork_message_id: str = ""
    messages: list[ChatMessage] = field(default_factory=list)
    timeline: list[dict] = field(default_factory=list)
    character_state_snapshot: dict = field(default_factory=dict)
    knowledge: list[CharacterKnowledge] = field(default_factory=list)
    relationships: list[RelationshipState] = field(default_factory=list)
    created_at: str = ""


@dataclass
class ChatSessionState:
    branches: list[ConversationBranch] = field(default_factory=list)
    active_branch_id: str = "main"
    sender_profile_id: str = ""
    scene_state: SceneState = field(default_factory=SceneState)
    turn_policy: TurnPolicy = field(default_factory=TurnPolicy)
    memory_change_sets: list[MemoryChangeSet] = field(default_factory=list)
    narrator_enabled: bool = False
    consistency_warnings: list[str] = field(default_factory=list)

    def active_branch(self) -> ConversationBranch:
        branch = next((item for item in self.branches if item.branch_id == self.active_branch_id), None)
        if branch is None:
            branch = ConversationBranch(branch_id="main", title="主线", created_at=now_text())
            self.branches.append(branch)
            self.active_branch_id = branch.branch_id
        return branch


def message_from_dict(data: dict) -> ChatMessage:
    return ChatMessage(**filter_fields(ChatMessage, data))


def knowledge_from_dict(data: dict) -> CharacterKnowledge:
    return CharacterKnowledge(**filter_fields(CharacterKnowledge, data))


def relationship_from_dict(data: dict) -> RelationshipState:
    return RelationshipState(**filter_fields(RelationshipState, data))


def change_from_dict(data: dict) -> MemoryChange:
    return MemoryChange(**filter_fields(MemoryChange, data))


def change_set_from_dict(data: dict) -> MemoryChangeSet:
    base = filter_fields(MemoryChangeSet, data)
    base["changes"] = [change_from_dict(item) for item in data.get("changes", [])]
    return MemoryChangeSet(**base)


def branch_from_dict(data: dict) -> ConversationBranch:
    base = filter_fields(ConversationBranch, data)
    base["messages"] = [message_from_dict(item) for item in data.get("messages", [])]
    base["knowledge"] = [knowledge_from_dict(item) for item in data.get("knowledge", [])]
    base["relationships"] = [relationship_from_dict(item) for item in data.get("relationships", [])]
    return ConversationBranch(**base)


def state_from_dict(data: dict | None) -> ChatSessionState:
    data = data or {}
    state = ChatSessionState(
        branches=[branch_from_dict(item) for item in data.get("branches", [])],
        active_branch_id=data.get("active_branch_id", "main"),
        sender_profile_id=data.get("sender_profile_id", ""),
        scene_state=SceneState(**filter_fields(SceneState, data.get("scene_state", {}))),
        turn_policy=TurnPolicy(**filter_fields(TurnPolicy, data.get("turn_policy", {}))),
        memory_change_sets=[change_set_from_dict(item) for item in data.get("memory_change_sets", [])],
        narrator_enabled=bool(data.get("narrator_enabled", False)),
        consistency_warnings=list(data.get("consistency_warnings", [])),
    )
    state.active_branch()
    return state


def state_to_dict(state: ChatSessionState) -> dict:
    return asdict(state)


def legacy_messages_to_structured(
    messages: list[dict],
    branch_id: str = "main",
    sender_name: str = "你",
    assistant_name: str = "角色",
    name_to_id: dict[str, str] | None = None,
) -> list[ChatMessage]:
    result: list[ChatMessage] = []
    turn = 0
    for item in messages:
        role = item.get("role", "")
        if role == "system":
            continue
        if role == "user":
            turn += 1
            speaker_id = "sender"
            speaker_name = sender_name
        else:
            if name_to_id:
                parsed = parse_structured_reply(
                    str(item.get("content", "")),
                    branch_id,
                    turn,
                    name_to_id,
                )
                if parsed:
                    result.extend(parsed)
                    continue
            speaker_id = "assistant"
            speaker_name = assistant_name
        result.append(ChatMessage(
            message_id=new_id("msg"),
            branch_id=branch_id,
            role=role,
            speaker_id=speaker_id,
            speaker_name=speaker_name,
            content=str(item.get("content", "")),
            turn_index=turn,
            created_at=item.get("timestamp") or now_text(),
        ))
    return result


def fork_branch(state: ChatSessionState, fork_message_id: str, title: str = "") -> ConversationBranch:
    parent = state.active_branch()
    messages: list[ChatMessage] = []
    for message in parent.messages:
        messages.append(copy.deepcopy(message))
        if message.message_id == fork_message_id:
            break
    branch = ConversationBranch(
        branch_id=new_id("branch"),
        title=title or f"分支 {len(state.branches) + 1}",
        parent_branch_id=parent.branch_id,
        fork_message_id=fork_message_id,
        messages=messages,
        timeline=copy.deepcopy(parent.timeline),
        character_state_snapshot=copy.deepcopy(parent.character_state_snapshot),
        knowledge=copy.deepcopy(parent.knowledge),
        relationships=copy.deepcopy(parent.relationships),
        created_at=now_text(),
    )
    state.branches.append(branch)
    state.active_branch_id = branch.branch_id
    return branch


def parse_structured_reply(
    raw: str,
    branch_id: str,
    turn_index: int,
    name_to_id: dict[str, str],
) -> list[ChatMessage]:
    text = (raw or "").strip()
    parsed = None
    candidate = text
    if "```json" in candidate:
        candidate = candidate.split("```json", 1)[1].split("```", 1)[0]
    elif candidate.startswith("```"):
        candidate = candidate.split("```", 1)[1].split("```", 1)[0]
    try:
        parsed = json.loads(candidate)
    except (json.JSONDecodeError, TypeError):
        parsed = None
    entries = parsed.get("messages", []) if isinstance(parsed, dict) else parsed if isinstance(parsed, list) else []
    result: list[ChatMessage] = []
    for item in entries:
        if not isinstance(item, dict):
            continue
        speaker_name = str(item.get("speaker_name") or item.get("speaker") or "").strip()
        content = str(item.get("content", "")).strip()
        if not speaker_name or not content:
            continue
        result.append(ChatMessage(
            message_id=new_id("msg"),
            branch_id=branch_id,
            role="assistant",
            speaker_id=str(item.get("speaker_id") or name_to_id.get(speaker_name) or "narrator"),
            speaker_name=speaker_name,
            content=content,
            action=str(item.get("action", "")),
            turn_index=turn_index,
            created_at=now_text(),
        ))
    if result:
        return result

    names = sorted(name_to_id, key=len, reverse=True)
    labels = [*names, "旁白"]
    pattern = re.compile(r"(?m)^\s*(" + "|".join(re.escape(name) for name in labels) + r")\s*[：:]\s*")
    matches = list(pattern.finditer(text))
    if matches:
        for index, match in enumerate(matches):
            end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
            speaker_name = match.group(1)
            content = text[match.end():end].strip()
            if content:
                result.append(ChatMessage(
                    message_id=new_id("msg"),
                    branch_id=branch_id,
                    role="assistant",
                    speaker_id=name_to_id.get(speaker_name, "narrator"),
                    speaker_name=speaker_name,
                    content=content,
                    turn_index=turn_index,
                    created_at=now_text(),
                ))
    if not result and text:
        result.append(ChatMessage(
            message_id=new_id("msg"),
            branch_id=branch_id,
            role="assistant",
            speaker_id="assistant",
            speaker_name="角色",
            content=text,
            turn_index=turn_index,
            created_at=now_text(),
        ))
    return result


def structured_to_legacy_messages(messages: list[ChatMessage], system_prompt: str = "") -> list[dict]:
    result = [{"role": "system", "content": system_prompt}] if system_prompt else []
    current_assistant: list[str] = []
    for message in messages:
        if message.role == "user":
            if current_assistant:
                result.append({"role": "assistant", "content": "\n\n".join(current_assistant)})
                current_assistant = []
            result.append({"role": "user", "content": message.content, "timestamp": message.created_at})
        elif message.role == "assistant":
            prefix = f"{message.speaker_name}：" if message.speaker_name else ""
            current_assistant.append(prefix + message.content)
    if current_assistant:
        result.append({"role": "assistant", "content": "\n\n".join(current_assistant)})
    return result


def apply_memory_change_set(book, change_set: MemoryChangeSet) -> None:
    from core.character_book import find_memory, find_profile

    for change in change_set.changes:
        memory = find_memory(book, change.character_id)
        profile = find_profile(book, change.character_id)
        target = memory if memory is not None and hasattr(memory, change.field_name) else profile
        if target is not None and hasattr(target, change.field_name):
            setattr(target, change.field_name, copy.deepcopy(change.new_value))
    change_set.status = "applied"
    change_set.applied_at = now_text()


def revert_memory_change_set(book, change_set: MemoryChangeSet) -> None:
    from core.character_book import find_memory, find_profile

    for change in reversed(change_set.changes):
        memory = find_memory(book, change.character_id)
        profile = find_profile(book, change.character_id)
        target = memory if memory is not None and hasattr(memory, change.field_name) else profile
        if target is not None and hasattr(target, change.field_name):
            setattr(target, change.field_name, copy.deepcopy(change.old_value))
    change_set.status = "reverted"


class SenderProfileManager:
    def __init__(self, root_dir: str, crypto=None, enc_key: bytes | None = None) -> None:
        self._path = os.path.join(root_dir, "sender_profiles.json")
        self._crypto = crypto
        self._enc_key = enc_key

    def load(self) -> list[SenderProfile]:
        path = self._path + ".enc" if self._enc_key is not None else self._path
        if not os.path.exists(path):
            return []
        if self._enc_key is None:
            with open(path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        else:
            data = self._crypto.decrypt_json(self._enc_key, path) or {}
        return [SenderProfile(**filter_fields(SenderProfile, item)) for item in data.get("profiles", [])]

    def save(self, profiles: list[SenderProfile]) -> None:
        now = now_text()
        for profile in profiles:
            profile.sender_profile_id = profile.sender_profile_id or new_id("sender")
            profile.created_at = profile.created_at or now
            profile.updated_at = now
        data = {"profiles": [asdict(profile) for profile in profiles]}
        if self._enc_key is None:
            with open(self._path, "w", encoding="utf-8") as handle:
                json.dump(data, handle, ensure_ascii=False, indent=2)
        else:
            self._crypto.encrypt_json(self._enc_key, self._path + ".enc", data)

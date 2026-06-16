"""
Character book storage and extraction for role-play chat mode.

This module is intentionally separate from the novel world bible. Character
profiles are global user-created records; memories are accumulated from chat
turns; timelines remain bound to individual conversations.
"""
import json
import os
import re
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime


@dataclass
class CharacterProfile:
    character_id: str = ""
    name: str = ""
    aliases: list[str] = field(default_factory=list)
    identity: str = ""
    appearance: str = ""
    personality: str = ""
    speech_style: str = ""
    background: str = ""
    goals: str = ""
    boundaries: str = ""
    notes: str = ""
    created_at: str = ""
    updated_at: str = ""


@dataclass
class CharacterMemory:
    character_id: str = ""
    name: str = ""
    experiences: list[str] = field(default_factory=list)
    current_state: str = ""
    relationships: dict[str, str] = field(default_factory=dict)
    knowledge_state: str = ""
    recent_actions: list[str] = field(default_factory=list)
    emotion_and_goals: str = ""
    key_dialogues: list[str] = field(default_factory=list)
    fact_sources: list[dict] = field(default_factory=list)
    updated_at: str = ""


@dataclass
class ChatTimelineEntry:
    event_id: str = ""
    turn_index: int = 0
    event: str = ""
    participants: list[str] = field(default_factory=list)
    impact: str = ""
    source_message_range: str = ""
    created_at: str = ""


@dataclass
class CharacterBook:
    profiles: list[CharacterProfile] = field(default_factory=list)
    memories: list[CharacterMemory] = field(default_factory=list)


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _as_list(value) -> list[str]:
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str) and value.strip():
        return [v.strip() for v in re.split(r"[、,，;\n]+", value) if v.strip()]
    return []


def _filter_fields(cls, data: dict) -> dict:
    return {k: v for k, v in data.items() if k in cls.__dataclass_fields__}


def dict_to_character_book(data: dict | None) -> CharacterBook:
    data = data or {}
    return CharacterBook(
        profiles=[CharacterProfile(**_filter_fields(CharacterProfile, p)) for p in data.get("profiles", [])],
        memories=[CharacterMemory(**_filter_fields(CharacterMemory, m)) for m in data.get("memories", [])],
    )


def character_book_to_dict(book: CharacterBook) -> dict:
    return asdict(book)


def dict_to_timeline(data: list | None) -> list[ChatTimelineEntry]:
    return [ChatTimelineEntry(**_filter_fields(ChatTimelineEntry, item)) for item in (data or [])]


def timeline_to_dict(timeline: list[ChatTimelineEntry]) -> list[dict]:
    return [asdict(item) for item in timeline]


def find_profile(book: CharacterBook, character_id: str) -> CharacterProfile | None:
    return next((p for p in book.profiles if p.character_id == character_id), None)


def find_memory(book: CharacterBook, character_id: str) -> CharacterMemory | None:
    return next((m for m in book.memories if m.character_id == character_id), None)


def format_profile(profile: CharacterProfile) -> str:
    parts = [f"角色：{profile.name}"]
    if profile.aliases:
        parts.append(f"别名：{'、'.join(profile.aliases)}")
    for label, value in (
        ("身份", profile.identity),
        ("外貌", profile.appearance),
        ("性格", profile.personality),
        ("说话风格", profile.speech_style),
        ("背景", profile.background),
        ("目标", profile.goals),
        ("禁忌/边界", profile.boundaries),
        ("补充", profile.notes),
    ):
        if value.strip():
            parts.append(f"{label}：{value.strip()}")
    return "\n".join(parts)


def format_character_book_for_prompt(
    book: CharacterBook,
    participant_ids: list[str],
    timeline: list[ChatTimelineEntry] | None = None,
    max_events: int = 8,
) -> str:
    ids = set(participant_ids)
    parts: list[str] = []
    for profile in book.profiles:
        if profile.character_id not in ids:
            continue
        parts.append(format_profile(profile))
        memory = find_memory(book, profile.character_id)
        if memory:
            mem_parts = []
            if memory.current_state:
                mem_parts.append(f"当前状态：{memory.current_state}")
            if memory.emotion_and_goals:
                mem_parts.append(f"情绪/目标：{memory.emotion_and_goals}")
            if memory.knowledge_state:
                mem_parts.append(f"已知信息：{memory.knowledge_state}")
            if memory.recent_actions:
                mem_parts.append("近期行动：" + "；".join(memory.recent_actions[-3:]))
            if memory.experiences:
                mem_parts.append("重要经历：" + "；".join(memory.experiences[-5:]))
            if memory.relationships:
                rels = [f"{k}={v}" for k, v in list(memory.relationships.items())[:6]]
                mem_parts.append("关系：" + "；".join(rels))
            if memory.key_dialogues:
                mem_parts.append("关键对话：" + " | ".join(memory.key_dialogues[-2:]))
            if mem_parts:
                parts.append("人物书记忆：\n" + "\n".join(f"- {p}" for p in mem_parts))
        parts.append("")
    recent = list(timeline or [])[-max_events:]
    if recent:
        parts.append("【当前对话独立时间线】")
        for item in recent:
            line = f"- 第{item.turn_index}轮：{item.event}"
            if item.participants:
                line += f" | 参与：{'、'.join(item.participants)}"
            if item.impact:
                line += f" | 影响：{item.impact}"
            parts.append(line)
    return "\n".join(parts).strip()


def _merge_unique(target: list[str], values: list[str], max_items: int = 30) -> None:
    seen = {v.strip() for v in target}
    for value in values:
        value = str(value).strip()
        if value and value not in seen:
            target.append(value[:300])
            seen.add(value)
    if len(target) > max_items:
        del target[:-max_items]


def merge_character_book_data(
    book: CharacterBook,
    data: dict,
    participant_ids: list[str],
    turn_index: int,
    source_message_range: str,
) -> tuple[CharacterBook, list[ChatTimelineEntry]]:
    id_by_name = {p.name: p.character_id for p in book.profiles}
    for profile in book.profiles:
        for alias in profile.aliases:
            id_by_name[alias] = profile.character_id

    for item in data.get("characters", []):
        name = str(item.get("name", "")).strip()
        character_id = item.get("character_id") or id_by_name.get(name)
        if not character_id or character_id not in participant_ids:
            continue
        profile = find_profile(book, character_id)
        memory = find_memory(book, character_id)
        if memory is None:
            memory = CharacterMemory(character_id=character_id, name=profile.name if profile else name)
            book.memories.append(memory)
        memory.name = profile.name if profile else name or memory.name
        _merge_unique(memory.experiences, _as_list(item.get("experiences", [])))
        _merge_unique(memory.recent_actions, _as_list(item.get("recent_actions", [])), max_items=12)
        _merge_unique(memory.key_dialogues, _as_list(item.get("key_dialogues", [])), max_items=20)
        if item.get("current_state"):
            memory.current_state = str(item["current_state"])[:300]
        if item.get("knowledge_state"):
            memory.knowledge_state = str(item["knowledge_state"])[:300]
        if item.get("emotion_and_goals"):
            memory.emotion_and_goals = str(item["emotion_and_goals"])[:300]
        rels = item.get("relationships", {})
        if isinstance(rels, list):
            rels = {str(r.get("target", "")): str(r.get("description", "") or r.get("type", "")) for r in rels if isinstance(r, dict)}
        if isinstance(rels, dict):
            for target, desc in rels.items():
                if str(target).strip() and str(desc).strip():
                    memory.relationships[str(target).strip()] = str(desc).strip()[:200]
        memory.fact_sources.append({
            "turn_index": turn_index,
            "source_message_range": source_message_range,
            "updated_at": _now(),
        })
        memory.fact_sources = memory.fact_sources[-20:]
        memory.updated_at = _now()

    timeline: list[ChatTimelineEntry] = []
    for item in data.get("timeline", []):
        event = str(item.get("event", "")).strip()
        if not event:
            continue
        timeline.append(ChatTimelineEntry(
            event_id=_new_id("evt"),
            turn_index=turn_index,
            event=event[:300],
            participants=_as_list(item.get("participants", [])),
            impact=str(item.get("impact", ""))[:300],
            source_message_range=source_message_range,
            created_at=_now(),
        ))
    return book, timeline


EXTRACT_PROMPT = """你是聊天角色人物书整理器。请只输出合法 JSON，不要输出解释。
从本轮对话中提取参与角色的经历、状态、关系、已知信息、行动、情绪目标变化、关键台词，以及本对话时间线事件。
只记录已经发生或明确表达的信息，不要把猜测写成事实。
JSON 格式：
{
  "characters": [
    {
      "character_id": "必须使用输入中的 character_id",
      "name": "角色名",
      "experiences": ["本轮新增或确认的重要经历"],
      "current_state": "当前状态",
      "relationships": {"对象": "关系变化或现状"},
      "knowledge_state": "该角色当前已知信息",
      "recent_actions": ["近期关键行动"],
      "emotion_and_goals": "情绪与目标变化",
      "key_dialogues": ["重要原话或近似摘录"]
    }
  ],
  "timeline": [
    {
      "event": "本轮发生的核心事件",
      "participants": ["角色名"],
      "impact": "对后续对话或关系的影响"
    }
  ]
}
"""


def extract_and_merge_character_book(
    client,
    model: str,
    book: CharacterBook,
    participant_ids: list[str],
    user_message: str,
    assistant_message: str,
    timeline: list[ChatTimelineEntry],
    turn_index: int,
    global_user_prompt: str = "",
) -> tuple[CharacterBook, list[ChatTimelineEntry]]:
    participants = [p for p in book.profiles if p.character_id in set(participant_ids)]
    if not participants:
        return book, []
    context = {
        "participants": [asdict(p) for p in participants],
        "recent_timeline": [asdict(t) for t in timeline[-8:]],
        "user_message": user_message,
        "assistant_message": assistant_message,
    }
    prompt = EXTRACT_PROMPT + "\n输入：\n" + json.dumps(context, ensure_ascii=False, indent=2)
    if global_user_prompt.strip():
        prompt += "\n用户偏好：" + global_user_prompt.strip()
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.1,
        max_tokens=4096,
    )
    raw = response.choices[0].message.content or "{}"
    if "```json" in raw:
        raw = raw.split("```json", 1)[1].split("```", 1)[0]
    elif "```" in raw:
        raw = raw.split("```", 1)[1].split("```", 1)[0]
    data = json.loads(raw.strip())
    return merge_character_book_data(
        book,
        data,
        participant_ids=participant_ids,
        turn_index=turn_index,
        source_message_range=f"turn:{turn_index}",
    )


class CharacterBookManager:
    def __init__(self, root_dir: str, crypto=None, enc_key: bytes | None = None) -> None:
        self._root_dir = root_dir
        self._crypto = crypto
        self._enc_key = enc_key
        os.makedirs(self._root_dir, exist_ok=True)

    def _path(self) -> str:
        return os.path.join(self._root_dir, "character_book.json")

    def _encrypt_path(self, path: str) -> str:
        if self._enc_key is None:
            return path
        return path + ".enc"

    def load(self) -> CharacterBook:
        path = self._path()
        enc_path = self._encrypt_path(path)
        if not os.path.exists(enc_path):
            return CharacterBook()
        if self._enc_key is None:
            with open(enc_path, "r", encoding="utf-8") as f:
                return dict_to_character_book(json.load(f))
        return dict_to_character_book(self._crypto.decrypt_json(self._enc_key, enc_path))

    def save(self, book: CharacterBook) -> None:
        path = self._path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        now = _now()
        for profile in book.profiles:
            profile.character_id = profile.character_id or _new_id("char")
            profile.created_at = profile.created_at or now
            profile.updated_at = profile.updated_at or now
        for memory in book.memories:
            memory.updated_at = memory.updated_at or now
        data = character_book_to_dict(book)
        if self._enc_key is None:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        else:
            self._crypto.encrypt_json(self._enc_key, path + ".enc", data)

    def list_profiles(self) -> list[CharacterProfile]:
        return self.load().profiles

    def create_profile(self, profile: CharacterProfile) -> CharacterProfile:
        book = self.load()
        now = _now()
        profile.character_id = profile.character_id or _new_id("char")
        profile.created_at = profile.created_at or now
        profile.updated_at = now
        book.profiles.append(profile)
        self.save(book)
        return profile

    def update_profile(self, profile: CharacterProfile) -> None:
        book = self.load()
        profile.updated_at = _now()
        for idx, existing in enumerate(book.profiles):
            if existing.character_id == profile.character_id:
                profile.created_at = existing.created_at or profile.created_at
                book.profiles[idx] = profile
                break
        else:
            book.profiles.append(profile)
        self.save(book)

    def delete_profile(self, character_id: str) -> None:
        book = self.load()
        book.profiles = [p for p in book.profiles if p.character_id != character_id]
        book.memories = [m for m in book.memories if m.character_id != character_id]
        self.save(book)

"""
世界书系统（World Bible）
负责从已生成的章节中提取核心设定、角色、地点、规则、剧情线索，
并持久化为结构化数据供后续章节生成时参考，防止设定矛盾。
"""

import difflib
import copy
import json
import os
import re
import uuid
from dataclasses import dataclass, field, asdict
from typing import Optional


# ========== 数据结构 ==========

WORLD_BIBLE_SCHEMA_VERSION = 2
_ENTITY_NAMESPACE = uuid.UUID("a464fcbe-2f2c-4b04-b83b-40ab4863a67a")


def _stable_id(kind: str, name: str, chapter: int = 0, salt: str = "") -> str:
    """Return a deterministic entity id across aggregate rebuilds."""
    normalized = re.sub(r"\s+", "", str(name or "")).lower()
    seed = f"{kind}:{normalized}:{int(chapter or 0)}:{salt}"
    return f"{kind}_{uuid.uuid5(_ENTITY_NAMESPACE, seed).hex[:16]}"


def repair_duplicate_entity_ids(bible) -> list[dict]:
    """Repair missing/duplicate stable IDs without deleting or merging entities.

    The first occurrence keeps its ID. Later collisions receive deterministic IDs
    derived from their own type, label, source chapter, and collision position.
    Existing references therefore continue to resolve to the original entity.
    """
    groups = (
        ("char", getattr(bible, "characters", []) or [], "name", "first_appearance"),
        ("loc", getattr(bible, "locations", []) or [], "name", "first_appearance"),
        ("event", getattr(bible, "timeline", []) or [], "event", "chapter"),
        ("thread", getattr(bible, "active_plot_threads", []) or [], "name", "opened_chapter"),
        ("rule", getattr(bible, "world_rules", []) or [], "name", "valid_from"),
    )
    used: set[str] = set()
    repairs: list[dict] = []
    for kind, entities, label_field, chapter_field in groups:
        for position, entity in enumerate(entities):
            old_id = str(getattr(entity, "id", "") or "")
            label = str(getattr(entity, label_field, "") or getattr(entity, "content", "") or f"{kind}-{position + 1}")
            chapter = int(getattr(entity, chapter_field, 0) or 0)
            if old_id and old_id not in used:
                used.add(old_id)
                continue
            salt_index = 1
            while True:
                candidate = _stable_id(kind, label, chapter, f"repair:{position}:{salt_index}")
                if candidate not in used:
                    break
                salt_index += 1
            setattr(entity, "id", candidate)
            used.add(candidate)
            repairs.append({
                "entity_type": kind,
                "label": label,
                "old_id": old_id,
                "new_id": candidate,
                "reason": "duplicate" if old_id else "missing",
            })
    if repairs and hasattr(bible, "diagnostics"):
        bible.diagnostics["entity_id_repairs"] = repairs[-100:]
    return repairs

@dataclass
class SourceRef:
    chapter: int = 0
    version: int = 0
    snapshot_key: str = ""
    excerpt: str = ""


@dataclass
class WorldFact:
    id: str = ""
    subject_id: str = ""
    predicate: str = ""
    value: object = ""
    valid_from: int = 0
    valid_to: int = 0
    source_refs: list[dict] = field(default_factory=list)
    knowledge_type: str = "canon"
    confidence: float = 1.0
    supersedes: str = ""
    locked: bool = False


@dataclass
class WorldRule:
    id: str = ""
    name: str = ""
    content: str = ""
    category: str = "general"
    priority: int = 50
    source_refs: list[dict] = field(default_factory=list)
    knowledge_type: str = "constraint"
    confidence: float = 1.0
    locked: bool = False
    hidden: bool = False
    valid_from: int = 0
    valid_to: int = 0
    exceptions: list[str] = field(default_factory=list)
    supersedes: str = ""
    conflicts_with: list[str] = field(default_factory=list)


@dataclass
class ManualOverride:
    id: str = ""
    operation: str = "patch"
    entity_type: str = ""
    entity_id: str = ""
    payload: dict = field(default_factory=dict)
    created_at: str = ""
    note: str = ""
    scope: str = "global"
    anchor_node_id: str = ""
    source: str = "manual"
    scope_reason: str = ""


@dataclass
class EntityMerge:
    id: str = ""
    entity_type: str = ""
    target_id: str = ""
    source_ids: list[str] = field(default_factory=list)
    aliases_added: list[str] = field(default_factory=list)
    reversible_snapshot: dict = field(default_factory=dict)
    created_at: str = ""
    reverted: bool = False



@dataclass
class Relationship:
    target: str = ""
    target_id: str = ""
    type: str = ""          # friend/enemy/family/master/student/ally/rival
    description: str = ""


@dataclass
class CharacterEntry:
    id: str = ""
    name: str = ""
    aliases: list[str] = field(default_factory=list)
    traits: str = ""         # 性格、外貌、能力
    relationships: list[Relationship] = field(default_factory=list)
    status: str = "alive"    # alive/dead/missing/transformed
    importance: str = "normal"  # major / normal / minor
    first_appearance: int = 0
    notes: str = ""
    key_details: list[str] = field(default_factory=list)       # 原文引用的角色关键描述
    key_dialogues: list[str] = field(default_factory=list)     # 原文引用的角色重要台词
    motivation: str = ""                                       # 核心动机/目标
    arc: str = ""                                              # 成长弧线
    birth_date: str = ""                                       # 出生日期/纪年，不确定则留空
    current_age: str = ""                                      # 当前年龄，可保留原文的约数口径
    age_basis: str = ""                                        # 年龄依据，如故事日期、生日或原文说明
    life_stage: str = ""                                       # 人生/身份阶段，如童年、大学一年级、孕中期
    current_location: str = ""                                  # 当前所在位置
    current_goal: str = ""                                      # 当前目标/意图
    current_emotion: str = ""                                   # 当前情绪/关系状态
    recent_action: str = ""                                     # 最近一次关键行动
    knowledge_state: str = ""                                   # 当前已知信息/误解
    unresolved_conflicts: list[str] = field(default_factory=list)  # 仍未解决的个人冲突
    source_chapter: int = 0                                      # 首次提取来源章节
    source_version: int = 0                                      # 首次提取来源版本
    last_updated_chapter: int = 0                                # 最近更新来源章节
    last_updated_version: int = 0                                # 最近更新来源版本
    hidden: bool = False                                         # 是否从生成注入中隐藏
    fact_sources: dict[str, list[dict]] = field(default_factory=dict)  # 字段级来源，兼容旧字段


    knowledge_type: str = "canon"
    confidence: float = 1.0
    source_refs: list[dict] = field(default_factory=list)
    locked: bool = False

@dataclass
class LocationEntry:
    id: str = ""
    name: str = ""
    description: str = ""
    significance: str = ""
    first_appearance: int = 0
    key_details: list[str] = field(default_factory=list)   # 原文引用的地点重要描写
    atmosphere: str = ""                                    # 氛围描述
    source_chapter: int = 0
    source_version: int = 0
    last_updated_chapter: int = 0
    last_updated_version: int = 0
    hidden: bool = False
    fact_sources: dict[str, list[dict]] = field(default_factory=dict)
    knowledge_type: str = "canon"
    confidence: float = 1.0
    source_refs: list[dict] = field(default_factory=list)
    locked: bool = False


@dataclass
class TimelineEntry:
    id: str = ""
    chapter: int = 0
    event: str = ""
    significance: str = ""
    occurrence_count: int = 1                                  # 关键事件被提取/触达的次数
    key_passages: list[str] = field(default_factory=list)          # 原文引用的事件重要段落
    foreshadowing_hints: list[str] = field(default_factory=list)   # 该事件中埋下的伏笔
    source_version: int = 0
    knowledge_type: str = "canon"
    confidence: float = 1.0
    source_refs: list[dict] = field(default_factory=list)
    locked: bool = False


@dataclass
class PlotThread:
    id: str = ""
    name: str = ""
    status: str = "active"   # active/resolved/dormant
    importance: str = "normal"  # major / normal / minor
    involved_characters: list[str] = field(default_factory=list)
    description: str = ""
    key_details: list[str] = field(default_factory=list)             # 原文引用的剧情线重要内容
    foreshadowing_related: list[str] = field(default_factory=list)   # 该线关联的前期伏笔
    opened_chapter: int = 0
    last_touched_chapter: int = 0
    expected_payoff: str = ""
    payoff_hint: str = ""
    source_chapter: int = 0
    source_version: int = 0
    last_updated_version: int = 0
    hidden: bool = False
    fact_sources: dict[str, list[dict]] = field(default_factory=dict)
    knowledge_type: str = "canon"
    confidence: float = 1.0
    source_refs: list[dict] = field(default_factory=list)
    locked: bool = False


@dataclass
class WorldBible:
    schema_version: int = WORLD_BIBLE_SCHEMA_VERSION
    characters: list[CharacterEntry] = field(default_factory=list)
    locations: list[LocationEntry] = field(default_factory=list)
    rules: list[str] = field(default_factory=list)
    timeline: list[TimelineEntry] = field(default_factory=list)
    active_plot_threads: list[PlotThread] = field(default_factory=list)
    story_clock: dict = field(default_factory=dict)                    # 当前故事日期、时段、已流逝时间和阶段
    story_clock_history: list[dict] = field(default_factory=list)       # 按章节保留时间状态演进历史
    last_updated_chapter: int = 0
    chapter_world_entries: dict[str, dict] = field(default_factory=dict)  # {"ch0001_v001": raw extracted JSON}
    key_worldbuilding_passages: list[dict] = field(default_factory=list)  # [{chapter, passage, topic}]
    global_foreshadowing: list[dict] = field(default_factory=list)        # [{hint, relates_to, status, introduced_chapter, last_touched_chapter, next_step, reveal_rule}]
    global_key_dialogues: list[dict] = field(default_factory=list)        # [{speaker, dialogue, context}]
    consistency_warnings: list[dict] = field(default_factory=list)         # [{severity, type, message, related}]
    chapter_snapshots: dict[str, dict] = field(default_factory=dict)
    manual_overrides: list[ManualOverride] = field(default_factory=list)
    resolved_view: dict = field(default_factory=dict)
    facts: list[WorldFact] = field(default_factory=list)
    world_rules: list[WorldRule] = field(default_factory=list)
    merge_history: list[EntityMerge] = field(default_factory=list)
    duplicate_candidates: list[dict] = field(default_factory=list)
    diagnostics: dict = field(default_factory=dict)
    migration_info: dict = field(default_factory=dict)


# ========== 序列化/反序列化 ==========


def _filter_fields(cls, data: dict) -> dict:
    """过滤 dict 只保留 dataclass 中定义的字段，兼容 schema 变化"""
    return {k: v for k, v in data.items() if k in cls.__dataclass_fields__}


def _source_ref(chapter: int, version: int = 0, excerpt: str = "") -> dict:
    return asdict(SourceRef(
        chapter=int(chapter or 0),
        version=int(version or 0),
        snapshot_key=_chapter_world_entry_key(chapter, version) if chapter else "",
        excerpt=str(excerpt or "")[:200],
    ))


def _ensure_entity_metadata(bible: WorldBible) -> None:
    """Fill stable IDs and v2 authority metadata without changing visible content."""
    for ch in bible.characters:
        ch.id = ch.id or _stable_id("char", ch.name, ch.first_appearance)
        if not ch.source_refs and (ch.source_chapter or ch.first_appearance):
            ch.source_refs = [_source_ref(ch.source_chapter or ch.first_appearance, ch.source_version)]
        for rel in ch.relationships:
            target = _find_character_by_name_or_alias(bible.characters, rel.target, [])
            if target:
                rel.target_id = target.id
    for loc in bible.locations:
        loc.id = loc.id or _stable_id("loc", loc.name, loc.first_appearance)
        if not loc.source_refs and (loc.source_chapter or loc.first_appearance):
            loc.source_refs = [_source_ref(loc.source_chapter or loc.first_appearance, loc.source_version)]
    for event in bible.timeline:
        event.id = event.id or _stable_id("event", event.event, event.chapter)
        if not event.source_refs and event.chapter:
            event.source_refs = [_source_ref(event.chapter, event.source_version)]
    for thread in bible.active_plot_threads:
        thread.id = thread.id or _stable_id("thread", thread.name, thread.opened_chapter)
        if not thread.source_refs and (thread.source_chapter or thread.opened_chapter):
            thread.source_refs = [_source_ref(thread.source_chapter or thread.opened_chapter, thread.source_version)]
    for item in bible.key_worldbuilding_passages:
        item.setdefault("id", _stable_id("setting", item.get("topic", ""), item.get("chapter", 0)))
        item.setdefault("knowledge_type", "constraint" if item.get("locked") else "canon")
        item.setdefault("confidence", 1.0)
        item.setdefault("source_refs", [_source_ref(item.get("chapter", 0), item.get("version", 0))] if item.get("chapter") else [])
        item.setdefault("locked", bool(item.get("locked")))
        item.setdefault("core_summary", str(item.get("description") or ""))
        item.setdefault("full_passage", str(item.get("passage") or ""))
        item.setdefault("constraints", [])
        item.setdefault("keywords", [])
    for item in bible.global_foreshadowing:
        item.setdefault("id", _stable_id("foreshadow", item.get("hint", ""), item.get("introduced_chapter", 0)))
        item.setdefault("knowledge_type", "author_plan")
        item.setdefault("confidence", 0.7)
        item.setdefault("source_refs", [_source_ref(item.get("introduced_chapter", 0), item.get("introduced_version", 0))] if item.get("introduced_chapter") else [])
        item.setdefault("locked", False)
    for item in bible.global_key_dialogues:
        item.setdefault("id", _stable_id("dialogue", item.get("dialogue", ""), item.get("chapter", 0)))
        item.setdefault("knowledge_type", "canon")
        item.setdefault("confidence", 1.0)
        item.setdefault("source_refs", [_source_ref(item.get("chapter", 0), item.get("version", 0), item.get("dialogue", ""))] if item.get("chapter") else [])
        item.setdefault("locked", False)

    existing_rule_content = {_norm_key(rule.content) for rule in bible.world_rules}
    for text in bible.rules:
        content = str(text).strip()
        if content and _norm_key(content) not in existing_rule_content:
            bible.world_rules.append(WorldRule(
                id=_stable_id("rule", content),
                name=content[:40],
                content=content,
                knowledge_type="constraint",
            ))
            existing_rule_content.add(_norm_key(content))
    bible.rules = [rule.content for rule in bible.world_rules if rule.content] or list(bible.rules)
    bible.chapter_snapshots.update(bible.chapter_world_entries)
    bible.chapter_world_entries.update(bible.chapter_snapshots)


def _flat_view_dict(bible: WorldBible) -> dict:
    return {
        "characters": [asdict(item) for item in bible.characters],
        "locations": [asdict(item) for item in bible.locations],
        "rules": list(bible.rules),
        "world_rules": [asdict(item) for item in bible.world_rules],
        "timeline": [asdict(item) for item in bible.timeline],
        "active_plot_threads": [asdict(item) for item in bible.active_plot_threads],
        "story_clock": copy.deepcopy(bible.story_clock),
        "story_clock_history": copy.deepcopy(bible.story_clock_history),
        "last_updated_chapter": bible.last_updated_chapter,
        "key_worldbuilding_passages": copy.deepcopy(bible.key_worldbuilding_passages),
        "global_foreshadowing": copy.deepcopy(bible.global_foreshadowing),
        "global_key_dialogues": copy.deepcopy(bible.global_key_dialogues),
        "facts": [asdict(item) for item in bible.facts],
    }


def _from_dict(cls, data: dict):
    """Deserialize legacy or v2 data while preserving forward-compatible fields."""
    if cls == CharacterEntry:
        rels = [Relationship(**_filter_fields(Relationship, r)) for r in data.get("relationships", []) if isinstance(r, dict)]
        base = _filter_fields(cls, {k: v for k, v in data.items() if k != "relationships"})
        return CharacterEntry(relationships=rels, **base)
    if cls == WorldBible:
        snapshots = dict(data.get("chapter_snapshots") or data.get("chapter_world_entries") or {})
        bible = WorldBible(
            schema_version=WORLD_BIBLE_SCHEMA_VERSION,
            characters=[_from_dict(CharacterEntry, c) for c in data.get("characters", []) if isinstance(c, dict)],
            locations=[LocationEntry(**_filter_fields(LocationEntry, item)) for item in data.get("locations", []) if isinstance(item, dict)],
            rules=[str(item) for item in data.get("rules", [])],
            timeline=[TimelineEntry(**_filter_fields(TimelineEntry, item)) for item in data.get("timeline", []) if isinstance(item, dict)],
            active_plot_threads=[PlotThread(**_filter_fields(PlotThread, item)) for item in data.get("active_plot_threads", []) if isinstance(item, dict)],
            story_clock=dict(data.get("story_clock", {})),
            story_clock_history=copy.deepcopy(data.get("story_clock_history", [])),
            last_updated_chapter=int(data.get("last_updated_chapter", 0) or 0),
            chapter_world_entries=copy.deepcopy(snapshots),
            chapter_snapshots=copy.deepcopy(snapshots),
            key_worldbuilding_passages=copy.deepcopy(data.get("key_worldbuilding_passages", [])),
            global_foreshadowing=copy.deepcopy(data.get("global_foreshadowing", [])),
            global_key_dialogues=copy.deepcopy(data.get("global_key_dialogues", [])),
            consistency_warnings=copy.deepcopy(data.get("consistency_warnings", [])),
            manual_overrides=[ManualOverride(**_filter_fields(ManualOverride, item)) for item in data.get("manual_overrides", []) if isinstance(item, dict)],
            resolved_view=copy.deepcopy(data.get("resolved_view", {})),
            facts=[WorldFact(**_filter_fields(WorldFact, item)) for item in data.get("facts", []) if isinstance(item, dict)],
            world_rules=[WorldRule(**_filter_fields(WorldRule, item)) for item in data.get("world_rules", []) if isinstance(item, dict)],
            merge_history=[EntityMerge(**_filter_fields(EntityMerge, item)) for item in data.get("merge_history", []) if isinstance(item, dict)],
            duplicate_candidates=copy.deepcopy(data.get("duplicate_candidates", [])),
            diagnostics=copy.deepcopy(data.get("diagnostics", {})),
            migration_info=copy.deepcopy(data.get("migration_info", {})),
        )
        if int(data.get("schema_version", 1) or 1) < WORLD_BIBLE_SCHEMA_VERSION:
            bible.migration_info.update({"migrated_from": int(data.get("schema_version", 1) or 1), "status": "migrated"})
        _ensure_entity_metadata(bible)
        bible.resolved_view = _flat_view_dict(bible)
        return bible
    return cls(**_filter_fields(cls, data))


def world_bible_to_dict(bible: WorldBible) -> dict:
    _ensure_entity_metadata(bible)
    bible.schema_version = WORLD_BIBLE_SCHEMA_VERSION
    bible.chapter_snapshots.update(bible.chapter_world_entries)
    bible.chapter_world_entries = copy.deepcopy(bible.chapter_snapshots)
    bible.resolved_view = _flat_view_dict(bible)
    return asdict(bible)


def dict_to_world_bible(data: dict) -> WorldBible:
    if not isinstance(data, dict):
        raise ValueError("World bible root must be a JSON object")
    version = int(data.get("schema_version", 1) or 1)
    if version > WORLD_BIBLE_SCHEMA_VERSION:
        raise ValueError(f"Unsupported world bible schema version: {version}")
    return _from_dict(WorldBible, data)

def _override_id(operation: str, entity_type: str, entity_id: str) -> str:
    return _stable_id("override", f"{operation}:{entity_type}:{entity_id}")


def _upsert_override(bible: WorldBible, override: ManualOverride) -> None:
    bible.manual_overrides = [
        item for item in bible.manual_overrides
        if not (
            item.operation == override.operation
            and item.entity_type == override.entity_type
            and item.entity_id == override.entity_id
            and getattr(item, "scope", "global") == getattr(override, "scope", "global")
            and getattr(item, "anchor_node_id", "") == getattr(override, "anchor_node_id", "")
        )
    ]
    bible.manual_overrides.append(override)


def record_manual_view_changes(bible: WorldBible, before_view: dict) -> None:
    """Convert UI edits into durable, replayable overrides."""
    _ensure_entity_metadata(bible)
    after_view = _flat_view_dict(bible)
    specs = (
        ("character", "characters"),
        ("location", "locations"),
        ("timeline", "timeline"),
        ("plot_thread", "active_plot_threads"),
        ("setting", "key_worldbuilding_passages"),
        ("foreshadowing", "global_foreshadowing"),
        ("dialogue", "global_key_dialogues"),
        ("rule", "world_rules"),
    )
    for entity_type, key in specs:
        old_items = {str(item.get("id", "")): item for item in before_view.get(key, []) if isinstance(item, dict) and item.get("id")}
        new_items = {str(item.get("id", "")): item for item in after_view.get(key, []) if isinstance(item, dict) and item.get("id")}
        for entity_id in old_items.keys() - new_items.keys():
            _upsert_override(bible, ManualOverride(
                id=_override_id("delete", entity_type, entity_id),
                operation="delete", entity_type=entity_type, entity_id=entity_id,
            ))
        for entity_id, payload in new_items.items():
            if entity_id not in old_items:
                _upsert_override(bible, ManualOverride(
                    id=_override_id("add", entity_type, entity_id),
                    operation="add", entity_type=entity_type, entity_id=entity_id,
                    payload=copy.deepcopy(payload),
                ))
            elif payload != old_items[entity_id]:
                _upsert_override(bible, ManualOverride(
                    id=_override_id("patch", entity_type, entity_id),
                    operation="patch", entity_type=entity_type, entity_id=entity_id,
                    payload=copy.deepcopy(payload),
                ))
    if before_view.get("story_clock", {}) != after_view.get("story_clock", {}):
        _upsert_override(bible, ManualOverride(
            id=_override_id("patch", "story_clock", "story_clock"),
            operation="patch", entity_type="story_clock", entity_id="story_clock",
            payload=copy.deepcopy(after_view.get("story_clock", {})),
        ))


def _override_collection(bible: WorldBible, entity_type: str):
    return {
        "character": (bible.characters, CharacterEntry),
        "location": (bible.locations, LocationEntry),
        "timeline": (bible.timeline, TimelineEntry),
        "plot_thread": (bible.active_plot_threads, PlotThread),
        "setting": (bible.key_worldbuilding_passages, dict),
        "foreshadowing": (bible.global_foreshadowing, dict),
        "dialogue": (bible.global_key_dialogues, dict),
        "rule": (bible.world_rules, WorldRule),
    }.get(entity_type)


def _override_is_active(
    override: ManualOverride,
    active_node_ids: list[str] | set[str] | None = None,
    current_node_id: str = "",
) -> bool:
    scope = str(getattr(override, "scope", "global") or "global")
    anchor = str(getattr(override, "anchor_node_id", "") or "")
    if scope == "global":
        return True
    if not anchor:
        return False
    active = set(active_node_ids or [])
    if scope == "branch":
        return anchor in active
    if scope == "chapter":
        return bool(current_node_id) and anchor == current_node_id
    return False


def apply_manual_overrides(
    bible: WorldBible,
    active_node_ids: list[str] | set[str] | None = None,
    current_node_id: str = "",
) -> WorldBible:
    """Replay user authority, filtered by global/chapter/branch scope."""
    for override in bible.manual_overrides:
        if not _override_is_active(override, active_node_ids, current_node_id):
            continue
        if override.entity_type == "story_clock" and override.operation == "patch":
            bible.story_clock = copy.deepcopy(override.payload)
            continue
        spec = _override_collection(bible, override.entity_type)
        if not spec:
            continue
        collection, cls = spec
        existing_index = next((i for i, item in enumerate(collection) if (item.get("id") if isinstance(item, dict) else getattr(item, "id", "")) == override.entity_id), -1)
        if override.operation == "delete":
            if existing_index >= 0:
                del collection[existing_index]
            continue
        payload = copy.deepcopy(override.payload)
        if cls is CharacterEntry:
            item = _from_dict(CharacterEntry, payload)
        elif cls is dict:
            item = payload
        else:
            item = cls(**_filter_fields(cls, payload))
        if existing_index >= 0:
            collection[existing_index] = item
        elif override.operation in ("add", "patch"):
            collection.append(item)
    _ensure_entity_metadata(bible)
    bible.rules = [rule.content for rule in bible.world_rules if rule.content and not rule.hidden]
    bible.resolved_view = _flat_view_dict(bible)
    return bible

def _record_dynamic_fact(
    bible: WorldBible,
    subject_id: str,
    predicate: str,
    value,
    chapter_num: int,
    chapter_version: int = 0,
    *,
    knowledge_type: str = "canon",
    confidence: float = 1.0,
) -> None:
    if not subject_id or not predicate or value in (None, "", []):
        return
    previous = next((item for item in reversed(bible.facts) if item.subject_id == subject_id and item.predicate == predicate and not item.valid_to), None)
    if previous and previous.value == value:
        if _source_ref(chapter_num, chapter_version) not in previous.source_refs:
            previous.source_refs.append(_source_ref(chapter_num, chapter_version))
        return
    if previous and chapter_num:
        previous.valid_to = max(previous.valid_from, chapter_num - 1)
    fact = WorldFact(
        id=_stable_id("fact", f"{subject_id}:{predicate}:{value}", chapter_num, str(chapter_version)),
        subject_id=subject_id,
        predicate=predicate,
        value=copy.deepcopy(value),
        valid_from=int(chapter_num or 0),
        source_refs=[_source_ref(chapter_num, chapter_version)],
        knowledge_type=knowledge_type,
        confidence=float(confidence),
        supersedes=previous.id if previous else "",
    )
    bible.facts.append(fact)


def materialize_current_facts(bible: WorldBible, target_chapter: int = 0) -> None:
    """Project the latest valid facts back to legacy current-state fields."""
    target = int(target_chapter or bible.last_updated_chapter or 0)
    by_subject: dict[tuple[str, str], WorldFact] = {}
    for fact in bible.facts:
        if fact.valid_from and target and fact.valid_from > target:
            continue
        if fact.valid_to and target and fact.valid_to < target:
            continue
        key = (fact.subject_id, fact.predicate)
        current = by_subject.get(key)
        if current is None or (fact.valid_from, fact.confidence) >= (current.valid_from, current.confidence):
            by_subject[key] = fact
    character_fields = {
        "status", "current_location", "current_goal", "current_emotion", "recent_action",
        "knowledge_state", "current_age", "life_stage", "age_basis", "birth_date",
    }
    for character in bible.characters:
        for field_name in character_fields:
            fact = by_subject.get((character.id, field_name))
            if fact:
                setattr(character, field_name, copy.deepcopy(fact.value))
    for thread in bible.active_plot_threads:
        fact = by_subject.get((thread.id, "status"))
        if fact:
            thread.status = str(fact.value)

# ========== 格式化输出 ==========


def format_world_bible_for_prompt(bible: WorldBible, max_entries: int = 10) -> str:
    """
    将世界书格式化为紧凑文本，供注入到生成 prompt 中使用
    限制条目数量避免超出上下文窗口
    """
    parts = []

    clock = bible.story_clock or {}
    clock_parts = []
    for key, label in (("current_date", "当前日期"), ("time_of_day", "时段"), ("elapsed_time", "已流逝"), ("story_phase", "故事阶段"), ("calendar_system", "纪年体系")):
        value = str(clock.get(key, "")).strip()
        if value:
            clock_parts.append(f"{label}：{value}")
    if clock_parts:
        parts.append("【当前故事时间】\n- " + "；".join(clock_parts))

    visible_characters = [c for c in bible.characters if not getattr(c, "hidden", False)]
    visible_locations = [l for l in bible.locations if not getattr(l, "hidden", False)]
    visible_threads = [p for p in bible.active_plot_threads if not getattr(p, "hidden", False)]
    visible_passages = [p for p in bible.key_worldbuilding_passages if not p.get("hidden")]
    visible_foreshadowing = [f for f in bible.global_foreshadowing if not f.get("hidden")]

    if visible_characters:
        parts.append("【已登场的角色】")
        # 按重要性排序：major 优先，normal 其次，minor 最后
        sorted_chars = sorted(
            visible_characters,
            key=lambda c: {"major": 0, "normal": 1, "minor": 2}.get(c.importance, 1),
        )
        for ch in sorted_chars[:max_entries]:
            line = f"- {ch.name}：{ch.traits[:100]}"
            if ch.motivation:
                line += f" | 动机：{ch.motivation[:60]}"
            if ch.arc:
                line += f" | 弧光：{ch.arc[:60]}"
            age_parts = []
            if ch.birth_date:
                age_parts.append(f"出生={ch.birth_date[:30]}")
            if ch.current_age:
                age_parts.append(f"年龄={ch.current_age[:30]}")
            if ch.life_stage:
                age_parts.append(f"阶段={ch.life_stage[:40]}")
            if ch.age_basis:
                age_parts.append(f"依据={ch.age_basis[:50]}")
            if age_parts:
                line += " | 时间年龄：" + "；".join(age_parts)
            state_parts = []
            if ch.current_location:
                state_parts.append(f"位置：{ch.current_location[:40]}")
            if ch.current_goal:
                state_parts.append(f"目标：{ch.current_goal[:50]}")
            if ch.current_emotion:
                state_parts.append(f"状态：{ch.current_emotion[:40]}")
            if ch.recent_action:
                state_parts.append(f"近况：{ch.recent_action[:60]}")
            if state_parts:
                line += " | 当前" + "；".join(state_parts)
            if ch.knowledge_state:
                line += f" | 已知：{ch.knowledge_state[:50]}"
            if ch.unresolved_conflicts:
                line += " | 未解冲突：" + "；".join(ch.unresolved_conflicts[:2])
            rel_str = "; ".join(f"{r.type}({r.target})" for r in ch.relationships[:3])
            if rel_str:
                line += f" | 关系：{rel_str}"
            if ch.status != "alive":
                line += f" [{ch.status}]"
            if ch.key_details:
                line += " | " + " | ".join(ch.key_details[:2])
            if ch.key_dialogues:
                line += " | 台词：" + " | ".join(ch.key_dialogues[:1])
            parts.append(line)
        if len(visible_characters) > max_entries:
            parts.append(f"  ...以及另 {len(visible_characters) - max_entries} 个角色")

    if visible_locations:
        parts.append("\n【重要地点】")
        for loc in visible_locations[:max_entries]:
            line = f"- {loc.name}：{loc.description[:80]}"
            if loc.atmosphere:
                line += f"（{loc.atmosphere[:40]}）"
            if loc.significance:
                line += f" | 意义：{loc.significance[:60]}"
            if loc.key_details:
                line += " | " + " | ".join(loc.key_details[:1])
            parts.append(line)

    if bible.rules:
        parts.append("\n【世界观规则】")
        for rule in bible.rules[:max_entries]:
            parts.append(f"- {rule[:150]}")

    if visible_threads:
        active = [p for p in visible_threads if p.status == "active"]
        if active:
            parts.append("\n【活跃剧情线】")
            for p in active[:max_entries]:
                line = f"- {p.name}：{p.description[:100]}"
                if p.involved_characters:
                    line += f" | 角色：{', '.join(p.involved_characters[:4])}"
                if p.expected_payoff:
                    line += f" | 预期回收：{p.expected_payoff[:60]}"
                if p.payoff_hint:
                    line += f" | 提示：{p.payoff_hint[:60]}"
                if p.last_touched_chapter:
                    line += f" | 最近触达：第{p.last_touched_chapter}章"
                if p.foreshadowing_related:
                    line += " | 伏笔：" + " | ".join(p.foreshadowing_related[:1])
                parts.append(line)
        # 非活跃剧情线（简略列出）
        non_active = [p for p in visible_threads if p.status != "active"]
        if non_active:
            parts.append("\n【待回收剧情线】")
            for p in non_active[:4]:
                line = f"- {p.name} [{p.status}]：{p.description[:80]}"
                if p.expected_payoff:
                    line += f" | 可回收：{p.expected_payoff[:50]}"
                parts.append(line)

    if bible.timeline:
        recent = bible.timeline[-max_entries:]
        parts.append("\n【近期事件】")
        for t in recent:
            line = f"- 第{t.chapter}章：{t.event[:80]}"
            if t.occurrence_count > 1:
                line += f" [次数：{t.occurrence_count}]"
            if t.significance:
                line += f"（{t.significance[:40]}）"
            if t.foreshadowing_hints:
                line += " 🔮" + " | ".join(t.foreshadowing_hints[:1])
            parts.append(line)

    # 全局设定与伏笔（简略展示 3-4 条）
    extras = []
    if visible_passages:
        for item in visible_passages[:3]:
            summary = item.get("core_summary") or item.get("passage", "")
            extras.append(f"- 设定·{item.get('topic', '')}：{str(summary)[:180]}")
            if item.get("constraints"):
                extras.append("  约束：" + "；".join(str(value) for value in item.get("constraints", [])[:4]))
    if visible_foreshadowing:
        for item in visible_foreshadowing[:5]:
            line = f"- 伏笔·{item.get('hint', '')[:60]}"
            if item.get("status"):
                line += f" [{item.get('status')}]"
            if item.get("next_step"):
                line += f" | 推进：{item.get('next_step', '')[:60]}"
            if item.get("reveal_rule"):
                line += f" | 限制：{item.get('reveal_rule', '')[:60]}"
            extras.append(line)
    if extras:
        parts.append("\n【关键设定与伏笔】")
        parts.extend(extras)

    return "\n".join(parts)


def confirm_duplicate_candidate(bible: WorldBible, candidate_id: str) -> bool:
    """Confirm a duplicate suggestion with a reversible snapshot and durable overrides."""
    candidate = next((item for item in bible.duplicate_candidates if item.get("id") == candidate_id), None)
    if not candidate or candidate.get("status", "pending") != "pending":
        return False
    entity_type = candidate.get("entity_type")
    source_ids = list(candidate.get("entity_ids", []))
    if len(source_ids) < 2:
        return False
    before = _flat_view_dict(bible)
    if entity_type == "character":
        collection = bible.characters
        matched = [item for item in collection if item.id in source_ids]
        if len(matched) < 2:
            return False
        matched.sort(key=lambda item: (item.importance == "major", len(item.traits), -item.first_appearance), reverse=True)
        base = matched[0]
        for other in matched[1:]:
            _merge_character_entry(base, asdict(other), "", other.last_updated_chapter, other.last_updated_version)
            if other.name not in base.aliases:
                base.aliases.append(other.name)
            for fact in bible.facts:
                if fact.subject_id == other.id:
                    fact.subject_id = base.id
            for item in bible.characters:
                for rel in item.relationships:
                    if rel.target_id == other.id or _norm_key(rel.target) in _character_keys(other):
                        rel.target_id = base.id
                        rel.target = base.name
        bible.characters = [item for item in collection if item is base or item.id not in source_ids]
        target_id = base.id
    elif entity_type == "location":
        collection = bible.locations
        matched = [item for item in collection if item.id in source_ids]
        if len(matched) < 2:
            return False
        matched.sort(key=lambda item: len(item.description) + len(item.key_details) * 40, reverse=True)
        base = matched[0]
        for other in matched[1:]:
            base.description = _append_text_unique(base.description, other.description, 1200)
            base.significance = _append_text_unique(base.significance, other.significance, 600)
            base.atmosphere = _append_text_unique(base.atmosphere, other.atmosphere, 500)
            _merge_list_dedup(base.key_details, other.key_details)
            for fact in bible.facts:
                if fact.subject_id == other.id:
                    fact.subject_id = base.id
        bible.locations = [item for item in collection if item is base or item.id not in source_ids]
        target_id = base.id
    else:
        return False
    history = EntityMerge(
        id=_stable_id("merge", candidate_id, len(bible.merge_history)),
        entity_type=entity_type,
        target_id=target_id,
        source_ids=[item for item in source_ids if item != target_id],
        reversible_snapshot={"view": before},
    )
    bible.merge_history.append(history)
    candidate["status"] = "confirmed"
    candidate["merge_id"] = history.id
    record_manual_view_changes(bible, before)
    bible.consistency_warnings = audit_world_bible_consistency(bible)
    return True


def undo_entity_merge(bible: WorldBible, merge_id: str = "") -> bool:
    """Restore the last or selected non-reverted entity merge."""
    history = next((item for item in reversed(bible.merge_history) if not item.reverted and (not merge_id or item.id == merge_id)), None)
    if history is None:
        return False
    view = history.reversible_snapshot.get("view", {})
    if history.entity_type == "character":
        bible.characters = [_from_dict(CharacterEntry, item) for item in view.get("characters", [])]
    elif history.entity_type == "location":
        bible.locations = [LocationEntry(**_filter_fields(LocationEntry, item)) for item in view.get("locations", [])]
    else:
        return False
    bible.facts = [WorldFact(**_filter_fields(WorldFact, item)) for item in view.get("facts", [])]
    affected = {history.target_id, *history.source_ids}
    bible.manual_overrides = [item for item in bible.manual_overrides if item.entity_id not in affected]
    history.reverted = True
    for candidate in bible.duplicate_candidates:
        if candidate.get("merge_id") == history.id:
            candidate["status"] = "pending"
            candidate.pop("merge_id", None)
    _ensure_entity_metadata(bible)
    bible.consistency_warnings = audit_world_bible_consistency(bible)
    return True

def _estimate_prompt_tokens(text: str) -> int:
    """Local deterministic token estimate suitable for enforcing a hard context budget."""
    chinese = len(re.findall(r"[\u4e00-\u9fff]", text or ""))
    ascii_chunks = re.findall(r"[A-Za-z0-9_]+|[^\s\u4e00-\u9fff]", text or "")
    return chinese + sum(max(1, (len(chunk) + 3) // 4) for chunk in ascii_chunks)


def _fit_lines_to_token_budget(lines: list[str], budget: int) -> tuple[str, int, int]:
    selected: list[str] = []
    used = 0
    omitted = 0
    for line in lines:
        cost = _estimate_prompt_tokens(line + "\n")
        if selected and used + cost > budget:
            omitted += 1
            continue
        if not selected and cost > budget:
            line = line[:max(80, budget)]
            cost = _estimate_prompt_tokens(line)
        selected.append(line)
        used += cost
    return "\n".join(selected).strip(), used, omitted

def format_relevant_world_bible_for_prompt(
    bible: WorldBible,
    query_text: str = "",
    *,
    max_characters: int = 8,
    max_locations: int = 5,
    max_threads: int = 6,
    active_chapters: set[int] | None = None,
    target_chapter: int = 0,
    token_budget: int = 4000,
    return_diagnostics: bool = False,
):
    """Local hybrid retrieval with authority separation and a deterministic token budget."""
    _ensure_entity_metadata(bible)
    materialize_current_facts(bible, target_chapter)
    query = _norm_key(query_text)
    active_chapters = {int(ch) for ch in (active_chapters or set()) if int(ch or 0) > 0}

    def in_scope(data: dict) -> bool:
        if not active_chapters:
            return True
        values = {
            data.get("last_updated_chapter"), data.get("source_chapter"),
            data.get("first_appearance"), data.get("last_touched_chapter"),
            data.get("introduced_chapter"), data.get("opened_chapter"), data.get("chapter"),
        }
        chapters = {int(value) for value in values if str(value).isdigit()}
        return not chapters or bool(chapters & active_chapters)

    def text_score(*values) -> int:
        score = 0
        for value in values:
            text = str(value or "")
            normalized = _norm_key(text)
            if not normalized:
                continue
            if normalized in query:
                score += 10
            if query and query in normalized:
                score += 6
            for token in re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z0-9_]+", text):
                if len(_norm_key(token)) >= 2 and _norm_key(token) in query:
                    score += 2
        return score

    importance = {"major": 6, "normal": 3, "minor": 0}
    visible_chars = [item for item in bible.characters if not item.hidden and in_scope(item.__dict__)]
    visible_locs = [item for item in bible.locations if not item.hidden and in_scope(item.__dict__)]
    visible_threads = [item for item in bible.active_plot_threads if not item.hidden and in_scope(item.__dict__)]
    visible_settings = [item for item in bible.key_worldbuilding_passages if not item.get("hidden") and in_scope(item)]
    visible_foreshadowing = [item for item in bible.global_foreshadowing if not item.get("hidden") and in_scope(item)]

    base_char_scores = {
        item.id: text_score(
            item.name, " ".join(item.aliases), item.traits, item.current_goal,
            item.current_location, item.recent_action, item.knowledge_state,
            " ".join(item.unresolved_conflicts),
        ) + importance.get(item.importance, 3)
        for item in visible_chars
    }
    direct_ids = {entity_id for entity_id, score in base_char_scores.items() if score >= 8}
    related_names = {
        _norm_key(rel.target)
        for item in visible_chars if item.id in direct_ids
        for rel in item.relationships if rel.target
    }
    related_ids = {
        item.id for item in visible_chars
        if _norm_key(item.name) in related_names or any(_norm_key(alias) in related_names for alias in item.aliases)
    }
    char_ranked = sorted(
        visible_chars,
        key=lambda item: (
            base_char_scores[item.id] + (7 if item.id in related_ids else 0)
            + (3 if item.current_goal or item.current_location or item.recent_action else 0),
            item.last_updated_chapter,
        ),
        reverse=True,
    )
    selected_chars: list[CharacterEntry] = []
    for item in [*[c for c in char_ranked if c.importance == "major"][:4], *char_ranked]:
        if item.id not in {old.id for old in selected_chars}:
            selected_chars.append(item)
        if len(selected_chars) >= max_characters:
            break
    selected_names = {_norm_key(item.name) for item in selected_chars}
    selected_locations = sorted(
        visible_locs,
        key=lambda item: (
            text_score(item.name, item.description, item.significance, item.atmosphere)
            + (8 if _norm_key(item.name) in {_norm_key(c.current_location) for c in selected_chars} else 0),
            item.last_updated_chapter,
        ),
        reverse=True,
    )[:max_locations]
    selected_threads = sorted(
        visible_threads,
        key=lambda item: (
            text_score(item.name, item.description, " ".join(item.involved_characters), " ".join(item.foreshadowing_related))
            + importance.get(item.importance, 3)
            + (6 if item.status == "active" else 0)
            + (5 if any(_norm_key(name) in selected_names for name in item.involved_characters) else 0),
            item.last_touched_chapter,
        ),
        reverse=True,
    )[:max_threads]
    selected_rules = sorted(
        [
            item for item in bible.world_rules
            if not item.hidden
            and (not target_chapter or not item.valid_from or item.valid_from <= target_chapter)
            and (not target_chapter or not item.valid_to or item.valid_to >= target_chapter)
        ],
        key=lambda item: (item.locked, item.priority, text_score(item.name, item.content, " ".join(item.exceptions))),
        reverse=True,
    )[:8]
    selected_settings = sorted(
        visible_settings,
        key=lambda item: (
            bool(item.get("locked")),
            text_score(
                item.get("topic"), item.get("core_summary"), item.get("full_passage") or item.get("passage"),
                " ".join(item.get("constraints", [])), " ".join(item.get("keywords", [])),
            ),
            int(item.get("chapter", 0) or 0),
        ),
        reverse=True,
    )[:5]
    selected_foreshadowing = sorted(
        [item for item in visible_foreshadowing if item.get("status", "open") not in ("resolved", "已回收")],
        key=lambda item: (text_score(item.get("hint"), item.get("relates_to")), int(item.get("last_touched_chapter", 0) or 0)),
        reverse=True,
    )[:8]

    lines: list[str] = []
    clock = bible.story_clock or {}
    clock_text = "；".join(
        f"{label}={clock.get(key)}" for key, label in (
            ("current_date", "当前日期"), ("time_of_day", "当前时段"),
            ("elapsed_time", "累计流逝"), ("story_phase", "故事阶段"),
            ("calendar_system", "纪年体系"),
        ) if clock.get(key)
    )
    if clock_text or selected_rules or selected_settings:
        lines.append("【硬约束 / Canon 规则】")
        if clock_text:
            lines.append(f"- 故事时钟：{clock_text}")
        for rule in selected_rules:
            text = f"- 规则：{rule.content[:180]}"
            if rule.exceptions:
                text += " | 例外：" + "；".join(rule.exceptions[:2])
            lines.append(text)
        for item in selected_settings:
            if item.get("locked") or item.get("knowledge_type") == "constraint":
                summary = item.get("core_summary") or item.get("passage", "")
                lines.append(f"- 设定·{item.get('topic', '')}：{str(summary)[:300]}")
                if item.get("constraints"):
                    lines.append("  约束：" + "；".join(str(value) for value in item.get("constraints", [])[:8]))
                full_passage = str(item.get("full_passage") or item.get("passage") or "")
                if full_passage and full_passage != summary:
                    lines.append(f"  完整设定原文：{full_passage[:1800]}")

    if selected_chars or selected_locations or selected_threads:
        lines.append("\n【当前章节相关 Canon 事实】")
        for item in selected_chars:
            states = []
            for label, value in (
                ("状态", item.status), ("位置", item.current_location), ("目标", item.current_goal),
                ("情绪", item.current_emotion), ("已知", item.knowledge_state), ("近况", item.recent_action),
                ("年龄", item.current_age), ("阶段", item.life_stage),
            ):
                if value and not (label == "状态" and value == "alive"):
                    states.append(f"{label}={str(value)[:60]}")
            lines.append(f"- 角色·{item.name}：{item.traits[:100]}" + (" | " + "；".join(states) if states else ""))
        for item in selected_locations:
            lines.append(f"- 地点·{item.name}：{item.description[:120]}" + (f" | 氛围={item.atmosphere[:50]}" if item.atmosphere else ""))
        for item in selected_threads:
            lines.append(f"- 剧情线·{item.name} [{item.status}]：{item.description[:130]}")
        for event in bible.timeline[-5:]:
            if in_scope(event.__dict__):
                lines.append(f"- 第{event.chapter}章事件：{event.event[:120]}")

    inference_lines = []
    for item in selected_chars:
        if item.motivation or item.arc:
            inference_lines.append(f"- {item.name}：动机={item.motivation[:70]}；弧光={item.arc[:70]}")
    if inference_lines:
        lines.append("\n【推断信息（可参考，不是既定事实）】")
        lines.extend(inference_lines[:6])

    plan_lines = []
    for item in selected_threads:
        if item.expected_payoff or item.payoff_hint:
            plan_lines.append(f"- {item.name}：预期回收={item.expected_payoff[:70]}；推进建议={item.payoff_hint[:70]}")
    for item in selected_foreshadowing:
        plan_lines.append(
            f"- 伏笔·{item.get('hint', '')[:60]} [{item.get('status', 'open')}]"
            f"：推进={item.get('next_step', '')[:70]}；限制={item.get('reveal_rule', '')[:70]}"
        )
    if plan_lines:
        lines.append("\n【作者规划（未来建议，不得写成已经发生）】")
        lines.extend(plan_lines[:10])

    if bible.consistency_warnings:
        lines.append("\n【一致性风险】")
        for warning in bible.consistency_warnings[:5]:
            lines.append(f"- [{warning.get('severity', 'minor')}] {warning.get('type', '冲突')}：{warning.get('message', '')[:130]}")

    text, estimated_tokens, omitted_lines = _fit_lines_to_token_budget(lines, max(256, int(token_budget or 4000)))
    diagnostics = {
        "query": query_text,
        "target_chapter": int(target_chapter or 0),
        "token_budget": int(token_budget or 4000),
        "estimated_tokens": estimated_tokens,
        "omitted_lines": omitted_lines,
        "selected": {
            "characters": [item.id for item in selected_chars],
            "locations": [item.id for item in selected_locations],
            "threads": [item.id for item in selected_threads],
            "rules": [item.id for item in selected_rules],
            "settings": [item.get("id", "") for item in selected_settings],
            "foreshadowing": [item.get("id", "") for item in selected_foreshadowing],
        },
        "reasons": {
            "direct_character_ids": sorted(direct_ids),
            "relationship_expansion_ids": sorted(related_ids),
            "active_scope": sorted(active_chapters),
        },
    }
    bible.diagnostics["last_retrieval"] = diagnostics
    return (text, diagnostics) if return_diagnostics else text

# ========== AI 提取与合并 ==========

_IMPORTANCE_RANK = {"major": 3, "normal": 2, "minor": 1}


def _higher_importance(a: str, b: str) -> str:
    """返回两者中更高的 importance 等级"""
    return a if _IMPORTANCE_RANK.get(a, 0) >= _IMPORTANCE_RANK.get(b, 0) else b


def _verify_verbatim(text: str, source: str) -> str:
    """将 LLM 输出的引用文本与源文本做模糊匹配，替换为精确原文"""
    if not text or not source:
        return text
    # 如果原文已含该文本则直接返回
    if text in source:
        return text
    # 用 difflib 找最佳匹配
    matches = difflib.SequenceMatcher(None, text, source).get_matching_blocks()
    if matches:
        best = max(matches, key=lambda m: m.size)
        if best.size >= max(5, len(text) * 0.7) and best.b >= 0:
            return source[best.b:best.b + best.size]
    return text


def _merge_list_dedup(target: list, source: list) -> None:
    """向 target 追加 source 中不重复的字符串"""
    seen = set(target)
    for item in source:
        if isinstance(item, str) and item not in seen:
            target.append(item)
            seen.add(item)


def _as_list(value) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        return [value] if value.strip() else []
    return [value]


def _norm_key(text: str) -> str:
    """用于名称/别名匹配的轻量归一化。"""
    return re.sub(r"\s+", "", (text or "").strip()).lower()


def _append_text_unique(current: str, new_text: str, limit: int = 800) -> str:
    """保留旧信息，将新增描述拼接进去，避免同名更新直接覆盖。"""
    current = (current or "").strip()
    new_text = (new_text or "").strip()
    if not new_text:
        return current[:limit]
    if not current:
        return new_text[:limit]
    if new_text in current:
        return current[:limit]
    if current in new_text:
        return new_text[:limit]
    return f"{current}\n{new_text}"[:limit]


def _fact_source_record(value, chapter_num: int, chapter_version: int = 0) -> dict | None:
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        source_value = value
        empty = not bool(value)
    else:
        source_value = str(value).strip()
        empty = not bool(source_value)
    if empty:
        return None
    return {
        "value": source_value,
        "source_chapter": int(chapter_num or 0),
        "source_version": int(chapter_version or 0),
    }


def _record_fact_source(obj, field_name: str, value, chapter_num: int, chapter_version: int = 0) -> None:
    """记录字段级来源；不改变旧字段值，保证旧代码继续可用。"""
    if not hasattr(obj, "fact_sources"):
        return
    if not chapter_num:
        return
    values = value if isinstance(value, list) else [value]
    bucket = getattr(obj, "fact_sources", None)
    if not isinstance(bucket, dict):
        bucket = {}
        setattr(obj, "fact_sources", bucket)
    entries = bucket.setdefault(field_name, [])
    for item in values:
        record = _fact_source_record(item, chapter_num, chapter_version)
        if not record:
            continue
        if not any(
            existing.get("value") == record["value"]
            and int(existing.get("source_chapter", 0) or 0) == record["source_chapter"]
            and int(existing.get("source_version", 0) or 0) == record["source_version"]
            for existing in entries
            if isinstance(existing, dict)
        ):
            entries.append(record)


def _merge_fact_sources(obj, incoming: dict | None) -> None:
    if not hasattr(obj, "fact_sources") or not isinstance(incoming, dict):
        return
    bucket = getattr(obj, "fact_sources", None)
    if not isinstance(bucket, dict):
        bucket = {}
        setattr(obj, "fact_sources", bucket)
    for field_name, records in incoming.items():
        if not isinstance(records, list):
            continue
        target = bucket.setdefault(str(field_name), [])
        for record in records:
            if not isinstance(record, dict):
                continue
            if record not in target:
                target.append(dict(record))


def _touch_source(obj, chapter_num: int, chapter_version: int = 0) -> None:
    if hasattr(obj, "source_chapter") and not getattr(obj, "source_chapter", 0):
        setattr(obj, "source_chapter", chapter_num)
    if hasattr(obj, "source_version") and not getattr(obj, "source_version", 0):
        setattr(obj, "source_version", chapter_version)
    if hasattr(obj, "last_updated_chapter"):
        setattr(obj, "last_updated_chapter", chapter_num)
    if hasattr(obj, "last_updated_version"):
        setattr(obj, "last_updated_version", chapter_version)


def _foreshadow_status(existing_status: str, new_status: str) -> str:
    order = {
        "open": 1,
        "noticed": 2,
        "advanced": 3,
        "dormant": 2,
        "resolved": 4,
        "已埋下": 1,
        "已被注意": 2,
        "推进中": 3,
        "暂缓": 2,
        "已回收": 4,
    }
    if not new_status:
        return existing_status or "open"
    if not existing_status:
        return new_status
    return new_status if order.get(new_status, 0) >= order.get(existing_status, 0) else existing_status


def _merge_foreshadowing(target: list[dict], item: dict, chapter_num: int, chapter_version: int = 0) -> None:
    hint = (item.get("hint") or "").strip()
    if not hint:
        return
    existing = next((f for f in target if _norm_key(f.get("hint", "")) == _norm_key(hint)), None)
    payload = {
        "hint": hint[:80],
        "relates_to": (item.get("relates_to") or "")[:40],
        "status": item.get("status") or "open",
        "introduced_chapter": int(item.get("introduced_chapter", 0) or chapter_num),
        "introduced_version": int(item.get("introduced_version", 0) or chapter_version),
        "last_touched_chapter": int(item.get("last_touched_chapter", 0) or chapter_num),
        "last_touched_version": int(item.get("last_touched_version", 0) or chapter_version),
        "next_step": (item.get("next_step") or "")[:120],
        "reveal_rule": (item.get("reveal_rule") or "")[:120],
    }
    if not existing:
        target.append(payload)
        return
    existing["status"] = _foreshadow_status(existing.get("status", "open"), payload["status"])
    if payload["relates_to"]:
        existing["relates_to"] = _append_text_unique(existing.get("relates_to", ""), payload["relates_to"], 80)
    existing["introduced_chapter"] = int(existing.get("introduced_chapter", 0) or payload["introduced_chapter"])
    existing["introduced_version"] = int(existing.get("introduced_version", 0) or payload["introduced_version"])
    existing["last_touched_chapter"] = max(
        int(existing.get("last_touched_chapter", 0) or 0),
        payload["last_touched_chapter"],
    )
    existing["last_touched_version"] = payload["last_touched_version"] or existing.get("last_touched_version", 0)
    if payload["next_step"]:
        existing["next_step"] = payload["next_step"]
    if payload["reveal_rule"]:
        existing["reveal_rule"] = payload["reveal_rule"]


def _character_keys(ch: CharacterEntry) -> set[str]:
    keys = {_norm_key(ch.name)}
    keys.update(_norm_key(alias) for alias in ch.aliases if alias)
    return {k for k in keys if k}


def _find_character_by_name_or_alias(
    characters: list[CharacterEntry], name: str, aliases: list[str]
) -> CharacterEntry | None:
    incoming = {_norm_key(name)}
    incoming.update(_norm_key(alias) for alias in aliases if alias)
    incoming = {k for k in incoming if k}
    for existing in characters:
        if _character_keys(existing) & incoming:
            return existing
    return None


def _merge_relationships(target: list[Relationship], source_items: list[dict | Relationship]) -> None:
    """按关系对象+关系类型合并关系描述。"""
    for item in source_items:
        if isinstance(item, Relationship):
            rel = item
        elif not isinstance(item, dict):
            continue
        else:
            rel_fields = {k: v for k, v in item.items() if k in Relationship.__dataclass_fields__}
            rel = Relationship(**rel_fields)
        if not rel.target:
            continue
        existing = next(
            (
                r for r in target
                if _norm_key(r.target) == _norm_key(rel.target)
                and (not rel.type or not r.type or r.type == rel.type)
            ),
            None,
        )
        if existing:
            if rel.type and not existing.type:
                existing.type = rel.type
            existing.description = _append_text_unique(existing.description, rel.description, 200)
        else:
            target.append(rel)


def _merge_character_entry(
    existing: CharacterEntry,
    ch_data: dict,
    chapter_content: str,
    chapter_num: int = 0,
    chapter_version: int = 0,
) -> None:
    """稳定合并角色字段，不用新提取结果覆盖旧信息。"""
    _merge_fact_sources(existing, ch_data.get("fact_sources"))
    name = ch_data.get("name", "").strip()
    if name and name != existing.name and name not in existing.aliases:
        existing.aliases.append(name)
        _record_fact_source(existing, "aliases", name, chapter_num, chapter_version)
    for alias in ch_data.get("aliases", []):
        alias = str(alias).strip()
        if alias and alias != existing.name and alias not in existing.aliases:
            existing.aliases.append(alias)
            _record_fact_source(existing, "aliases", alias, chapter_num, chapter_version)
    existing.traits = _append_text_unique(existing.traits, ch_data.get("traits", "")[:500], 1000)
    _record_fact_source(existing, "traits", ch_data.get("traits", "")[:500], chapter_num, chapter_version)
    if ch_data.get("status") in ("dead", "missing", "transformed"):
        existing.status = ch_data["status"]
        _record_fact_source(existing, "status", ch_data["status"], chapter_num, chapter_version)
    existing.importance = _higher_importance(existing.importance, ch_data.get("importance", "normal"))
    _record_fact_source(existing, "importance", ch_data.get("importance", "normal"), chapter_num, chapter_version)
    key_details = [_verify_verbatim(kd, chapter_content) for kd in ch_data.get("key_details", [])]
    key_dialogues = [_verify_verbatim(kd, chapter_content) for kd in ch_data.get("key_dialogues", [])]
    _merge_list_dedup(existing.key_details, key_details)
    _merge_list_dedup(existing.key_dialogues, key_dialogues)
    _record_fact_source(existing, "key_details", key_details, chapter_num, chapter_version)
    _record_fact_source(existing, "key_dialogues", key_dialogues, chapter_num, chapter_version)
    existing.motivation = _append_text_unique(existing.motivation, ch_data.get("motivation", "")[:200], 400)
    existing.arc = _append_text_unique(existing.arc, ch_data.get("arc", "")[:200], 400)
    _record_fact_source(existing, "motivation", ch_data.get("motivation", "")[:200], chapter_num, chapter_version)
    _record_fact_source(existing, "arc", ch_data.get("arc", "")[:200], chapter_num, chapter_version)
    for field_name, limit in (
        ("birth_date", 60),
        ("current_age", 60),
        ("age_basis", 120),
        ("life_stage", 100),
        ("current_location", 100),
        ("current_goal", 200),
        ("current_emotion", 200),
        ("recent_action", 200),
        ("knowledge_state", 200),
    ):
        value = str(ch_data.get(field_name, "")).strip()
        if value:
            setattr(existing, field_name, value[:limit])
            _record_fact_source(existing, field_name, value[:limit], chapter_num, chapter_version)
    conflicts = [str(item)[:50] for item in _as_list(ch_data.get("unresolved_conflicts", [])) if item]
    _merge_list_dedup(
        existing.unresolved_conflicts,
        conflicts,
    )
    _record_fact_source(existing, "unresolved_conflicts", conflicts, chapter_num, chapter_version)
    _merge_relationships(existing.relationships, ch_data.get("relationships", []))
    _record_fact_source(existing, "relationships", ch_data.get("relationships", []), chapter_num, chapter_version)
    if chapter_num:
        _touch_source(existing, chapter_num, chapter_version)


def _plot_thread_status(existing_status: str, new_status: str) -> str:
    """稳定合并剧情线状态：已解决不被后续粗糙提取重新打开。"""
    if existing_status == "resolved":
        return "resolved"
    if new_status == "resolved":
        return "resolved"
    if new_status == "active":
        return "active"
    if existing_status == "active":
        return "active"
    return new_status if new_status in ("active", "resolved", "dormant") else existing_status


def _merge_plot_thread(
    existing: PlotThread,
    pt_data: dict,
    chapter_content: str,
    chapter_num: int = 0,
    chapter_version: int = 0,
) -> None:
    _merge_fact_sources(existing, pt_data.get("fact_sources"))
    existing.status = _plot_thread_status(existing.status, pt_data.get("status", "active"))
    _record_fact_source(existing, "status", pt_data.get("status", "active"), chapter_num, chapter_version)
    existing.description = _append_text_unique(existing.description, pt_data.get("description", "")[:300], 800)
    _record_fact_source(existing, "description", pt_data.get("description", "")[:300], chapter_num, chapter_version)
    for char in pt_data.get("involved_characters", []):
        if char and char not in existing.involved_characters:
            existing.involved_characters.append(char)
            _record_fact_source(existing, "involved_characters", char, chapter_num, chapter_version)
    existing.importance = _higher_importance(existing.importance, pt_data.get("importance", "normal"))
    _record_fact_source(existing, "importance", pt_data.get("importance", "normal"), chapter_num, chapter_version)
    if existing.opened_chapter == 0:
        existing.opened_chapter = int(pt_data.get("opened_chapter", 0) or 0)
        _record_fact_source(existing, "opened_chapter", existing.opened_chapter, chapter_num, chapter_version)
    if pt_data.get("last_touched_chapter"):
        existing.last_touched_chapter = max(existing.last_touched_chapter, int(pt_data.get("last_touched_chapter", 0) or 0))
        _record_fact_source(existing, "last_touched_chapter", existing.last_touched_chapter, chapter_num, chapter_version)
    existing.expected_payoff = _append_text_unique(existing.expected_payoff, pt_data.get("expected_payoff", "")[:100], 300)
    existing.payoff_hint = _append_text_unique(existing.payoff_hint, pt_data.get("payoff_hint", "")[:100], 300)
    _record_fact_source(existing, "expected_payoff", pt_data.get("expected_payoff", "")[:100], chapter_num, chapter_version)
    _record_fact_source(existing, "payoff_hint", pt_data.get("payoff_hint", "")[:100], chapter_num, chapter_version)
    key_details = [_verify_verbatim(kd, chapter_content) for kd in pt_data.get("key_details", [])]
    foreshadowing_related = [fr[:50] for fr in pt_data.get("foreshadowing_related", [])]
    _merge_list_dedup(existing.key_details, key_details)
    _merge_list_dedup(existing.foreshadowing_related, foreshadowing_related)
    _record_fact_source(existing, "key_details", key_details, chapter_num, chapter_version)
    _record_fact_source(existing, "foreshadowing_related", foreshadowing_related, chapter_num, chapter_version)
    if chapter_num:
        if existing.last_touched_chapter == 0:
            existing.last_touched_chapter = chapter_num
        _touch_source(existing, chapter_num, chapter_version)


def _detect_duplicate_characters(
    bible: WorldBible, client, model: str = "deepseek-v4-flash",
    global_user_prompt: str = "",
) -> list[list[str]]:
    """用 AI 检测世界书中重复的角色（不同名称指向同一人物）"""
    if len(bible.characters) < 2:
        return []

    char_lines = []
    for c in bible.characters:
        aliases = "、".join(c.aliases) if c.aliases else "无"
        traits_short = c.traits[:80] if c.traits else "无"
        char_lines.append(f"- {c.name} (别名: {aliases}, 描述: {traits_short})")

    prompt = f"""以下是一部小说的角色列表，请仔细阅读并判断哪些角色指向同一个人物（因不同章节提取时用了不同称呼）。

角色列表：
{chr(10).join(char_lines)}

请将指向同一人物的角色名分组，输出JSON格式：
{{"groups": [["角色A", "角色B"], ["角色C", "角色D", "角色E"]]}}

规则：
- 只有确定指向同一人物时才归为一组
- 每个角色名只能出现在一个组中
- 不属于任何组的角色不要列出
- 别名不算独立角色，无需合并
- 仅当角色名不同但实际相同才需合并"""
    if global_user_prompt.strip():
        prompt += f"\n\n用户偏好参考: {global_user_prompt}"

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=2000,
            temperature=0.1,
        )
        raw = response.choices[0].message.content or "{}"
        if "```json" in raw:
            raw = raw.split("```json")[1].split("```")[0].strip()
        elif "```" in raw:
            raw = raw.split("```")[1].split("```")[0].strip()
        data = json.loads(raw)
        return data.get("groups", [])
    except Exception:
        return []


def _merge_character_group(
    characters: list[CharacterEntry], group_names: list[str]
) -> tuple[CharacterEntry, list[int]] | None:
    """通过拼接合并一组重复角色，返回(合并后的角色, 被合并角色的索引列表)"""
    matched = [(i, c) for i, c in enumerate(characters) if c.name in group_names]
    if not matched:
        return None

    def _completeness(c):
        return len(c.traits) + len(c.key_details) * 50 + len(c.key_dialogues) * 30 + len(c.relationships) * 20

    matched.sort(key=lambda x: _completeness(x[1]), reverse=True)
    base_idx, base = matched[0]

    for _, other in matched[1:]:
        _merge_fact_sources(base, getattr(other, "fact_sources", {}))
        for alias in other.aliases:
            if alias not in base.aliases:
                base.aliases.append(alias)
        if other.name not in base.aliases:
            base.aliases.append(other.name)

        if other.traits:
            base_lines = set(base.traits.split("\n")) if base.traits else set()
            new_lines = [l for l in other.traits.split("\n") if l.strip() and l not in base_lines]
            if new_lines:
                base.traits = base.traits + "\n" + "\n".join(new_lines) if base.traits else "\n".join(new_lines)

        base.importance = _higher_importance(base.importance, other.importance)
        if other.status != "alive":
            base.status = other.status
        if other.first_appearance > 0 and (base.first_appearance == 0 or other.first_appearance < base.first_appearance):
            base.first_appearance = other.first_appearance

        _merge_list_dedup(base.key_details, other.key_details)
        _merge_list_dedup(base.key_dialogues, other.key_dialogues)

        if other.motivation and other.motivation not in (base.motivation or ""):
            base.motivation = f"{base.motivation}；{other.motivation}" if base.motivation else other.motivation
        if other.arc and other.arc not in (base.arc or ""):
            base.arc = f"{base.arc}；{other.arc}" if base.arc else other.arc
        for field_name in (
            "current_location",
            "current_goal",
            "current_emotion",
            "recent_action",
            "knowledge_state",
        ):
            value = getattr(other, field_name, "")
            if value:
                setattr(base, field_name, value)
        _merge_list_dedup(base.unresolved_conflicts, other.unresolved_conflicts)

        for rel in other.relationships:
            existing = next((r for r in base.relationships if r.target == rel.target), None)
            if existing:
                if rel.description and rel.description not in existing.description:
                    existing.description = f"{existing.description}；{rel.description}"
            else:
                base.relationships.append(rel)
        if other.notes:
            base.notes = f"{base.notes}\n{other.notes}".strip()

    remove_indices = [idx for idx, _ in matched[1:]]
    return base, remove_indices


def dedup_world_bible_characters(
    bible: WorldBible, client=None, model: str = "deepseek-v4-flash",
    global_user_prompt: str = "",
) -> WorldBible:
    """检测并合并世界书中重复的角色（AI 检测 + 仅拼接，不压缩）"""
    if len(bible.characters) < 2:
        return bible
    groups = _detect_duplicate_characters(bible, client, model, global_user_prompt)
    if not groups or not any(len(g) > 1 for g in groups):
        return bible

    to_remove = set()
    for group in groups:
        if len(group) < 2:
            continue
        result = _merge_character_group(bible.characters, group)
        if result:
            _, remove_indices = result
            to_remove.update(remove_indices)

    bible.characters = [c for i, c in enumerate(bible.characters) if i not in to_remove]
    return bible


def _detect_duplicate_locations(
    bible: WorldBible, client, model: str = "deepseek-v4-flash",
    global_user_prompt: str = "",
) -> list[list[str]]:
    """用 AI 检测世界书中重复的地点（不同名称指向同一地点）"""
    if len(bible.locations) < 2:
        return []

    loc_lines = []
    for l in bible.locations:
        desc_short = l.description[:80] if l.description else "无"
        loc_lines.append(f"- {l.name} (描述: {desc_short})")

    prompt = f"""以下是一部小说的地点列表，请仔细阅读并判断哪些地点指向同一个地方（因不同章节提取时用了不同称呼）。

地点列表：
{chr(10).join(loc_lines)}

请将指向同一地点的地点名分组，输出JSON格式：
{{"groups": [["地点A", "地点B"], ["地点C", "地点D", "地点E"]]}}

规则：
- 只有确定指向同一地点时才归为一组
- 每个地点名只能出现在一个组中
- 不属于任何组的地点不要列出
- 仅当地点名不同但实际相同才需合并"""
    if global_user_prompt.strip():
        prompt += f"\n\n用户偏好参考: {global_user_prompt}"

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=2000,
            temperature=0.1,
        )
        raw = response.choices[0].message.content or "{}"
        if "```json" in raw:
            raw = raw.split("```json")[1].split("```")[0].strip()
        elif "```" in raw:
            raw = raw.split("```")[1].split("```")[0].strip()
        data = json.loads(raw)
        return data.get("groups", [])
    except Exception:
        return []


def _merge_location_group(
    locations: list[LocationEntry], group_names: list[str]
) -> tuple[LocationEntry, list[int]] | None:
    """通过拼接合并一组重复地点，返回(合并后的地点, 被合并地点的索引列表)"""
    matched = [(i, l) for i, l in enumerate(locations) if l.name in group_names]
    if not matched:
        return None

    def _completeness(l):
        return len(l.description) + len(l.key_details) * 50 + (20 if l.atmosphere else 0)

    matched.sort(key=lambda x: _completeness(x[1]), reverse=True)
    base_idx, base = matched[0]

    for _, other in matched[1:]:
        _merge_fact_sources(base, getattr(other, "fact_sources", {}))
        if other.description and other.description not in base.description:
            base.description = f"{base.description}\n{other.description}" if base.description else other.description
        if other.significance and other.significance not in base.significance:
            base.significance = f"{base.significance}\n{other.significance}" if base.significance else other.significance
        if other.first_appearance > 0 and (base.first_appearance == 0 or other.first_appearance < base.first_appearance):
            base.first_appearance = other.first_appearance
        _merge_list_dedup(base.key_details, other.key_details)
        if other.atmosphere and other.atmosphere not in base.atmosphere:
            base.atmosphere = f"{base.atmosphere}\n{other.atmosphere}" if base.atmosphere else other.atmosphere

    remove_indices = [idx for idx, _ in matched[1:]]
    return base, remove_indices


def dedup_world_bible_locations(
    bible: WorldBible, client=None, model: str = "deepseek-v4-flash",
    global_user_prompt: str = "",
) -> WorldBible:
    """检测并合并世界书中重复的地点（AI 检测 + 仅拼接，不压缩）"""
    if len(bible.locations) < 2:
        return bible
    groups = _detect_duplicate_locations(bible, client, model, global_user_prompt)
    if not groups or not any(len(g) > 1 for g in groups):
        return bible

    to_remove = set()
    for group in groups:
        if len(group) < 2:
            continue
        result = _merge_location_group(bible.locations, group)
        if result:
            _, remove_indices = result
            to_remove.update(remove_indices)

    bible.locations = [l for i, l in enumerate(bible.locations) if i not in to_remove]
    return bible


def audit_world_bible_consistency(bible: WorldBible) -> list[dict]:
    """规则型世界书一致性/健康检查。用于更新后提示风险，不阻断写作。"""
    warnings: list[dict] = []

    def add(severity: str, issue_type: str, message: str, related: list[str] | None = None) -> None:
        warnings.append({
            "severity": severity,
            "type": issue_type,
            "message": message,
            "related": related or [],
        })

    def has_source(data: dict) -> bool:
        return bool(
            data.get("source_chapter")
            or data.get("source_version")
            or data.get("last_updated_chapter")
            or data.get("last_updated_version")
            or data.get("first_appearance")
            or data.get("chapter")
            or data.get("introduced_chapter")
            or data.get("opened_chapter")
        )

    def source_chapter(data: dict) -> int:
        for key in (
            "last_updated_chapter",
            "source_chapter",
            "first_appearance",
            "last_touched_chapter",
            "introduced_chapter",
            "opened_chapter",
            "chapter",
        ):
            try:
                value = int(data.get(key, 0) or 0)
            except (TypeError, ValueError):
                value = 0
            if value > 0:
                return value
        return 0

    def source_version(data: dict) -> int:
        for key in ("last_updated_version", "source_version", "version"):
            try:
                value = int(data.get(key, 0) or 0)
            except (TypeError, ValueError):
                value = 0
            if value > 0:
                return value
        return 0

    def has_chapter_snapshot(chapter: int, version: int = 0) -> bool:
        if chapter <= 0:
            return False
        entries = getattr(bible, "chapter_world_entries", {}) or {}
        if version:
            return f"ch{chapter:04d}_v{version:03d}" in entries
        prefix = f"ch{chapter:04d}_v"
        return any(str(key).startswith(prefix) for key in entries)

    def check_source_health(kind: str, title: str, data: dict) -> None:
        if not has_source(data):
            add("info", "缺少来源", f"{kind}「{title}」缺少来源章节/版本，后续按章节追溯会不准确。", [title])
            return
        chapter = source_chapter(data)
        version = source_version(data)
        if chapter and not has_chapter_snapshot(chapter, version):
            version_text = f" v{version}" if version else ""
            add("info", "快照缺失", f"{kind}「{title}」指向第{chapter}章{version_text}，但没有对应章节世界书快照。", [title, f"第{chapter}章"])

    def check_fact_source_health(kind: str, title: str, data: dict, fields: tuple[str, ...]) -> None:
        fact_sources = data.get("fact_sources")
        missing = [
            field_name
            for field_name in fields
            if data.get(field_name)
            and (
                not isinstance(fact_sources, dict)
                or not isinstance(fact_sources.get(field_name), list)
                or not fact_sources.get(field_name)
            )
        ]
        if missing:
            add(
                "info",
                "字段来源待补全",
                f"{kind}「{title}」有 {len(missing)} 个主要字段缺少章节级来源，可通过按活跃路径同步世界书补全。",
                [title],
            )

    category_counts = {
        "角色": len(bible.characters),
        "地点": len(bible.locations),
        "规则": len(bible.rules),
        "时间线": len(bible.timeline),
        "剧情线": len(bible.active_plot_threads),
        "设定": len(bible.key_worldbuilding_passages),
        "伏笔": len(bible.global_foreshadowing),
        "关键对话": len(bible.global_key_dialogues),
        "时间状态": 1 if bible.story_clock else 0,
    }
    if not any(category_counts.values()):
        add("info", "世界书为空", "世界书还没有任何结构化条目，生成时只能依赖剧情摘要或正文上下文。")
    else:
        for label, count in category_counts.items():
            if count == 0:
                add("info", "栏目为空", f"{label}栏目暂时没有条目。")

    # 同名/同别名角色碰撞
    seen_chars: dict[str, str] = {}
    for ch in bible.characters:
        ch_data = asdict(ch)
        check_source_health("角色", ch.name or "未命名角色", ch_data)
        check_fact_source_health(
            "角色",
            ch.name or "未命名角色",
            ch_data,
            (
                "traits", "status", "motivation", "arc", "birth_date", "current_age",
                "age_basis", "life_stage", "current_location",
                "current_goal", "current_emotion", "recent_action",
                "knowledge_state", "key_details", "relationships",
            ),
        )
        if ch.importance == "major" and not any([
            ch.current_goal,
            ch.current_location,
            ch.current_emotion,
            ch.recent_action,
            ch.knowledge_state,
        ]):
            add("info", "角色状态缺失", f"重要角色「{ch.name}」缺少当前目标/位置/情绪/近期行动等关键状态。", [ch.name])
        if ch.importance == "major" and not ch.current_age and not ch.life_stage:
            add("info", "年龄阶段缺失", f"重要角色「{ch.name}」缺少当前年龄和人生阶段，跨年或成长剧情容易失真。", [ch.name])
        if ch.current_age and not ch.age_basis and not ch.birth_date:
            add("minor", "年龄依据不足", f"角色「{ch.name}」记录了当前年龄，但没有出生日期或判断依据，时间推进后难以校准。", [ch.name])
        keys = _character_keys(ch)
        for key in keys:
            if key in seen_chars and seen_chars[key] != ch.name:
                add("major", "角色重复", f"「{seen_chars[key]}」与「{ch.name}」可能是同一角色或别名冲突。", [seen_chars[key], ch.name])
                break
            seen_chars[key] = ch.name
        if ch.status == "dead" and any([ch.current_goal, ch.current_location, ch.recent_action]):
            add("major", "角色状态", f"「{ch.name}」状态为 dead，但仍保留当前位置/目标/近期行动，需要确认是否为旧版本残留或复活缺少解释。", [ch.name])
        rel_types: dict[str, set[str]] = {}
        for rel in ch.relationships:
            if not rel.target:
                continue
            rel_types.setdefault(_norm_key(rel.target), set()).add(rel.type or "unknown")
        for target_key, types in rel_types.items():
            meaningful = {t for t in types if t and t != "unknown"}
            if len(meaningful) >= 3:
                add("minor", "关系噪声", f"「{ch.name}」与同一对象存在多种关系标签 {sorted(meaningful)}，可能需要人工合并关系。", [ch.name, target_key])

    # 地点重复/矛盾风险
    seen_locs: dict[str, str] = {}
    for loc in bible.locations:
        loc_data = asdict(loc)
        check_source_health("地点", loc.name or "未命名地点", loc_data)
        check_fact_source_health(
            "地点",
            loc.name or "未命名地点",
            loc_data,
            ("description", "significance", "key_details", "atmosphere"),
        )
        if not loc.description:
            add("info", "地点描述缺失", f"地点「{loc.name}」缺少描述。", [loc.name])
        if not loc.key_details:
            add("info", "地点细节缺失", f"地点「{loc.name}」缺少原文关键细节，后续复用时容易失真。", [loc.name])
        key = _norm_key(loc.name)
        if key in seen_locs and seen_locs[key] != loc.name:
            add("minor", "地点重复", f"「{seen_locs[key]}」与「{loc.name}」名称高度相似，可能需要合并。", [seen_locs[key], loc.name])
        seen_locs[key] = loc.name

    # 时间线顺序与重复事件
    last_chapter = 0
    seen_events: set[str] = set()
    for entry in bible.timeline:
        check_source_health("时间线", entry.event[:40] or "未命名事件", asdict(entry))
        if entry.chapter and entry.chapter < last_chapter:
            add("major", "时间线倒错", f"时间线中第{entry.chapter}章事件出现在第{last_chapter}章事件之后。", [entry.event[:40]])
        last_chapter = max(last_chapter, entry.chapter)
        event_key = _norm_key(entry.event[:80])
        if event_key and event_key in seen_events:
            add("minor", "事件重复", f"时间线中可能重复记录事件：{entry.event[:80]}", [entry.event[:40]])
        seen_events.add(event_key)

    current_chapter = bible.last_updated_chapter or max([t.chapter for t in bible.timeline] or [0])
    for thread in bible.active_plot_threads:
        thread_data = asdict(thread)
        check_source_health("剧情线", thread.name or "未命名剧情线", thread_data)
        check_fact_source_health(
            "剧情线",
            thread.name or "未命名剧情线",
            thread_data,
            (
                "status", "description", "involved_characters", "key_details",
                "foreshadowing_related", "expected_payoff", "payoff_hint",
            ),
        )
        if thread.status == "active" and thread.last_touched_chapter:
            idle = current_chapter - thread.last_touched_chapter
            if idle >= 8:
                add("minor", "剧情线久未推进", f"剧情线「{thread.name}」已 {idle} 章未触达，建议推进、转入 dormant 或收束。", [thread.name])
        elif thread.status == "active":
            add("info", "剧情线触达缺失", f"活跃剧情线「{thread.name}」没有最近触达章节。", [thread.name])
        if thread.status == "resolved" and thread.payoff_hint:
            add("minor", "剧情线状态", f"剧情线「{thread.name}」已 resolved 但仍保留回收提示，可能需要清理。", [thread.name])

    for item in bible.key_worldbuilding_passages:
        title = item.get("topic") or item.get("passage", "")[:40] or "未命名设定"
        check_source_health("设定", title, item)

    for item in bible.global_foreshadowing:
        title = item.get("hint", "")[:60] or "未命名伏笔"
        check_source_health("伏笔", title, item)
        status = item.get("status", "open")
        if status in ("resolved", "已回收"):
            continue
        touched = int(item.get("last_touched_chapter", 0) or item.get("introduced_chapter", 0) or 0)
        if touched and current_chapter - touched >= 8:
            add("minor", "伏笔久未回收", f"伏笔「{item.get('hint', '')[:60]}」已 {current_chapter - touched} 章未推进。", [item.get("hint", "")])
        if status in ("open", "noticed", "advanced") and not item.get("next_step"):
            add("minor", "伏笔缺少推进", f"伏笔「{item.get('hint', '')[:60]}」没有 next_step，后续容易遗忘。", [item.get("hint", "")])

    for item in bible.global_key_dialogues:
        title = item.get("speaker") or item.get("dialogue", "")[:40] or "未命名对话"
        check_source_health("关键对话", title, item)

    rule_seen: set[str] = set()
    for rule in bible.rules:
        key = _norm_key(rule[:120])
        if key in rule_seen:
            add("minor", "规则重复", f"世界观规则可能重复：{rule[:80]}", [rule[:40]])
        rule_seen.add(key)

    # v2 schema, reference integrity and fact-state checks
    entity_objects = [*bible.characters, *bible.locations, *bible.timeline, *bible.active_plot_threads, *bible.world_rules]
    id_counts: dict[str, int] = {}
    for entity in entity_objects:
        entity_id = getattr(entity, "id", "")
        if not entity_id:
            add("error", "实体 ID 缺失", f"{type(entity).__name__} 缺少稳定 ID。")
            continue
        id_counts[entity_id] = id_counts.get(entity_id, 0) + 1
    for entity_id, count in id_counts.items():
        if count > 1:
            add("error", "实体 ID 冲突", f"稳定 ID {entity_id} 被 {count} 个实体重复使用。", [entity_id])

    character_ids = {item.id for item in bible.characters}
    character_names = {_norm_key(name) for item in bible.characters for name in [item.name, *item.aliases] if name}
    for item in bible.characters:
        for rel in item.relationships:
            if rel.target_id and rel.target_id not in character_ids:
                add("major", "关系引用悬空", f"角色「{item.name}」关系指向不存在的 ID：{rel.target_id}。", [item.id, rel.target_id])
            elif rel.target and _norm_key(rel.target) not in character_names:
                add("minor", "关系对象缺失", f"角色「{item.name}」引用了尚无角色条目的「{rel.target}」。", [item.id, rel.target])
    for thread in bible.active_plot_threads:
        missing = [name for name in thread.involved_characters if _norm_key(name) not in character_names]
        if missing:
            add("minor", "剧情线角色缺失", f"剧情线「{thread.name}」引用不存在的角色：{'、'.join(missing)}。", [thread.id, *missing])

    all_entity_ids = set(id_counts)
    for fact in bible.facts:
        if fact.subject_id not in all_entity_ids:
            add("major", "事实主体缺失", f"事实 {fact.id or fact.predicate} 指向不存在的主体 {fact.subject_id}。", [fact.subject_id])
        if fact.valid_to and fact.valid_from and fact.valid_to < fact.valid_from:
            add("error", "事实时间范围无效", f"事实 {fact.id} 的 valid_to 早于 valid_from。", [fact.subject_id])
    active_fact_keys: dict[tuple[str, str], list[WorldFact]] = {}
    for fact in bible.facts:
        if not fact.valid_to:
            active_fact_keys.setdefault((fact.subject_id, fact.predicate), []).append(fact)
    for (subject_id, predicate), facts in active_fact_keys.items():
        canon_values = {json.dumps(item.value, ensure_ascii=False, sort_keys=True) for item in facts if item.locked or item.knowledge_type in ("canon", "constraint")}
        if len(canon_values) > 1:
            add("major", "Canon 事实冲突", f"主体 {subject_id} 的 {predicate} 同时存在多个有效值。", [subject_id])

    rule_ids = {item.id for item in bible.world_rules}
    rule_content: dict[str, str] = {}
    for rule in bible.world_rules:
        if rule.valid_to and rule.valid_from and rule.valid_to < rule.valid_from:
            add("error", "规则时间范围无效", f"规则「{rule.name}」失效章早于生效章。", [rule.id])
        missing_conflicts = [item for item in rule.conflicts_with if item not in rule_ids]
        if missing_conflicts:
            add("minor", "规则冲突引用悬空", f"规则「{rule.name}」引用不存在的冲突规则。", [rule.id, *missing_conflicts])
        content_key = _norm_key(rule.content)
        if content_key and content_key in rule_content and rule_content[content_key] != rule.id:
            add("minor", "结构化规则重复", f"规则「{rule.name}」与另一规则内容重复。", [rule.id, rule_content[content_key]])
        elif content_key:
            rule_content[content_key] = rule.id

    pending_candidates = [item for item in bible.duplicate_candidates if item.get("status", "pending") == "pending"]
    if pending_candidates:
        add("info", "待确认重复实体", f"有 {len(pending_candidates)} 组疑似重复实体等待确认。", [item.get("id", "") for item in pending_candidates])
    if bible.chapter_snapshots != bible.chapter_world_entries:
        add("major", "快照镜像不一致", "chapter_snapshots 与兼容字段 chapter_world_entries 不一致。")
    for warning in warnings:
        warning.setdefault("entity_ids", list(warning.get("related", [])))
        warning.setdefault("source_refs", [])
        warning.setdefault("suggestion", "检查相关来源并人工确认。")
    order = {"error": 0, "major": 1, "minor": 2, "info": 3}
    warnings.sort(key=lambda item: order.get(item.get("severity", "info"), 3))
    return warnings[:40]


EXTRACT_PROMPT = """你是一个小说信息深度提取专家。请严格根据以下章节内容，深度提取其中的角色、地点、世界观规则、事件和剧情线索。

约束：
- 严格基于原文，不要添加社会学分析、心理描写分析或道德评判
- 日期、年龄和人生阶段只能提取正文明确给出的信息或可由明确日期直接计算的信息；没有依据必须留空，不得猜测
- 对于标注了【原文引用】的字段，直接从原文复制原文，不要改写或概括
- 对于未标注【原文引用】的字段，可以适当概括但保留所有关键信息
- 宁多勿少，不确定该不该提取的信息请提取出来
- 遇到包含多条信息、规则链、限制条件或因果约束的长段落，不要压缩成一句话；拆成多个 key_worldbuilding 条目，并保留 full_passage
- 同一设定同时提取 core_summary、完整原文和 constraints；摘要用于检索，完整原文用于命中后的精确跟随

请严格按照以下 JSON 格式输出，不包含任何其他文字：

{
  "characters": [
    {
      "name": "角色名",
      "aliases": ["别名", "别称"],
      "traits": "【500字内】性格描写、外貌特征、能力特长——尽可能详细地从原文提取",
      "relationships": [
        {"target": "关系对象", "type": "friend/enemy/family/master/student/ally/rival/lover", "description": "关系描述（30字内）"}
      ],
      "status": "alive/dead/missing/transformed",
      "importance": "major/normal/minor",
      "key_details": ["【原文引用】从原文中直接复制关于该角色的重要描述片段（每段100字内）"],
      "key_dialogues": ["【原文引用】从原文中直接复制该角色说出的重要台词（每句100字内）"],
      "motivation": "该角色的核心动机/目标（100字内）",
      "arc": "该角色的成长弧线/变化趋势（100字内）",
      "birth_date": "出生日期、年份或纪年（正文无依据则空字符串）",
      "current_age": "本章结尾时的当前年龄，可写约数（正文无依据则空字符串）",
      "age_basis": "年龄判断依据，如出生日期+当前日期或原文明示（无依据则空字符串）",
      "life_stage": "本章结尾时的人生/身份阶段，如童年、大学一年级、孕中期（无依据则空字符串）",
      "current_location": "该角色章节结尾时所在位置（50字内，不确定则空字符串）",
      "current_goal": "该角色当前最明确的目标/意图（100字内，不确定则空字符串）",
      "current_emotion": "该角色当前情绪、关系状态或心理状态（100字内，不确定则空字符串）",
      "recent_action": "该角色最近一次关键行动或章节结尾动作（100字内，不确定则空字符串）",
      "knowledge_state": "该角色当前已知的重要信息、误解或隐瞒内容（100字内，不确定则空字符串）",
      "unresolved_conflicts": ["该角色身上仍未解决的冲突/问题（每条50字内）"]
    }
  ],
  "story_clock": {
    "current_date": "本章结尾的故事内日期/纪年；不明确则空字符串",
    "time_of_day": "本章结尾时段，如清晨、深夜；不明确则空字符串",
    "elapsed_time": "从故事基准点累计流逝的时间或本章明确推进的时间；不明确则空字符串",
    "story_phase": "当前阶段，如入学第一周、战争第三年、任务执行期；不明确则空字符串",
    "calendar_system": "公历、架空纪年或其他日历说明；不明确则空字符串"
  },
  "locations": [
    {
      "name": "地点名",
      "description": "【300字内】地点的外观、氛围、布局等详细描述",
      "significance": "【200字内】该地点在故事中的重要性/象征意义",
      "key_details": ["【原文引用】从原文中直接复制关于该地点的重要描写片段"],
      "atmosphere": "【200字内】该地点的氛围/给人的感觉"
    }
  ],
  "rules": [
    {
      "name": "规则名称",
      "content": "完整规则内容；保留条件、限制、代价、例外和后果，不得只写结论",
      "category": "能力/社会/地理/历史/科技/魔法/组织/其他",
      "priority": 50,
      "exceptions": ["明确例外或边界条件"]
    }
  ],
  "timeline": [
    {
      "event": "【200字内】核心事件的详细描述",
      "significance": "【200字内】该事件的影响/意义",
      "occurrence_count": 1,
      "key_passages": ["【原文引用】从原文中直接复制该事件中最重要的一段描写"],
      "foreshadowing_hints": ["该事件中埋下的伏笔或暗示（50字内）"]
    }
  ],
  "plot_threads": [
    {
      "name": "剧情线索名",
      "status": "active/resolved/dormant",
      "importance": "major/normal/minor",
      "involved_characters": ["角色名"],
      "description": "【300字内】该线索的详细描述",
      "key_details": ["【原文引用】关于该剧情线的重要原文片段"],
      "foreshadowing_related": ["该剧情线涉及的前期伏笔（50字内）"],
      "expected_payoff": "这条线索后续最可能/最应该回收的方向（100字内，不确定则空字符串）",
      "payoff_hint": "适合在后续章节使用的推进或回收提示（100字内，不确定则空字符串）"
    }
  ],
  "key_worldbuilding": [
    {
      "topic": "可检索的设定主题",
      "core_summary": "核心内容摘要：说明这项设定是什么、影响什么（200字内）",
      "full_passage": "【原文引用】完整复制承载该设定的关键原文块；可以保留多个自然段，单项最多2000字",
      "constraints": ["必须遵守的规则、条件、限制、代价、例外、禁止事项和因果后果"],
      "keywords": ["实体名", "术语", "地点", "能力", "组织"],
      "passage": "兼容字段：与 full_passage 相同或填写最关键原文"
    }
  ],
  "global_key_dialogues": [
    {"speaker": "说话者", "dialogue": "【原文引用】重要对话原文", "context": "对话背景（30字内）"}
  ],
  "global_foreshadowing": [
    {
      "hint": "伏笔内容（50字内）",
      "relates_to": "可能相关的剧情线或角色（20字内）",
      "status": "open/noticed/advanced/resolved/dormant",
      "next_step": "下次最适合如何推进或回收（80字内，不确定则空字符串）",
      "reveal_rule": "回收限制，例如禁止立刻揭底/需要先铺垫的条件（80字内，不确定则空字符串）"
    }
  ]
}

如果没有某项内容，用空数组 []。确保 JSON 合法。

章节内容：
"""


def _repair_json(text: str) -> str:
    """修复 LLM 返回的常见 JSON 格式错误（中文标点、括号用错等）"""
    # 中文引号/标点 → ASCII
    text = text.replace('“', '"').replace('”', '"')
    text = text.replace('，', ',').replace('：', ':')
    text = text.replace('；', ';').replace('（', '(').replace('）', ')')

    # 修复用圆括号代替花括号包裹对象: ("key" → {"key"
    text = re.sub(r'\(\s*("(?:\\.|[^"\\])*"\s*:)', r'{\1', text)
    # 修复对象闭合: 在 ,]} 前的 ) → }
    text = re.sub(r'\)\s*(?=[,\}\]])', r'}', text)

    return text


def _repair_truncated_json(text: str) -> str:
    """尝试修复被截断的 JSON：补齐末尾未闭合的数组/对象/字符串"""
    # 找到最后一个完整闭合的 } 或 ]
    stack = []
    last_good_end = -1
    for i, ch in enumerate(text):
        if ch in '{[':
            stack.append(ch)
        elif ch == '}':
            if stack and stack[-1] == '{':
                stack.pop()
                if not stack:
                    last_good_end = i
            else:
                return text  # 括号不匹配，无法修复
        elif ch == ']':
            if stack and stack[-1] == '[':
                stack.pop()
                if not stack:
                    last_good_end = i
            else:
                return text
    if last_good_end > 0:
        return text[:last_good_end + 1]
    return text


def _chapter_world_entry_key(chapter_num: int, chapter_version: int = 0) -> str:
    return f"ch{int(chapter_num):04d}_v{int(chapter_version or 0):03d}"


def validate_extracted_world_bible_data(data: dict) -> list[str]:
    """Validate the extraction wire shape while retaining usable categories."""
    errors: list[str] = []
    if not isinstance(data, dict):
        return ["root must be an object"]
    list_fields = ("characters", "locations", "rules", "timeline", "plot_threads", "key_worldbuilding", "global_key_dialogues", "global_foreshadowing")
    for field_name in list_fields:
        if field_name in data and not isinstance(data.get(field_name), list):
            errors.append(f"{field_name} must be a list")
    if "story_clock" in data and not isinstance(data.get("story_clock"), dict):
        errors.append("story_clock must be an object")
    for index, item in enumerate(data.get("characters", []) if isinstance(data.get("characters", []), list) else []):
        if not isinstance(item, dict):
            errors.append(f"characters[{index}] must be an object")
        elif not str(item.get("name", "")).strip():
            errors.append(f"characters[{index}].name is required")
        elif item.get("status", "alive") not in ("alive", "dead", "missing", "transformed"):
            errors.append(f"characters[{index}].status is invalid")
    for index, item in enumerate(data.get("plot_threads", []) if isinstance(data.get("plot_threads", []), list) else []):
        if not isinstance(item, dict):
            errors.append(f"plot_threads[{index}] must be an object")
        elif item.get("status", "active") not in ("active", "resolved", "dormant"):
            errors.append(f"plot_threads[{index}].status is invalid")
    return errors


def _sanitize_extracted_world_bible_data(data: dict) -> dict:
    clean = copy.deepcopy(data) if isinstance(data, dict) else {}
    for field_name in ("characters", "locations", "rules", "timeline", "plot_threads", "key_worldbuilding", "global_key_dialogues", "global_foreshadowing"):
        value = clean.get(field_name)
        clean[field_name] = value if isinstance(value, list) else []
    if not isinstance(clean.get("story_clock", {}), dict):
        clean["story_clock"] = {}

    object_collections = (
        "characters", "locations", "timeline", "plot_threads",
        "key_worldbuilding", "global_key_dialogues", "global_foreshadowing",
    )
    for field_name in object_collections:
        clean[field_name] = [
            item for item in clean[field_name] if isinstance(item, dict)
        ]

    list_text_fields = {
        "characters": ("aliases", "key_details", "key_dialogues", "unresolved_conflicts"),
        "locations": ("key_details",),
        "timeline": ("key_passages", "foreshadowing_hints"),
        "plot_threads": ("involved_characters", "key_details", "foreshadowing_related"),
        "key_worldbuilding": ("constraints", "keywords"),
    }
    scalar_text_fields = {
        "characters": (
            "name", "traits", "status", "importance", "motivation", "arc",
            "birth_date", "current_age", "age_basis", "life_stage",
            "current_location", "current_goal", "current_emotion",
            "recent_action", "knowledge_state",
        ),
        "locations": ("name", "description", "significance", "atmosphere"),
        "timeline": ("event", "significance"),
        "plot_threads": (
            "name", "status", "importance", "description",
            "expected_payoff", "payoff_hint",
        ),
        "key_worldbuilding": (
            "topic", "passage", "description", "core_summary", "full_passage",
        ),
        "global_key_dialogues": ("speaker", "dialogue", "context"),
        "global_foreshadowing": (
            "hint", "relates_to", "status", "next_step", "reveal_rule",
        ),
    }
    for collection, fields in list_text_fields.items():
        for item in clean[collection]:
            for field_name in fields:
                item[field_name] = _coerce_extracted_text_list(item.get(field_name, []))
    for collection, fields in scalar_text_fields.items():
        for item in clean[collection]:
            for field_name in fields:
                if field_name in item:
                    item[field_name] = _coerce_extracted_text(item.get(field_name))
    return clean


def _coerce_extracted_text(value) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        for key in (
            "text", "content", "quote", "dialogue", "passage",
            "description", "hint", "name", "value",
        ):
            text = _coerce_extracted_text(value.get(key))
            if text:
                return text
        return ""
    if isinstance(value, (list, tuple)):
        return "；".join(filter(None, (_coerce_extracted_text(item) for item in value)))
    if value is None:
        return ""
    return str(value).strip()


def _coerce_extracted_text_list(value) -> list[str]:
    values = value if isinstance(value, list) else [value]
    result: list[str] = []
    for item in values:
        text = _coerce_extracted_text(item)
        if text and text not in result:
            result.append(text)
    return result

def merge_extracted_world_bible_data(
    existing_bible: WorldBible | None,
    data: dict,
    *,
    chapter_content: str = "",
    chapter_num: int = 0,
    chapter_version: int = 0,
    client=None,
    model: str = "deepseek-v4-flash",
    global_user_prompt: str = "",
    store_chapter_entry: bool = False,
    run_dedup: bool = True,
) -> WorldBible:
    """将已提取出的章节世界书 JSON 合并进 WorldBible。"""
    bible = existing_bible or WorldBible()
    _ensure_entity_metadata(bible)
    validation_errors = validate_extracted_world_bible_data(data)
    data = _sanitize_extracted_world_bible_data(data)
    bible.diagnostics["last_validation"] = {
        "valid": not validation_errors,
        "errors": validation_errors,
        "chapter": int(chapter_num or 0),
        "version": int(chapter_version or 0),
    }

    if store_chapter_entry and chapter_num:
        key = _chapter_world_entry_key(chapter_num, chapter_version)
        bible.chapter_world_entries[key] = {
            "chapter": int(chapter_num),
            "version": int(chapter_version or 0),
            "data": data,
        }
        bible.chapter_snapshots[key] = copy.deepcopy(bible.chapter_world_entries[key])

    incoming_clock = data.get("story_clock", {})
    if isinstance(incoming_clock, dict):
        clock = dict(bible.story_clock or {})
        clock_fields = ("current_date", "time_of_day", "elapsed_time", "story_phase", "calendar_system")
        before_clock = {key: clock.get(key, "") for key in clock_fields}
        for field_name in clock_fields:
            value = str(incoming_clock.get(field_name, "")).strip()
            if value:
                clock[field_name] = value[:120]
        clock_values = {key: clock.get(key, "") for key in clock_fields}
        if any(clock_values.values()):
            if chapter_num:
                clock["source_chapter"] = int(chapter_num)
                clock["source_version"] = int(chapter_version or 0)
            bible.story_clock = clock
            history_entry = {
                **clock_values,
                "source_chapter": int(chapter_num or 0),
                "source_version": int(chapter_version or 0),
                "changed_fields": [
                    key for key, value in clock_values.items()
                    if value and value != before_clock.get(key, "")
                ],
            }
            history_key = (
                history_entry["source_chapter"], history_entry["source_version"],
                tuple((key, history_entry.get(key, "")) for key in clock_fields),
            )
            existing_keys = {
                (
                    int(item.get("source_chapter", 0) or 0),
                    int(item.get("source_version", 0) or 0),
                    tuple((key, item.get(key, "")) for key in clock_fields),
                )
                for item in bible.story_clock_history if isinstance(item, dict)
            }
            if history_entry["changed_fields"] and history_key not in existing_keys:
                bible.story_clock_history.append(history_entry)
    # === 合并角色 ===
    for ch_data in data.get("characters", []):
        name = ch_data.get("name", "").strip()
        if not name:
            continue
        existing = _find_character_by_name_or_alias(
            bible.characters, name, ch_data.get("aliases", [])
        )
        if existing:
            _merge_character_entry(existing, ch_data, chapter_content, chapter_num, chapter_version)
        else:
            entry = CharacterEntry(
                id=_stable_id("char", name, chapter_num),
                name=name,
                aliases=ch_data.get("aliases", []),
                traits=ch_data.get("traits", "")[:500],
                status=ch_data.get("status", "alive"),
                importance=ch_data.get("importance", "normal"),
                first_appearance=chapter_num,
                key_details=[_verify_verbatim(kd, chapter_content) for kd in ch_data.get("key_details", [])],
                key_dialogues=[_verify_verbatim(kd, chapter_content) for kd in ch_data.get("key_dialogues", [])],
                motivation=ch_data.get("motivation", "")[:200],
                arc=ch_data.get("arc", "")[:200],
                birth_date=ch_data.get("birth_date", "")[:60],
                current_age=ch_data.get("current_age", "")[:60],
                age_basis=ch_data.get("age_basis", "")[:120],
                life_stage=ch_data.get("life_stage", "")[:100],
                current_location=ch_data.get("current_location", "")[:100],
                current_goal=ch_data.get("current_goal", "")[:200],
                current_emotion=ch_data.get("current_emotion", "")[:200],
                recent_action=ch_data.get("recent_action", "")[:200],
                knowledge_state=ch_data.get("knowledge_state", "")[:200],
                unresolved_conflicts=[str(item)[:50] for item in _as_list(ch_data.get("unresolved_conflicts", [])) if item],
                source_chapter=chapter_num,
                source_version=chapter_version,
                last_updated_chapter=chapter_num,
                last_updated_version=chapter_version,
            )
            _merge_relationships(entry.relationships, ch_data.get("relationships", []))
            _merge_fact_sources(entry, ch_data.get("fact_sources"))
            for field_name in (
                "name", "aliases", "traits", "status", "importance", "key_details", "key_dialogues",
                "motivation", "arc", "birth_date", "current_age", "age_basis", "life_stage",
                "current_location", "current_goal", "current_emotion",
                "recent_action", "knowledge_state", "unresolved_conflicts", "relationships",
            ):
                _record_fact_source(entry, field_name, getattr(entry, field_name, None), chapter_num, chapter_version)
            bible.characters.append(entry)
        subject = existing if existing else entry
        for field_name in (
            "status", "birth_date", "current_age", "age_basis", "life_stage",
            "current_location", "current_goal", "current_emotion", "recent_action", "knowledge_state",
        ):
            value = ch_data.get(field_name)
            if value not in (None, "", []):
                _record_dynamic_fact(bible, subject.id, field_name, value, chapter_num, chapter_version)

    # === 合并地点 ===
    existing_locs = {l.name for l in bible.locations}
    for loc_data in data.get("locations", []):
        name = loc_data.get("name", "").strip()
        if not name:
            continue
        if name in existing_locs:
            for existing in bible.locations:
                if existing.name == name:
                    _merge_fact_sources(existing, loc_data.get("fact_sources"))
                    if loc_data.get("description"):
                        existing.description = loc_data["description"][:300]
                        _record_fact_source(existing, "description", existing.description, chapter_num, chapter_version)
                    if loc_data.get("significance"):
                        existing.significance = loc_data["significance"][:200]
                        _record_fact_source(existing, "significance", existing.significance, chapter_num, chapter_version)
                    key_details = [_verify_verbatim(kd, chapter_content) for kd in loc_data.get("key_details", [])]
                    _merge_list_dedup(existing.key_details, key_details)
                    _record_fact_source(existing, "key_details", key_details, chapter_num, chapter_version)
                    if loc_data.get("atmosphere"):
                        existing.atmosphere = loc_data["atmosphere"][:200]
                        _record_fact_source(existing, "atmosphere", existing.atmosphere, chapter_num, chapter_version)
                    _touch_source(existing, chapter_num, chapter_version)
                    break
        else:
            entry = LocationEntry(
                id=_stable_id("loc", name, chapter_num),
                name=name,
                description=loc_data.get("description", "")[:300],
                significance=loc_data.get("significance", "")[:200],
                first_appearance=chapter_num,
                key_details=[_verify_verbatim(kd, chapter_content) for kd in loc_data.get("key_details", [])],
                atmosphere=loc_data.get("atmosphere", "")[:200],
                source_chapter=chapter_num,
                source_version=chapter_version,
                last_updated_chapter=chapter_num,
                last_updated_version=chapter_version,
            )
            _merge_fact_sources(entry, loc_data.get("fact_sources"))
            for field_name in ("name", "description", "significance", "first_appearance", "key_details", "atmosphere"):
                _record_fact_source(entry, field_name, getattr(entry, field_name, None), chapter_num, chapter_version)
            bible.locations.append(entry)
            existing_locs.add(name)

    # === 合并规则 ===
    for rule in data.get("rules", []):
        if isinstance(rule, dict):
            content = str(rule.get("content") or rule.get("text") or "").strip()
            rule_data = rule
        else:
            content = str(rule).strip()
            rule_data = {}
        if not content:
            continue
        existing_rule = next((item for item in bible.world_rules if _norm_key(item.content) == _norm_key(content)), None)
        if existing_rule is None:
            bible.world_rules.append(WorldRule(
                id=str(rule_data.get("id") or _stable_id("rule", content)),
                name=str(rule_data.get("name") or content[:40]),
                content=content,
                category=str(rule_data.get("category") or "general"),
                priority=int(rule_data.get("priority", 50) or 50),
                source_refs=[_source_ref(chapter_num, chapter_version)] if chapter_num else [],
                knowledge_type="constraint",
                confidence=float(rule_data.get("confidence", 1.0) or 1.0),
                locked=bool(rule_data.get("locked", False)),
                valid_from=int(rule_data.get("valid_from", chapter_num) or 0),
                valid_to=int(rule_data.get("valid_to", 0) or 0),
                exceptions=[str(item) for item in rule_data.get("exceptions", [])],
            ))
        if content not in bible.rules:
            bible.rules.append(content)

    # === 合并时间线 ===
    for t_data in data.get("timeline", []):
        event = t_data.get("event", "").strip()
        if event:
            try:
                incoming_count = max(1, int(t_data.get("occurrence_count", 1) or 1))
            except (TypeError, ValueError):
                incoming_count = 1
            existing = next(
                (entry for entry in bible.timeline if _norm_key(entry.event) == _norm_key(event)),
                None,
            )
            key_passages = [_verify_verbatim(kp, chapter_content) for kp in t_data.get("key_passages", [])]
            hints = [fh[:50] for fh in t_data.get("foreshadowing_hints", [])]
            if existing:
                existing.occurrence_count = max(1, existing.occurrence_count) + incoming_count
                existing.significance = _append_text_unique(
                    existing.significance,
                    t_data.get("significance", "")[:200],
                    500,
                )
                _merge_list_dedup(existing.key_passages, key_passages)
                _merge_list_dedup(existing.foreshadowing_hints, hints)
                existing.source_version = chapter_version or existing.source_version
            else:
                entry = TimelineEntry(
                    id=_stable_id("event", event, chapter_num),
                    chapter=chapter_num,
                    event=event[:200],
                    significance=t_data.get("significance", "")[:200],
                    occurrence_count=incoming_count,
                    key_passages=key_passages,
                    foreshadowing_hints=hints,
                    source_version=chapter_version,
                )
                bible.timeline.append(entry)

    # === 合并剧情线 ===
    for pt_data in data.get("plot_threads", []):
        name = pt_data.get("name", "").strip()
        if not name:
            continue
        existing = next(
            (p for p in bible.active_plot_threads if _norm_key(p.name) == _norm_key(name)),
            None,
        )
        if existing:
            _merge_plot_thread(existing, pt_data, chapter_content, chapter_num, chapter_version)
        else:
            entry = PlotThread(
                id=_stable_id("thread", name, chapter_num),
                name=name,
                status=pt_data.get("status", "active"),
                importance=pt_data.get("importance", "normal"),
                involved_characters=pt_data.get("involved_characters", []),
                description=pt_data.get("description", "")[:300],
                key_details=[_verify_verbatim(kd, chapter_content) for kd in pt_data.get("key_details", [])],
                foreshadowing_related=[fr[:50] for fr in pt_data.get("foreshadowing_related", [])],
                opened_chapter=chapter_num,
                last_touched_chapter=chapter_num,
                expected_payoff=pt_data.get("expected_payoff", "")[:100],
                payoff_hint=pt_data.get("payoff_hint", "")[:100],
                source_chapter=chapter_num,
                source_version=chapter_version,
                last_updated_version=chapter_version,
            )
            _merge_fact_sources(entry, pt_data.get("fact_sources"))
            for field_name in (
                "name", "status", "importance", "involved_characters", "description", "key_details",
                "foreshadowing_related", "opened_chapter", "last_touched_chapter",
                "expected_payoff", "payoff_hint",
            ):
                _record_fact_source(entry, field_name, getattr(entry, field_name, None), chapter_num, chapter_version)
            bible.active_plot_threads.append(entry)
        subject = existing if existing else entry
        _record_dynamic_fact(
            bible, subject.id, "status", pt_data.get("status", "active"),
            chapter_num, chapter_version,
        )

    # === 合并顶层字段：世界观设定、全局伏笔、关键对话 ===
    for item in data.get("key_worldbuilding", []):
        if not isinstance(item, dict):
            continue
        topic = str(item.get("topic", "")).strip()
        core_summary = str(item.get("core_summary") or item.get("description") or "").strip()[:500]
        raw_passage = str(item.get("full_passage") or item.get("passage") or "").strip()
        full_passage = _verify_verbatim(raw_passage, chapter_content)[:12000]
        constraints = [str(value).strip()[:500] for value in _as_list(item.get("constraints", [])) if str(value).strip()]
        keywords = [str(value).strip()[:80] for value in _as_list(item.get("keywords", [])) if str(value).strip()]
        if not topic or not (full_passage or core_summary or constraints):
            continue
        existing = next((ex for ex in bible.key_worldbuilding_passages if _norm_key(ex.get("topic", "")) == _norm_key(topic)), None)
        if existing is None:
            bible.key_worldbuilding_passages.append({
                "id": _stable_id("setting", topic, chapter_num),
                "chapter": chapter_num,
                "version": chapter_version,
                "topic": topic,
                "core_summary": core_summary,
                "full_passage": full_passage,
                "passage": full_passage or core_summary,
                "constraints": list(dict.fromkeys(constraints)),
                "keywords": list(dict.fromkeys(keywords)),
                "knowledge_type": "constraint" if constraints else "canon",
                "confidence": 1.0,
                "source_refs": [_source_ref(chapter_num, chapter_version, full_passage or core_summary)],
                "locked": False,
                "hidden": False,
            })
        else:
            if core_summary:
                existing["core_summary"] = _append_text_unique(str(existing.get("core_summary", "")), core_summary, 1200)
            if full_passage:
                existing["full_passage"] = _append_text_unique(str(existing.get("full_passage") or existing.get("passage", "")), full_passage, 24000)
                existing["passage"] = existing["full_passage"]
            existing["constraints"] = list(dict.fromkeys([*existing.get("constraints", []), *constraints]))
            existing["keywords"] = list(dict.fromkeys([*existing.get("keywords", []), *keywords]))
            existing.setdefault("source_refs", []).append(_source_ref(chapter_num, chapter_version, full_passage or core_summary))
            existing["chapter"] = chapter_num or existing.get("chapter", 0)
            existing["version"] = chapter_version or existing.get("version", 0)
            if constraints:
                existing["knowledge_type"] = "constraint"
    for item in data.get("global_key_dialogues", []):
        dialogue = _verify_verbatim(item.get("dialogue", "").strip(), chapter_content)
        if dialogue:
            if not any(d.get("dialogue") == dialogue for d in bible.global_key_dialogues):
                bible.global_key_dialogues.append({
                    "speaker": item.get("speaker", "").strip(),
                    "dialogue": dialogue,
                    "context": item.get("context", "")[:30],
                    "chapter": chapter_num,
                    "version": chapter_version,
                })

    for item in data.get("global_foreshadowing", []):
        _merge_foreshadowing(bible.global_foreshadowing, item, chapter_num, chapter_version)

    if run_dedup and client is not None:
        try:
            bible = dedup_world_bible_characters(bible, client, model, global_user_prompt)
        except Exception:
            pass
        try:
            bible = dedup_world_bible_locations(bible, client, model, global_user_prompt)
        except Exception:
            pass

    if chapter_num:
        bible.last_updated_chapter = chapter_num
    _ensure_entity_metadata(bible)
    materialize_current_facts(bible, chapter_num)
    apply_manual_overrides(bible)
    bible.consistency_warnings = audit_world_bible_consistency(bible)
    bible.resolved_view = _flat_view_dict(bible)
    bible.diagnostics.update({
        "schema_version": WORLD_BIBLE_SCHEMA_VERSION,
        "last_merged_chapter": int(chapter_num or 0),
        "snapshot_count": len(bible.chapter_snapshots),
        "fact_count": len(bible.facts),
        "override_count": len(bible.manual_overrides),
        "warning_count": len(bible.consistency_warnings),
    })
    return bible


def _split_chapter_for_world_extraction(text: str, max_chars: int = 16000) -> list[str]:
    """Split all chapter content on paragraph boundaries; never discard the ending."""
    text = str(text or "")
    if len(text) <= max_chars:
        return [text]
    paragraphs = re.split(r"(\n\s*\n)", text)
    chunks: list[str] = []
    current = ""
    for part in paragraphs:
        if len(part) > max_chars:
            if current.strip():
                chunks.append(current.strip())
                current = ""
            for offset in range(0, len(part), max_chars):
                chunks.append(part[offset:offset + max_chars])
            continue
        if current and len(current) + len(part) > max_chars:
            chunks.append(current.strip())
            current = part
        else:
            current += part
    if current.strip():
        chunks.append(current.strip())
    return chunks or [text]


def _parse_extraction_response(raw: str) -> dict:
    json_str = (raw or "").strip()
    if "```json" in json_str:
        json_str = json_str.split("```json", 1)[1].split("```", 1)[0].strip()
    elif "```" in json_str:
        json_str = json_str.split("```", 1)[1].split("```", 1)[0].strip()
    last_error = None
    for candidate in (json_str, _repair_json(json_str), _repair_json(_repair_truncated_json(json_str))):
        try:
            data = json.loads(candidate)
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError as exc:
            last_error = exc
    raise last_error or ValueError("Extraction response is not a JSON object")


def _world_bible_to_extracted_data(bible: WorldBible) -> dict:
    return {
        "characters": [asdict(item) for item in bible.characters],
        "story_clock": copy.deepcopy(bible.story_clock),
        "story_clock_history": copy.deepcopy(bible.story_clock_history),
        "locations": [asdict(item) for item in bible.locations],
        "rules": [item.content for item in bible.world_rules if item.content] or list(bible.rules),
        "world_rules": [asdict(item) for item in bible.world_rules],
        "timeline": [asdict(item) for item in bible.timeline],
        "plot_threads": [asdict(item) for item in bible.active_plot_threads],
        "key_worldbuilding": copy.deepcopy(bible.key_worldbuilding_passages),
        "global_key_dialogues": copy.deepcopy(bible.global_key_dialogues),
        "global_foreshadowing": copy.deepcopy(bible.global_foreshadowing),
    }


def extract_and_merge_world_bible(
    client,
    chapter_content: str,
    chapter_num: int,
    existing_bible: WorldBible | None,
    model: str,
    chapter_version: int = 0,
    global_user_prompt: str = "",
    story_context: str = "",
    background_story: str = "",
    protagonist_bio: str = "",
    writing_demand: str = "",
    xp_mode: bool = False,
) -> WorldBible:
    """Extract every chapter chunk, retain partial success, then merge one canonical snapshot."""
    bible = existing_bible or WorldBible()
    ctx_parts = []
    if background_story or protagonist_bio or story_context or writing_demand:
        ctx_parts.append("【故事背景】")
        if background_story:
            ctx_parts.append(f"世界观设定：{background_story[:500]}")
        if protagonist_bio:
            ctx_parts.append(f"主角描述：{protagonist_bio[:500]}")
        if story_context:
            ctx_parts.append(f"前情提要：{story_context[:1000]}")
        if writing_demand:
            ctx_parts.append(f"写作要求：{writing_demand[:300]}")
    prompt_prefix = ("\n".join(ctx_parts) + "\n\n") if ctx_parts else ""

    chunks = _split_chapter_for_world_extraction(chapter_content)
    snapshot_bible = WorldBible()
    errors: list[dict] = []
    covered_chars = 0
    for index, chunk in enumerate(chunks, 1):
        user_content = (
            prompt_prefix
            + f"【章节分块】{index}/{len(chunks)}。这是同一章节的一部分，请只提取本块明确出现的信息。\n\n"
            + EXTRACT_PROMPT
            + chunk
        )
        if xp_mode:
            from utils.prompts import Prompts
            user_content += f"\n\n{Prompts.XP_WORLD_BIBLE_GUIDE}"
        if global_user_prompt.strip():
            user_content += f"\n\n用户偏好参考: {global_user_prompt}"
        data = None
        last_error = None
        for max_tokens in (16384, 32768):
            try:
                response = client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": user_content}],
                    max_tokens=max_tokens,
                    temperature=0.1,
                )
                data = _parse_extraction_response(response.choices[0].message.content or "")
                break
            except Exception as exc:
                last_error = exc
                user_content += "\n\n请只输出完整、合法的 JSON，不要添加解释。"
        if data is None:
            errors.append({"chunk": index, "chars": len(chunk), "error": str(last_error)})
            continue
        covered_chars += len(chunk)
        snapshot_bible = merge_extracted_world_bible_data(
            snapshot_bible,
            data,
            chapter_content=chunk,
            chapter_num=chapter_num,
            chapter_version=chapter_version,
            run_dedup=False,
            store_chapter_entry=False,
        )
    if not covered_chars:
        raise RuntimeError(f"世界书提取全部分块失败: {errors}")

    combined_data = _world_bible_to_extracted_data(snapshot_bible)
    result = merge_extracted_world_bible_data(
        bible,
        combined_data,
        chapter_content=chapter_content,
        chapter_num=chapter_num,
        chapter_version=chapter_version,
        client=client,
        model=model,
        global_user_prompt=global_user_prompt,
        store_chapter_entry=True,
        run_dedup=True,
    )
    result.diagnostics["last_extraction"] = {
        "chapter": int(chapter_num or 0),
        "version": int(chapter_version or 0),
        "chunk_count": len(chunks),
        "successful_chunks": len(chunks) - len(errors),
        "failed_chunks": errors,
        "input_chars": len(chapter_content or ""),
        "covered_chars": covered_chars,
        "complete": not errors and covered_chars >= len(chapter_content or ""),
    }
    return result

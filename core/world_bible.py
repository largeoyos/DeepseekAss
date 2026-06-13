"""
世界书系统（World Bible）
负责从已生成的章节中提取核心设定、角色、地点、规则、剧情线索，
并持久化为结构化数据供后续章节生成时参考，防止设定矛盾。
"""

import difflib
import json
import os
import re
from dataclasses import dataclass, field, asdict
from typing import Optional


# ========== 数据结构 ==========


@dataclass
class Relationship:
    target: str = ""
    type: str = ""          # friend/enemy/family/master/student/ally/rival
    description: str = ""


@dataclass
class CharacterEntry:
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
    current_location: str = ""                                  # 当前所在位置
    current_goal: str = ""                                      # 当前目标/意图
    current_emotion: str = ""                                   # 当前情绪/关系状态
    recent_action: str = ""                                     # 最近一次关键行动
    knowledge_state: str = ""                                   # 当前已知信息/误解
    unresolved_conflicts: list[str] = field(default_factory=list)  # 仍未解决的个人冲突


@dataclass
class LocationEntry:
    name: str = ""
    description: str = ""
    significance: str = ""
    first_appearance: int = 0
    key_details: list[str] = field(default_factory=list)   # 原文引用的地点重要描写
    atmosphere: str = ""                                    # 氛围描述


@dataclass
class TimelineEntry:
    chapter: int = 0
    event: str = ""
    significance: str = ""
    key_passages: list[str] = field(default_factory=list)          # 原文引用的事件重要段落
    foreshadowing_hints: list[str] = field(default_factory=list)   # 该事件中埋下的伏笔


@dataclass
class PlotThread:
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


@dataclass
class WorldBible:
    characters: list[CharacterEntry] = field(default_factory=list)
    locations: list[LocationEntry] = field(default_factory=list)
    rules: list[str] = field(default_factory=list)
    timeline: list[TimelineEntry] = field(default_factory=list)
    active_plot_threads: list[PlotThread] = field(default_factory=list)
    last_updated_chapter: int = 0
    key_worldbuilding_passages: list[dict] = field(default_factory=list)  # [{chapter, passage, topic}]
    global_foreshadowing: list[dict] = field(default_factory=list)        # [{hint, relates_to}]
    global_key_dialogues: list[dict] = field(default_factory=list)        # [{speaker, dialogue, context}]


# ========== 序列化/反序列化 ==========


def _filter_fields(cls, data: dict) -> dict:
    """过滤 dict 只保留 dataclass 中定义的字段，兼容 schema 变化"""
    return {k: v for k, v in data.items() if k in cls.__dataclass_fields__}


def _from_dict(cls, data: dict):
    """递归反序列化 dataclass"""
    if cls == CharacterEntry:
        rels = [Relationship(**r) for r in data.get("relationships", [])]
        base = _filter_fields(cls, {k: v for k, v in data.items() if k != "relationships"})
        return CharacterEntry(relationships=rels, **base)
    if cls == WorldBible:
        return WorldBible(
            characters=[_from_dict(CharacterEntry, c) for c in data.get("characters", [])],
            locations=[LocationEntry(**_filter_fields(LocationEntry, l)) for l in data.get("locations", [])],
            rules=list(data.get("rules", [])),
            timeline=[TimelineEntry(**_filter_fields(TimelineEntry, t)) for t in data.get("timeline", [])],
            active_plot_threads=[PlotThread(**_filter_fields(PlotThread, p)) for p in data.get("active_plot_threads", [])],
            last_updated_chapter=data.get("last_updated_chapter", 0),
            key_worldbuilding_passages=list(data.get("key_worldbuilding_passages", [])),
            global_foreshadowing=list(data.get("global_foreshadowing", [])),
            global_key_dialogues=list(data.get("global_key_dialogues", [])),
        )
    return cls(**_filter_fields(cls, data))


def world_bible_to_dict(bible: WorldBible) -> dict:
    return asdict(bible)


def dict_to_world_bible(data: dict) -> WorldBible:
    return _from_dict(WorldBible, data)


# ========== 格式化输出 ==========


def format_world_bible_for_prompt(bible: WorldBible, max_entries: int = 10) -> str:
    """
    将世界书格式化为紧凑文本，供注入到生成 prompt 中使用
    限制条目数量避免超出上下文窗口
    """
    parts = []

    if bible.characters:
        parts.append("【已登场的角色】")
        # 按重要性排序：major 优先，normal 其次，minor 最后
        sorted_chars = sorted(
            bible.characters,
            key=lambda c: {"major": 0, "normal": 1, "minor": 2}.get(c.importance, 1),
        )
        for ch in sorted_chars[:max_entries]:
            line = f"- {ch.name}：{ch.traits[:100]}"
            if ch.motivation:
                line += f" | 动机：{ch.motivation[:60]}"
            if ch.arc:
                line += f" | 弧光：{ch.arc[:60]}"
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
        if len(bible.characters) > max_entries:
            parts.append(f"  ...以及另 {len(bible.characters) - max_entries} 个角色")

    if bible.locations:
        parts.append("\n【重要地点】")
        for loc in bible.locations[:max_entries]:
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

    if bible.active_plot_threads:
        active = [p for p in bible.active_plot_threads if p.status == "active"]
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
        non_active = [p for p in bible.active_plot_threads if p.status != "active"]
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
            if t.significance:
                line += f"（{t.significance[:40]}）"
            if t.foreshadowing_hints:
                line += " 🔮" + " | ".join(t.foreshadowing_hints[:1])
            parts.append(line)

    # 全局设定与伏笔（简略展示 3-4 条）
    extras = []
    if bible.key_worldbuilding_passages:
        for item in bible.key_worldbuilding_passages[:3]:
            extras.append(f"- 设定·{item.get('topic', '')}：{item.get('passage', '')[:100]}")
    if bible.global_foreshadowing:
        for item in bible.global_foreshadowing[:3]:
            extras.append(f"- 伏笔·{item.get('hint', '')[:60]}")
    if extras:
        parts.append("\n【关键设定与伏笔】")
        parts.extend(extras)

    return "\n".join(parts)


def format_relevant_world_bible_for_prompt(
    bible: WorldBible,
    query_text: str = "",
    *,
    max_characters: int = 8,
    max_locations: int = 5,
    max_threads: int = 6,
) -> str:
    """
    根据本章标题/剧情走向检索相关世界书条目。

    世界书变大后不应机械注入前 N 项；这里用轻量关键词评分优先保留相关角色、
    地点、活跃剧情线和待回收伏笔。没有命中时退回重要性排序。
    """
    query = _norm_key(query_text)

    def score_text(*values: str) -> int:
        score = 0
        for value in values:
            value = value or ""
            norm = _norm_key(value)
            if not norm:
                continue
            if norm in query:
                score += 8
            if query and query in norm:
                score += 5
            for token in re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z0-9_]+", value):
                token_norm = _norm_key(token)
                if len(token_norm) >= 2 and token_norm in query:
                    score += 2
        return score

    imp_score = {"major": 4, "normal": 2, "minor": 0}
    status_score = {"active": 4, "dormant": 1, "resolved": 0}

    chars = sorted(
        bible.characters,
        key=lambda c: (
            score_text(
                c.name,
                " ".join(c.aliases),
                c.traits,
                c.current_goal,
                c.current_location,
                c.recent_action,
                " ".join(c.unresolved_conflicts),
            ) + imp_score.get(c.importance, 2),
            imp_score.get(c.importance, 2),
            c.first_appearance,
        ),
        reverse=True,
    )[:max_characters]

    locs = sorted(
        bible.locations,
        key=lambda l: (
            score_text(l.name, l.description, l.significance, l.atmosphere)
            + (2 if l.key_details else 0),
            l.first_appearance,
        ),
        reverse=True,
    )[:max_locations]

    threads = sorted(
        bible.active_plot_threads,
        key=lambda p: (
            score_text(
                p.name,
                p.description,
                " ".join(p.involved_characters),
                " ".join(p.foreshadowing_related),
                p.expected_payoff,
                p.payoff_hint,
            ) + imp_score.get(p.importance, 2) + status_score.get(p.status, 0),
            p.last_touched_chapter,
        ),
        reverse=True,
    )[:max_threads]

    scoped = WorldBible(
        characters=chars,
        locations=locs,
        rules=bible.rules,
        timeline=bible.timeline,
        active_plot_threads=threads,
        last_updated_chapter=bible.last_updated_chapter,
        key_worldbuilding_passages=bible.key_worldbuilding_passages,
        global_foreshadowing=bible.global_foreshadowing,
        global_key_dialogues=bible.global_key_dialogues,
    )
    return format_world_bible_for_prompt(
        scoped,
        max_entries=max(max_characters, max_locations, max_threads),
    )


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


def _merge_character_entry(existing: CharacterEntry, ch_data: dict, chapter_content: str) -> None:
    """稳定合并角色字段，不用新提取结果覆盖旧信息。"""
    name = ch_data.get("name", "").strip()
    if name and name != existing.name and name not in existing.aliases:
        existing.aliases.append(name)
    for alias in ch_data.get("aliases", []):
        alias = str(alias).strip()
        if alias and alias != existing.name and alias not in existing.aliases:
            existing.aliases.append(alias)
    existing.traits = _append_text_unique(existing.traits, ch_data.get("traits", "")[:500], 1000)
    if ch_data.get("status") in ("dead", "missing", "transformed"):
        existing.status = ch_data["status"]
    existing.importance = _higher_importance(existing.importance, ch_data.get("importance", "normal"))
    _merge_list_dedup(existing.key_details, [_verify_verbatim(kd, chapter_content) for kd in ch_data.get("key_details", [])])
    _merge_list_dedup(existing.key_dialogues, [_verify_verbatim(kd, chapter_content) for kd in ch_data.get("key_dialogues", [])])
    existing.motivation = _append_text_unique(existing.motivation, ch_data.get("motivation", "")[:200], 400)
    existing.arc = _append_text_unique(existing.arc, ch_data.get("arc", "")[:200], 400)
    for field_name, limit in (
        ("current_location", 100),
        ("current_goal", 200),
        ("current_emotion", 200),
        ("recent_action", 200),
        ("knowledge_state", 200),
    ):
        value = str(ch_data.get(field_name, "")).strip()
        if value:
            setattr(existing, field_name, value[:limit])
    _merge_list_dedup(
        existing.unresolved_conflicts,
        [str(item)[:50] for item in _as_list(ch_data.get("unresolved_conflicts", [])) if item],
    )
    _merge_relationships(existing.relationships, ch_data.get("relationships", []))


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


def _merge_plot_thread(existing: PlotThread, pt_data: dict, chapter_content: str) -> None:
    existing.status = _plot_thread_status(existing.status, pt_data.get("status", "active"))
    existing.description = _append_text_unique(existing.description, pt_data.get("description", "")[:300], 800)
    for char in pt_data.get("involved_characters", []):
        if char and char not in existing.involved_characters:
            existing.involved_characters.append(char)
    existing.importance = _higher_importance(existing.importance, pt_data.get("importance", "normal"))
    if existing.opened_chapter == 0:
        existing.opened_chapter = int(pt_data.get("opened_chapter", 0) or 0)
    if pt_data.get("last_touched_chapter"):
        existing.last_touched_chapter = max(existing.last_touched_chapter, int(pt_data.get("last_touched_chapter", 0) or 0))
    existing.expected_payoff = _append_text_unique(existing.expected_payoff, pt_data.get("expected_payoff", "")[:100], 300)
    existing.payoff_hint = _append_text_unique(existing.payoff_hint, pt_data.get("payoff_hint", "")[:100], 300)
    _merge_list_dedup(existing.key_details, [_verify_verbatim(kd, chapter_content) for kd in pt_data.get("key_details", [])])
    _merge_list_dedup(existing.foreshadowing_related, [fr[:50] for fr in pt_data.get("foreshadowing_related", [])])


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


EXTRACT_PROMPT = """你是一个小说信息深度提取专家。请严格根据以下章节内容，深度提取其中的角色、地点、世界观规则、事件和剧情线索。

约束：
- 严格基于原文，不要添加社会学分析、心理描写分析或道德评判
- 对于标注了【原文引用】的字段，直接从原文复制原文，不要改写或概括
- 对于未标注【原文引用】的字段，可以适当概括但保留所有关键信息
- 宁多勿少，不确定该不该提取的信息请提取出来

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
      "current_location": "该角色章节结尾时所在位置（50字内，不确定则空字符串）",
      "current_goal": "该角色当前最明确的目标/意图（100字内，不确定则空字符串）",
      "current_emotion": "该角色当前情绪、关系状态或心理状态（100字内，不确定则空字符串）",
      "recent_action": "该角色最近一次关键行动或章节结尾动作（100字内，不确定则空字符串）",
      "knowledge_state": "该角色当前已知的重要信息、误解或隐瞒内容（100字内，不确定则空字符串）",
      "unresolved_conflicts": ["该角色身上仍未解决的冲突/问题（每条50字内）"]
    }
  ],
  "locations": [
    {
      "name": "地点名",
      "description": "【300字内】地点的外观、氛围、布局等详细描述",
      "significance": "【200字内】该地点在故事中的重要性/象征意义",
      "key_details": ["【原文引用】从原文中直接复制关于该地点的重要描写片段"],
      "atmosphere": "【200字内】该地点的氛围/给人的感觉"
    }
  ],
  "rules": ["世界观规则1（完整保留原文描述）", "规则2"],
  "timeline": [
    {
      "event": "【200字内】核心事件的详细描述",
      "significance": "【200字内】该事件的影响/意义",
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
    {"topic": "设定主题", "passage": "【原文引用】从原文中直接复制重要的世界观设定段落（300字内）"}
  ],
  "global_key_dialogues": [
    {"speaker": "说话者", "dialogue": "【原文引用】重要对话原文", "context": "对话背景（30字内）"}
  ],
  "global_foreshadowing": [
    {"hint": "伏笔内容（50字内）", "relates_to": "可能相关的剧情线或角色（20字内）"}
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


def extract_and_merge_world_bible(
    client,
    chapter_content: str,
    chapter_num: int,
    existing_bible: WorldBible | None,
    model: str,
    global_user_prompt: str = "",
    story_context: str = "",
    background_story: str = "",
    protagonist_bio: str = "",
    writing_demand: str = "",
    xp_mode: bool = False,
) -> WorldBible:
    """
    分析章节内容，提取世界观信息并与现有世界书合并

    Args:
        client: OpenAI 客户端
        chapter_content: 章节正文
        chapter_num: 当前章节编号
        existing_bible: 现有的世界书，None 表示新建
        model: 模型名称
        global_user_prompt: 用户全局提示词（偏好参考）
        story_context: 前文摘要（用于批量导入时逐章积累）
        background_story: 世界观设定背景
        protagonist_bio: 主角描述
        writing_demand: 写作要求

    Returns:
        合并后的 WorldBible
    """
    bible = existing_bible or WorldBible()

    # 截取前 40000 字符分析
    content_sample = chapter_content[:40000]

    # 如果有故事背景上下文，构建 prompt 前缀
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

    user_content = prompt_prefix + EXTRACT_PROMPT + content_sample
    if xp_mode:
        from utils.prompts import Prompts
        user_content += f"\n\n{Prompts.XP_WORLD_BIBLE_GUIDE}"
    if global_user_prompt.strip():
        user_content += f"\n\n用户偏好参考: {global_user_prompt}"

    # 首次尝试，设较高的 max_tokens 避免截断
    max_extract_tokens = 16384
    last_error = None

    for attempt in range(2):
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": user_content}],
                max_tokens=max_extract_tokens,
                temperature=0.1,
            )
            raw = response.choices[0].message.content or ""
        except Exception as e:
            raise RuntimeError(f"世界书提取 API 调用失败: {e}")

        # 解析 JSON
        json_str = raw.strip()
        if "```json" in json_str:
            json_str = json_str.split("```json")[1].split("```")[0].strip()
        elif "```" in json_str:
            json_str = json_str.split("```")[1].split("```")[0].strip()

        # 尝试修复并解析
        for repair_step in [json_str, _repair_json(json_str), _repair_json(_repair_truncated_json(json_str))]:
            try:
                data = json.loads(repair_step)
                break  # 解析成功
            except json.JSONDecodeError:
                continue
        else:
            # 全部修复尝试均失败
            if attempt == 0:
                # 首次失败 → 增大 max_tokens 重试
                max_extract_tokens = 32768
                user_content += "\n\n注意：请确保输出完整、合法的 JSON，不要被截断。"
                continue
            raise RuntimeError(
                f"世界书提取返回的 JSON 解析失败。原始响应 (前500字):\n{raw[:500]}"
            )
        break  # 成功解析后跳出重试循环

    # === 合并角色 ===
    for ch_data in data.get("characters", []):
        name = ch_data.get("name", "").strip()
        if not name:
            continue
        existing = _find_character_by_name_or_alias(
            bible.characters, name, ch_data.get("aliases", [])
        )
        if existing:
            _merge_character_entry(existing, ch_data, chapter_content)
        else:
            entry = CharacterEntry(
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
                current_location=ch_data.get("current_location", "")[:100],
                current_goal=ch_data.get("current_goal", "")[:200],
                current_emotion=ch_data.get("current_emotion", "")[:200],
                recent_action=ch_data.get("recent_action", "")[:200],
                knowledge_state=ch_data.get("knowledge_state", "")[:200],
                unresolved_conflicts=[str(item)[:50] for item in _as_list(ch_data.get("unresolved_conflicts", [])) if item],
            )
            _merge_relationships(entry.relationships, ch_data.get("relationships", []))
            bible.characters.append(entry)

    # === 合并地点 ===
    existing_locs = {l.name for l in bible.locations}
    for loc_data in data.get("locations", []):
        name = loc_data.get("name", "").strip()
        if not name:
            continue
        if name in existing_locs:
            for existing in bible.locations:
                if existing.name == name:
                    if loc_data.get("description"):
                        existing.description = loc_data["description"][:300]
                    if loc_data.get("significance"):
                        existing.significance = loc_data["significance"][:200]
                    _merge_list_dedup(existing.key_details, [_verify_verbatim(kd, chapter_content) for kd in loc_data.get("key_details", [])])
                    if loc_data.get("atmosphere"):
                        existing.atmosphere = loc_data["atmosphere"][:200]
                    break
        else:
            bible.locations.append(LocationEntry(
                name=name,
                description=loc_data.get("description", "")[:300],
                significance=loc_data.get("significance", "")[:200],
                first_appearance=chapter_num,
                key_details=[_verify_verbatim(kd, chapter_content) for kd in loc_data.get("key_details", [])],
                atmosphere=loc_data.get("atmosphere", "")[:200],
            ))
            existing_locs.add(name)

    # === 合并规则 ===
    for rule in data.get("rules", []):
        r = rule.strip()
        if r and r not in bible.rules:
            bible.rules.append(r)

    # === 合并时间线 ===
    for t_data in data.get("timeline", []):
        event = t_data.get("event", "").strip()
        if event:
            entry = TimelineEntry(
                chapter=chapter_num,
                event=event[:200],
                significance=t_data.get("significance", "")[:200],
                key_passages=[_verify_verbatim(kp, chapter_content) for kp in t_data.get("key_passages", [])],
                foreshadowing_hints=[fh[:50] for fh in t_data.get("foreshadowing_hints", [])],
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
            _merge_plot_thread(existing, pt_data, chapter_content)
        else:
            bible.active_plot_threads.append(PlotThread(
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
            ))

    # === 合并顶层字段：世界观设定、全局伏笔、关键对话 ===
    for item in data.get("key_worldbuilding", []):
        topic = item.get("topic", "").strip()
        passage = _verify_verbatim(item.get("passage", "").strip(), chapter_content)
        if topic and passage:
            if not any(ex.get("topic") == topic for ex in bible.key_worldbuilding_passages):
                bible.key_worldbuilding_passages.append({
                    "chapter": chapter_num,
                    "topic": topic,
                    "passage": passage[:300],
                })

    for item in data.get("global_key_dialogues", []):
        dialogue = _verify_verbatim(item.get("dialogue", "").strip(), chapter_content)
        if dialogue:
            if not any(d.get("dialogue") == dialogue for d in bible.global_key_dialogues):
                bible.global_key_dialogues.append({
                    "speaker": item.get("speaker", "").strip(),
                    "dialogue": dialogue,
                    "context": item.get("context", "")[:30],
                })

    for item in data.get("global_foreshadowing", []):
        hint = item.get("hint", "").strip()
        if hint and not any(f.get("hint") == hint for f in bible.global_foreshadowing):
            bible.global_foreshadowing.append({
                "hint": hint[:50],
                "relates_to": item.get("relates_to", "")[:20],
            })

    # 合并完成后去重重复角色和地点（仅拼接，不压缩）
    try:
        bible = dedup_world_bible_characters(bible, client, model, global_user_prompt)
    except Exception:
        pass
    try:
        bible = dedup_world_bible_locations(bible, client, model, global_user_prompt)
    except Exception:
        pass

    bible.last_updated_chapter = chapter_num
    return bible

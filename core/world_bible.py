"""
世界书系统（World Bible）
负责从已生成的章节中提取核心设定、角色、地点、规则、剧情线索，
并持久化为结构化数据供后续章节生成时参考，防止设定矛盾。
"""

import difflib
import json
import os
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
                if p.foreshadowing_related:
                    line += " | 伏笔：" + " | ".join(p.foreshadowing_related[:1])
                parts.append(line)
        # 非活跃剧情线（简略列出）
        non_active = [p for p in bible.active_plot_threads if p.status != "active"]
        if non_active:
            parts.append("\n【待回收剧情线】")
            for p in non_active[:4]:
                parts.append(f"- {p.name} [{p.status}]：{p.description[:80]}")

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
      "arc": "该角色的成长弧线/变化趋势（100字内）"
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
      "foreshadowing_related": ["该剧情线涉及的前期伏笔（50字内）"]
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


def extract_and_merge_world_bible(
    client,
    chapter_content: str,
    chapter_num: int,
    existing_bible: WorldBible | None,
    model: str,
    global_user_prompt: str = "",
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

    Returns:
        合并后的 WorldBible
    """
    bible = existing_bible or WorldBible()

    # 截取前 6000 字分析
    content_sample = chapter_content[:6000]

    user_content = EXTRACT_PROMPT + content_sample
    if global_user_prompt.strip():
        user_content += f"\n\n用户偏好参考: {global_user_prompt}"

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": user_content}],
            max_tokens=4096,
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

    try:
        data = json.loads(json_str)
    except json.JSONDecodeError:
        raise RuntimeError(
            f"世界书提取返回的 JSON 解析失败。原始响应 (前500字):\n{raw[:500]}"
        )

    # === 合并角色 ===
    existing_names = {c.name for c in bible.characters}
    for ch_data in data.get("characters", []):
        name = ch_data.get("name", "").strip()
        if not name:
            continue
        if name in existing_names:
            for existing in bible.characters:
                if existing.name == name:
                    if ch_data.get("traits"):
                        existing.traits = ch_data["traits"][:500]
                    if ch_data.get("status") and ch_data["status"] in ("alive", "dead", "missing", "transformed"):
                        existing.status = ch_data["status"]
                    if ch_data.get("aliases"):
                        for alias in ch_data["aliases"]:
                            if alias and alias not in existing.aliases:
                                existing.aliases.append(alias)
                    new_imp = ch_data.get("importance", "normal")
                    existing.importance = _higher_importance(existing.importance, new_imp)
                    # 新字段
                    _merge_list_dedup(existing.key_details, [_verify_verbatim(kd, chapter_content) for kd in ch_data.get("key_details", [])])
                    _merge_list_dedup(existing.key_dialogues, [_verify_verbatim(kd, chapter_content) for kd in ch_data.get("key_dialogues", [])])
                    if ch_data.get("motivation"):
                        existing.motivation = ch_data["motivation"][:200]
                    if ch_data.get("arc"):
                        existing.arc = ch_data["arc"][:200]
                    # 关系
                    for r in ch_data.get("relationships", []):
                        if not any(r.get("target") == rel.target for rel in existing.relationships):
                            # 过滤 LLM 可能输出的额外字段，只保留 Relationship 定义的字段
                            rel_fields = {k: v for k, v in r.items() if k in Relationship.__dataclass_fields__}
                            existing.relationships.append(Relationship(**rel_fields))
                    break
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
            )
            for r_data in ch_data.get("relationships", []):
                rel_fields = {k: v for k, v in r_data.items() if k in Relationship.__dataclass_fields__}
                entry.relationships.append(Relationship(**rel_fields))
            bible.characters.append(entry)
            existing_names.add(name)

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
    existing_threads = {p.name for p in bible.active_plot_threads}
    for pt_data in data.get("plot_threads", []):
        name = pt_data.get("name", "").strip()
        if not name:
            continue
        if name in existing_threads:
            for existing in bible.active_plot_threads:
                if existing.name == name:
                    if pt_data.get("status") in ("active", "resolved", "dormant"):
                        existing.status = pt_data["status"]
                    if pt_data.get("description"):
                        existing.description = pt_data["description"][:300]
                    for char in pt_data.get("involved_characters", []):
                        if char and char not in existing.involved_characters:
                            existing.involved_characters.append(char)
                    new_imp = pt_data.get("importance", "normal")
                    existing.importance = _higher_importance(existing.importance, new_imp)
                    _merge_list_dedup(existing.key_details, [_verify_verbatim(kd, chapter_content) for kd in pt_data.get("key_details", [])])
                    _merge_list_dedup(existing.foreshadowing_related, [fr[:50] for fr in pt_data.get("foreshadowing_related", [])])
                    break
        else:
            bible.active_plot_threads.append(PlotThread(
                name=name,
                status=pt_data.get("status", "active"),
                importance=pt_data.get("importance", "normal"),
                involved_characters=pt_data.get("involved_characters", []),
                description=pt_data.get("description", "")[:300],
                key_details=[_verify_verbatim(kd, chapter_content) for kd in pt_data.get("key_details", [])],
                foreshadowing_related=[fr[:50] for fr in pt_data.get("foreshadowing_related", [])],
            ))
            existing_threads.add(name)

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

    bible.last_updated_chapter = chapter_num
    return bible

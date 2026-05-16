"""
世界书系统（World Bible）
负责从已生成的章节中提取核心设定、角色、地点、规则、剧情线索，
并持久化为结构化数据供后续章节生成时参考，防止设定矛盾。
"""

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
    first_appearance: int = 0
    notes: str = ""


@dataclass
class LocationEntry:
    name: str = ""
    description: str = ""
    significance: str = ""
    first_appearance: int = 0


@dataclass
class TimelineEntry:
    chapter: int = 0
    event: str = ""
    significance: str = ""


@dataclass
class PlotThread:
    name: str = ""
    status: str = "active"   # active/resolved/dormant
    involved_characters: list[str] = field(default_factory=list)
    description: str = ""


@dataclass
class WorldBible:
    characters: list[CharacterEntry] = field(default_factory=list)
    locations: list[LocationEntry] = field(default_factory=list)
    rules: list[str] = field(default_factory=list)
    timeline: list[TimelineEntry] = field(default_factory=list)
    active_plot_threads: list[PlotThread] = field(default_factory=list)
    last_updated_chapter: int = 0


# ========== 序列化/反序列化 ==========


def _from_dict(cls, data: dict):
    """递归反序列化 dataclass"""
    if cls == CharacterEntry:
        rels = [Relationship(**r) for r in data.get("relationships", [])]
        return CharacterEntry(relationships=rels, **{k: v for k, v in data.items() if k != "relationships"})
    if cls == WorldBible:
        return WorldBible(
            characters=[_from_dict(CharacterEntry, c) for c in data.get("characters", [])],
            locations=[LocationEntry(**l) for l in data.get("locations", [])],
            rules=list(data.get("rules", [])),
            timeline=[TimelineEntry(**t) for t in data.get("timeline", [])],
            active_plot_threads=[PlotThread(**p) for p in data.get("active_plot_threads", [])],
            last_updated_chapter=data.get("last_updated_chapter", 0),
        )
    return cls(**data)


def world_bible_to_dict(bible: WorldBible) -> dict:
    return asdict(bible)


def dict_to_world_bible(data: dict) -> WorldBible:
    return _from_dict(WorldBible, data)


# ========== 格式化输出 ==========


def format_world_bible_for_prompt(bible: WorldBible, max_entries: int = 5) -> str:
    """
    将世界书格式化为紧凑文本，供注入到生成 prompt 中使用
    限制条目数量避免超出上下文窗口
    """
    parts = []

    if bible.characters:
        parts.append("【已登场的角色】")
        for ch in bible.characters[:max_entries]:
            rel_str = "; ".join(f"{r.type}({r.target})" for r in ch.relationships[:3])
            parts.append(
                f"- {ch.name}：{ch.traits[:100]}"
                + (f" 关系：{rel_str}" if rel_str else "")
                + (f" [{ch.status}]" if ch.status != "alive" else "")
            )
        if len(bible.characters) > max_entries:
            parts.append(f"  ...以及另 {len(bible.characters) - max_entries} 个角色")

    if bible.locations:
        parts.append("\n【重要地点】")
        for loc in bible.locations[:max_entries]:
            parts.append(f"- {loc.name}：{loc.description[:80]}")

    if bible.rules:
        parts.append("\n【世界观规则】")
        for rule in bible.rules[:max_entries]:
            parts.append(f"- {rule[:120]}")

    if bible.active_plot_threads:
        active = [p for p in bible.active_plot_threads if p.status == "active"]
        if active:
            parts.append("\n【活跃剧情线】")
            for p in active[:max_entries]:
                parts.append(f"- {p.name}：{p.description[:100]}")

    if bible.timeline:
        recent = bible.timeline[-max_entries:]
        parts.append("\n【近期事件】")
        for t in recent:
            parts.append(f"- 第{t.chapter}章：{t.event[:80]}")

    return "\n".join(parts)


# ========== AI 提取与合并 ==========


EXTRACT_PROMPT = """你是一个专业的小说分析工具。请分析以下章节内容，提取结构化的世界观信息。

请严格按照以下 JSON 格式输出，不包含任何其他文字：

{
  "characters": [
    {"name": "角色名", "aliases": ["别名"], "traits": "性格/外貌/能力描述（50字内）", "status": "alive/dead/missing/transformed"}
  ],
  "locations": [
    {"name": "地点名", "description": "地点描述（30字内）", "significance": "重要性"}
  ],
  "rules": ["世界观规则1", "规则2"],
  "timeline": [
    {"event": "核心事件（30字内）", "significance": "意义"}
  ],
  "plot_threads": [
    {"name": "剧情线索名", "status": "active/resolved/dormant", "involved_characters": ["角色名"], "description": "描述（30字内）"}
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
) -> WorldBible:
    """
    分析章节内容，提取世界观信息并与现有世界书合并

    Args:
        client: OpenAI 客户端
        chapter_content: 章节正文
        chapter_num: 当前章节编号
        existing_bible: 现有的世界书，None 表示新建
        model: 模型名称

    Returns:
        合并后的 WorldBible
    """
    bible = existing_bible or WorldBible()

    # 截取前 4000 字分析（避免超出上下文）
    content_sample = chapter_content[:4000]

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": EXTRACT_PROMPT + content_sample}],
            max_tokens=2000,
            temperature=0.1,
        )
        raw = response.choices[0].message.content or ""
    except Exception:
        # 提取失败不中断流程
        return bible

    # 解析 JSON（可能被 markdown 代码块包裹）
    json_str = raw.strip()
    if "```json" in json_str:
        json_str = json_str.split("```json")[1].split("```")[0].strip()
    elif "```" in json_str:
        json_str = json_str.split("```")[1].split("```")[0].strip()

    try:
        data = json.loads(json_str)
    except json.JSONDecodeError:
        return bible

    # === 合并角色 ===
    existing_names = {c.name for c in bible.characters}
    for ch_data in data.get("characters", []):
        name = ch_data.get("name", "").strip()
        if not name:
            continue
        if name in existing_names:
            # 更新已有角色
            for existing in bible.characters:
                if existing.name == name:
                    if ch_data.get("traits"):
                        existing.traits = ch_data["traits"][:200]
                    if ch_data.get("status") and ch_data["status"] in ("alive", "dead", "missing", "transformed"):
                        existing.status = ch_data["status"]
                    if ch_data.get("aliases"):
                        for alias in ch_data["aliases"]:
                            if alias and alias not in existing.aliases:
                                existing.aliases.append(alias)
                    break
        else:
            entry = CharacterEntry(
                name=name,
                aliases=ch_data.get("aliases", []),
                traits=ch_data.get("traits", "")[:200],
                status=ch_data.get("status", "alive"),
                first_appearance=chapter_num,
            )
            bible.characters.append(entry)
            existing_names.add(name)

    # === 合并地点 ===
    existing_locs = {l.name for l in bible.locations}
    for loc_data in data.get("locations", []):
        name = loc_data.get("name", "").strip()
        if not name or name in existing_locs:
            continue
        bible.locations.append(LocationEntry(
            name=name,
            description=loc_data.get("description", "")[:100],
            significance=loc_data.get("significance", "")[:100],
            first_appearance=chapter_num,
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
            bible.timeline.append(TimelineEntry(
                chapter=chapter_num,
                event=event[:100],
                significance=t_data.get("significance", "")[:100],
            ))

    # === 合并剧情线 ===
    existing_threads = {p.name for p in bible.active_plot_threads}
    for pt_data in data.get("plot_threads", []):
        name = pt_data.get("name", "").strip()
        if not name:
            continue
        if name in existing_threads:
            # 更新状态
            for existing in bible.active_plot_threads:
                if existing.name == name:
                    if pt_data.get("status") in ("active", "resolved", "dormant"):
                        existing.status = pt_data["status"]
                    if pt_data.get("description"):
                        existing.description = pt_data["description"][:200]
                    for char in pt_data.get("involved_characters", []):
                        if char and char not in existing.involved_characters:
                            existing.involved_characters.append(char)
                    break
        else:
            bible.active_plot_threads.append(PlotThread(
                name=name,
                status=pt_data.get("status", "active"),
                involved_characters=pt_data.get("involved_characters", []),
                description=pt_data.get("description", "")[:200],
            ))
            existing_threads.add(name)

    bible.last_updated_chapter = chapter_num
    return bible

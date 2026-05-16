"""
文件分段摘要 + 世界观提取模块
功能：
- AI 语义分段（不依赖 # 标题，由大模型按话题转折划分层次）
- 逐段提取角色、地点、规则、时间线、剧情线
- 合并为完整的 WorldBible
- 自动生成小说设定（背景故事、主角描述）
"""

import json
import os
import re
import time
from typing import Callable


SEGMENT_PROMPT = """你是一位文本分析专家。请分析以下文本，根据话题转折和语义变化，
将文本划分为若干逻辑段落，并为每个段落拟定一个小标题。

要求：
1. 在每个段落**前**单独一行插入标记：【语义段落】段落标题
2. 保留所有原文内容，不要修改、省略或改写任何文字
3. 保持原文的格式和换行

输出示例：
【语义段落】世界背景设定
这里是世界背景设定的内容……（原文完整保留）

【语义段落】主要角色介绍
这里是主要角色介绍的内容……（原文完整保留）

开始处理：

{text}"""


EXTRACT_PROMPT = """你是一个专业的小说分析工具。请分析以下文本片段，提取其中包含的结构化世界观信息。

文本标题：{title}

文本内容：
{content}

请严格按照以下 JSON 格式输出，不包含任何其他文字：

{{
  "characters": [
    {{"name": "角色名", "aliases": ["别名"], "traits": "性格/外貌/能力描述（50字内）", "status": "alive"}}
  ],
  "locations": [
    {{"name": "地点名", "description": "地点描述（30字内）", "significance": "重要性"}}
  ],
  "rules": ["世界观规则1", "规则2"],
  "timeline": [
    {{"event": "核心事件（30字内）", "significance": "意义"}}
  ],
  "plot_threads": [
    {{"name": "剧情线索名", "status": "active/resolved/dormant", "involved_characters": ["角色名"], "description": "描述（30字内）"}}
  ],
  "key_settings": "这段内容反映的核心设定要点（50字内）",
  "character_focus": "这段内容涉及的关键角色特征（50字内）"
}}

如果没有某项内容，用空数组 []。确保 JSON 合法。"""


BACKGROUND_PROMPT = """你是一位资深小说编辑。以下是从一份小说设定文档中提取出的结构化世界观信息。
请根据这些信息，撰写两段用于指导小说创作的核心设定文本。

角色列表：
{characters}

重要地点：
{locations}

世界观规则：
{rules}

剧情线索：
{plot_threads}

关键事件：
{timeline}

请输出以下 JSON 格式，不包含任何其他文字：

{{
  "background_story": "【核心设定】将世界观规则、地点、时代背景整合为一段连贯的背景描述（300-500字），作为后续章节生成时的世界观参考。",
  "protagonist_bio": "【人物背景】将主要角色的设定整合为一段描述（200-300字），包括角色关系网络。",
  "writing_demand": "【写作要求】根据剧情线索和核心事件，列出3-5条具体的写作指导（每条20字内）。"
}}

确保 JSON 合法。"""


def _call_api(client, messages, model, max_tokens=165536, temperature=0.1):
    """调用 API，带重试"""
    for attempt in range(3):
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            return resp.choices[0].message.content or ""
        except Exception as e:
            if attempt < 2:
                time.sleep(2 ** attempt)
            else:
                raise e
    return ""


def _parse_json(text: str) -> dict:
    """从 API 返回文本中解析 JSON（处理 markdown 代码块包裹）"""
    text = text.strip()
    if "```json" in text:
        text = text.split("```json")[1].split("```")[0].strip()
    elif "```" in text:
        text = text.split("```")[1].split("```")[0].strip()
    return json.loads(text)


# ========== 对外接口 ==========


def segment_by_ai(client, text: str, model: str) -> list[tuple[str, str]]:
    """
    AI 语义分段：由大模型按话题转折划分逻辑段落。

    Returns:
        [(title, content), ...]  每个段落的标题和内容
    """
    prompt = SEGMENT_PROMPT.format(text=text)
    raw = _call_api(client, [{"role": "user", "content": prompt}], model)

    # 解析 【语义段落】 标记
    parts = re.split(r'^【语义段落】(.+)$', raw, flags=re.MULTILINE)
    segments = []
    for i in range(1, len(parts), 2):
        title = parts[i].strip()
        content = parts[i + 1].strip() if i + 1 < len(parts) else ""
        if title and content:
            segments.append((title, content))

    # 容错：如果 AI 没按格式输出，整段作为一个段落
    if not segments:
        segments = [("全文", text)]

    return segments


def extract_world_bible_from_segments(
    client,
    segments: list[tuple[str, str]],
    model: str,
    *,
    progress_callback: Callable[[int, int], None] | None = None,
) -> dict:
    """
    对每个语义段落提取世界观信息，合并为完整数据。

    Returns:
        {
            "characters": [...],
            "locations": [...],
            "rules": [...],
            "timeline": [...],
            "plot_threads": [...],
            "key_settings_hints": [...],   # 每段的核心设定要点
            "character_focus_hints": [...], # 每段的角色特征要点
        }
    """
    merged = {
        "characters": [],
        "locations": [],
        "rules": [],
        "timeline": [],
        "plot_threads": [],
        "key_settings_hints": [],
        "character_focus_hints": [],
    }
    seen_names = {"characters": set(), "locations": set(), "rules": set(), "plot_threads": set()}
    # 用 chapter_num=1 标记所有条目都来自初始导入（区别于后续章节生成的条目）
    chapter_marker = 0

    for idx, (title, content) in enumerate(segments):
        if progress_callback:
            progress_callback(idx + 1, len(segments))

        # 控制每段送审长度
        content_sample = content[:4000]

        prompt = EXTRACT_PROMPT.format(title=title, content=content_sample)
        try:
            raw = _call_api(client, [{"role": "user", "content": prompt}], model, max_tokens=4096, temperature=0.1)
            data = _parse_json(raw)
        except Exception:
            continue

        # 合并角色
        for ch in data.get("characters", []):
            name = ch.get("name", "").strip()
            if not name or name in seen_names["characters"]:
                continue
            seen_names["characters"].add(name)
            merged["characters"].append({
                "name": name,
                "aliases": ch.get("aliases", []),
                "traits": ch.get("traits", "")[:200],
                "status": ch.get("status", "alive"),
                "first_appearance": chapter_marker,
            })

        # 合并地点
        for loc in data.get("locations", []):
            name = loc.get("name", "").strip()
            if not name or name in seen_names["locations"]:
                continue
            seen_names["locations"].add(name)
            merged["locations"].append({
                "name": name,
                "description": loc.get("description", "")[:100],
                "significance": loc.get("significance", "")[:100],
                "first_appearance": chapter_marker,
            })

        # 合并规则
        for rule in data.get("rules", []):
            r = rule.strip()
            if r and r not in seen_names["rules"]:
                seen_names["rules"].add(r)
                merged["rules"].append(r)

        # 合并时间线
        for t in data.get("timeline", []):
            event = t.get("event", "").strip()
            if event:
                merged["timeline"].append({
                    "chapter": chapter_marker,
                    "event": event[:100],
                    "significance": t.get("significance", "")[:100],
                })

        # 合并剧情线
        for pt in data.get("plot_threads", []):
            name = pt.get("name", "").strip()
            if not name or name in seen_names["plot_threads"]:
                continue
            seen_names["plot_threads"].add(name)
            merged["plot_threads"].append({
                "name": name,
                "status": pt.get("status", "active"),
                "involved_characters": pt.get("involved_characters", []),
                "description": pt.get("description", "")[:200],
            })

        # 收集设定要点
        if data.get("key_settings"):
            merged["key_settings_hints"].append(data["key_settings"])
        if data.get("character_focus"):
            merged["character_focus_hints"].append(data["character_focus"])

    return merged


def generate_novel_settings_from_world_bible(
    client,
    world_data: dict,
    model: str,
) -> dict:
    """
    从提取的世界书数据生成小说设定（背景故事、主角描述、写作要求）。

    Returns:
        {
            "background_story": str,
            "protagonist_bio": str,
            "writing_demand": str,
        }
    """
    # 格式化世界书数据供 prompt 使用
    chars_str = "\n".join(
        f"- {c['name']}（{'、'.join(c.get('aliases', []))}）: {c.get('traits', '')[:100]}"
        for c in world_data.get("characters", [])[:10]
    ) or "（无）"
    locs_str = "\n".join(
        f"- {l['name']}: {l.get('description', '')[:60]}"
        for l in world_data.get("locations", [])[:10]
    ) or "（无）"
    rules_str = "\n".join(f"- {r[:80]}" for r in world_data.get("rules", [])[:10]) or "（无）"
    plot_str = "\n".join(
        f"- {p['name']} [{p.get('status', 'active')}]: {p.get('description', '')[:60]}"
        for p in world_data.get("plot_threads", [])[:10]
    ) or "（无）"
    timeline_str = "\n".join(
        f"- {t.get('event', '')[:60]}"
        for t in world_data.get("timeline", [])[:10]
    ) or "（无）"

    prompt = BACKGROUND_PROMPT.format(
        characters=chars_str,
        locations=locs_str,
        rules=rules_str,
        plot_threads=plot_str,
        timeline=timeline_str,
    )

    try:
        raw = _call_api(client, [{"role": "user", "content": prompt}], model, max_tokens=4096, temperature=0.3)
        data = _parse_json(raw)
        return {
            "background_story": data.get("background_story", ""),
            "protagonist_bio": data.get("protagonist_bio", ""),
            "writing_demand": data.get("writing_demand", ""),
        }
    except Exception:
        return {
            "background_story": "",
            "protagonist_bio": "",
            "writing_demand": "",
        }


# ========== 旧接口保留（兼容现有调用） ==========


def _split_segments(text: str) -> list[tuple[str, str]]:
    """按 # 标题分割文本；无标题则按段落分组（每 5 段一组）。"""
    lines = text.split("\n")
    heading_candidates = [i for i, line in enumerate(lines) if re.match(r"^#{1,6}\s+", line)]

    if len(heading_candidates) >= 2:
        segments = []
        for idx, h_pos in enumerate(heading_candidates):
            heading = lines[h_pos].strip().lstrip("#").strip()
            next_pos = heading_candidates[idx + 1] if idx + 1 < len(heading_candidates) else len(lines)
            content = "\n".join(lines[h_pos + 1:next_pos]).strip()
            if content:
                segments.append((heading, content))
        return segments
    elif len(heading_candidates) == 1:
        content = "\n".join(lines[heading_candidates[0] + 1:]).strip()
        heading = lines[heading_candidates[0]].strip().lstrip("#").strip()
        return [(heading, content)] if content else [("全文", text)]
    else:
        paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
        if len(paragraphs) <= 5:
            return [("全文", text)]
        segments = []
        group_size = 5
        for i in range(0, len(paragraphs), group_size):
            group = paragraphs[i:i + group_size]
            heading = f"段落 {i // group_size + 1}"
            content = "\n\n".join(group)
            segments.append((heading, content))
        return segments


def _summarize_segment(client, heading: str, content: str, model: str) -> str:
    """调用 API 对单段内容生成摘要（旧行为）"""
    try:
        prompt = (
            f"以下是一篇文章中标题为「{heading}」的部分。\n"
            f"请用 100 字以内总结其核心要点，保留关键信息和结论：\n\n{content[:4000]}"
        )
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=500,
            temperature=0.3,
        )
        return response.choices[0].message.content or ""
    except Exception as e:
        return f"[摘要失败: {e}]"


def split_and_summarize(
    client,
    file_path: str,
    model: str,
    *,
    progress_callback: Callable[[int, int], None] | None = None,
) -> list[dict]:
    """
    旧接口：读取文件 → 分段 → 逐段调用 API 摘要
    保留向后兼容。
    """
    with open(file_path, "r", encoding="utf-8") as f:
        text = f.read()

    segments = _split_segments(text)
    results = []

    for i, (heading, content) in enumerate(segments, 1):
        preview = content[:200].replace("\n", " ")
        summary = _summarize_segment(client, heading, content, model)
        results.append({
            "heading": heading,
            "summary": summary,
            "content_preview": preview,
        })
        if progress_callback:
            progress_callback(i, len(segments))

    return results

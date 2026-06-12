"""
文件分段摘要 + 世界观提取模块
功能：
- AI 语义分段（不依赖 # 标题，由大模型按话题转折划分层次）
- 逐段提取角色、地点、规则、时间线、剧情线
- 合并为完整的 WorldBible
- 自动生成小说设定（背景故事、主角描述）
"""

import difflib
import json
import re
import time
from typing import Callable


SEGMENT_PROMPT = """分析以下文本的话题转折点，在转折处插入分隔标记。

规则：
- 只在话题/场景/时间发生明显转折时插入
- 不要在段落中间插入

- 在转折处插入：<!--BREAK-->
  并在下一行用 ## 写小标题（10字以内）
- 不要改动原文其他任何文字
- 如果全文一气呵成不需要分段，回复：无需分段

文本：
{full_text}"""


EXTRACT_PROMPT = """你是一个小说信息深度提取专家。请严格根据以下文本内容，深度提取其中的角色、地点、世界观规则、事件和剧情线索。

文本标题：{title}

约束：
- 严格基于原文，不要添加社会学分析、心理描写分析或道德评判
- 对于标注了【原文引用】的字段，直接从原文复制原文，不要改写或概括
- 对于未标注【原文引用】的字段，可以适当概括但保留所有关键信息
- 宁多勿少，不确定该不该提取的信息请提取出来

{dedup_context}

文本内容：
{content}

请严格按照以下 JSON 格式输出，不包含任何其他文字：

{{
  "characters": [
    {{
      "name": "角色名",
      "aliases": ["别名", "别称"],
      "traits": "【500字内】性格描写、外貌特征、能力特长——尽可能详细地从原文提取",
      "relationships": [
        {{"target": "关系对象", "type": "friend/enemy/family/master/student/ally/rival/lover", "description": "关系描述（30字内）"}}
      ],
      "status": "alive/dead/missing/transformed",
      "importance": "major/normal/minor",
      "key_details": ["【原文引用】从原文中直接复制关于该角色的重要描述片段（每段100字内）"],
      "key_dialogues": ["【原文引用】从原文中直接复制该角色说出的重要台词（每句100字内）"],
      "motivation": "该角色的核心动机/目标（100字内）",
      "arc": "该角色的成长弧线/变化趋势（100字内）"
    }}
  ],
  "locations": [
    {{
      "name": "地点名",
      "description": "【300字内】地点的外观、氛围、布局等详细描述",
      "significance": "【200字内】该地点在故事中的重要性/象征意义",
      "key_details": ["【原文引用】从原文中直接复制关于该地点的重要描写片段"],
      "atmosphere": "【200字内】该地点的氛围/给人的感觉"
    }}
  ],
  "rules": ["世界观规则1（完整保留原文描述）", "规则2"],
  "timeline": [
    {{
      "event": "【200字内】核心事件的详细描述",
      "significance": "【200字内】该事件的影响/意义",
      "key_passages": ["【原文引用】从原文中直接复制该事件中最重要的一段描写"],
      "foreshadowing_hints": ["该事件中埋下的伏笔或暗示（50字内）"]
    }}
  ],
  "plot_threads": [
    {{
      "name": "剧情线索名",
      "status": "active/resolved/dormant",
      "importance": "major/normal/minor",
      "involved_characters": ["角色名"],
      "description": "【300字内】该线索的详细描述",
      "key_details": ["【原文引用】关于该剧情线的重要原文片段"],
      "foreshadowing_related": ["该剧情线涉及的前期伏笔（50字内）"]
    }}
  ],
  "key_worldbuilding": [
    {{"topic": "设定主题", "passage": "【原文引用】从原文中直接复制重要的世界观设定段落（300字内）"}}
  ],
  "global_key_dialogues": [
    {{"speaker": "说话者", "dialogue": "【原文引用】重要对话原文", "context": "对话背景（30字内）"}}
  ],
  "global_foreshadowing": [
    {{"hint": "伏笔内容（50字内）", "relates_to": "可能相关的剧情线或角色（20字内）"}}
  ]
}

如果没有某项内容，用空数组 []。确保 JSON 合法。"""


BACKGROUND_PROMPT = """你是一位小说设定整理助手。以下是从一份小说设定文档中提取出的结构化世界观信息。
请根据这些信息，生成三份严格基于已有设定的参考文本。

约束：
- 严格基于已有设定，不要自由发挥或添加原文没有的内容
- 不要做社会学分析或文学评论
- 只做信息整合，不做创造性扩展

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


def _call_api(client, messages, model, max_tokens=32768, temperature=0.1, global_user_prompt=""):
    """调用 API，带重试"""
    if global_user_prompt.strip() and messages and messages[-1].get("role") == "user":
        messages[-1] = {
            "role": "user",
            "content": messages[-1]["content"] + f"\n\n用户偏好参考: {global_user_prompt}"
        }
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


def _parse_json(text: str) -> dict:
    """从 API 返回文本中解析 JSON（处理 markdown 代码块包裹）"""
    text = text.strip()
    if "```json" in text:
        text = text.split("```json")[1].split("```")[0].strip()
    elif "```" in text:
        text = text.split("```")[1].split("```")[0].strip()
    return json.loads(_repair_json(_extract_json_object(text)))


def _extract_json_object(text: str) -> str:
    """截取响应中的第一个 JSON 对象，避免模型附带解释文本导致解析失败。"""
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        return text[start:end + 1]
    return text


def _repair_json(text: str) -> str:
    """修复常见 JSON 响应问题。"""
    text = text.strip()
    text = re.sub(r",\s*([}\]])", r"\1", text)
    return text


def _parse_json_with_repair(text: str) -> dict:
    """尽量稳健地解析 LLM JSON。"""
    candidates = []
    raw = text.strip()
    if "```json" in raw:
        raw = raw.split("```json", 1)[1].split("```", 1)[0].strip()
    elif "```" in raw:
        raw = raw.split("```", 1)[1].split("```", 1)[0].strip()
    candidates.append(raw)
    candidates.append(_extract_json_object(raw))
    candidates.append(_repair_json(_extract_json_object(raw)))

    last_error = None
    for candidate in candidates:
        try:
            return json.loads(candidate)
        except json.JSONDecodeError as exc:
            last_error = exc
    raise last_error or json.JSONDecodeError("Invalid JSON", raw, 0)


def _safe_format(template: str, **kwargs) -> str:
    """安全的模板替换，值中含 { 或 } 不会导致崩溃。

    先替换 {key} 占位符，再转换 {{ → { 、}} → }，
    避免 Python str.format() 在用户内容含 {/} 时报错。
    """
    result = template
    for key, value in kwargs.items():
        result = result.replace("{" + key + "}", value)
    result = result.replace("{{", "{").replace("}}", "}")
    return result


def _verify_verbatim(text: str, source: str) -> str:
    """将 LLM 输出的引用文本与源文本做模糊匹配，替换为精确原文"""
    if not text or not source:
        return text
    if text in source:
        return text
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


SYNTHESIS_PROMPT = """你是一个小说信息合成专家。以下是从同一部作品的多个段落中分别提取的世界观信息。
请在保留所有原文引用信息的前提下，合成、去重、整合为一份统一的结构化数据。

核心任务：
1. **合并同名角色**：将同一角色的信息合并，累加 aliases、key_details、key_dialogues
2. **交叉识别**：识别跨段落出现的同一人物/地点/剧情线，统一命名（合并同人异名时用出现次数较多的名称）
3. **去重**：去除重复的规则、事件、关键细节
4. **优先级**：重要性更高的版本优先，详细信息累积
5. **全局识别**：从各段落的零散信息中识别出全局性的伏笔（global_foreshadowing）、关键世界观段落（key_worldbuilding）
6. **保留原文引用**：所有 key_details、key_dialogues、key_passages 等原文引用字段必须原样保留，不要改写

输入数据：
{accumulated_data}

请按以下 JSON 格式输出：
{{
  "characters": [
    {{
      "name": "角色名",
      "aliases": ["别名"],
      "traits": "整合后的详细描述",
      "relationships": [{{"target": "关系对象", "type": "关系类型", "description": "描述"}}],
      "status": "alive/dead/missing/transformed",
      "importance": "major/normal/minor",
      "key_details": ["合并所有段落中关于该角色的原文引用，去重"],
      "key_dialogues": ["合并所有段落中该角色的台词，去重"],
      "motivation": "合并后的动机描述",
      "arc": "合并后的角色弧线"
    }}
  ],
  "locations": [...],
  "rules": ["去重后的规则列表"],
  "timeline": [{"event": "事件", "significance": "意义", "key_passages": [...], "foreshadowing_hints": [...]}],
  "plot_threads": [
    {{
      "name": "剧情线名",
      "status": "active/resolved/dormant",
      "importance": "major/normal/minor",
      "involved_characters": ["角色"],
      "description": "描述",
      "key_details": ["原文引用"],
      "foreshadowing_related": ["相关伏笔"]
    }}
  ],
  "key_worldbuilding": [{{"topic": "主题", "passage": "原文引用"}}],
  "global_key_dialogues": [{{"speaker": "说话者", "dialogue": "原文", "context": "背景"}}],
  "global_foreshadowing": [{{"hint": "伏笔", "relates_to": "关联"}}]
}}

如果没有某项内容，用空数组 []。确保 JSON 合法。"""


# ========== 对外接口 ==========


TITLE_PROMPT = """分析以下文本的核心主题，给出一个简短的段落标题（10字以内）。

文本：
{text}

标题："""


def _title_chunk(client, chunk_text: str, model: str, global_user_prompt: str = "") -> str:
    """让 AI 为一个文本块生成标题"""
    prompt = _safe_format(TITLE_PROMPT, text=chunk_text[:2000])
    try:
        raw = _call_api(client, [{"role": "user", "content": prompt}], model,
                        max_tokens=100, temperature=0.1, global_user_prompt=global_user_prompt)
        title = raw.strip().rstrip('.！。').replace('"', '').replace('「', '').replace('」', '')
        return title[:20] or "未命名段落"
    except Exception:
        # 标题提取可以接受失败，不影响正文处理
        return "未命名段落"


def _parse_break_markers(raw: str) -> list[tuple[str, str]] | None:
    """解析 AI 输出中的 <!--BREAK--> 分隔标记，返回 [(title, content), ...]"""
    if not raw.strip() or raw.strip() == "无需分段":
        return None

    parts = raw.split("<!--BREAK-->")
    if len(parts) <= 1:
        return None

    segments = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        # 提取 ## 标题
        m = re.match(r'##\s*(.+?)(?:\n|$)', part)
        title = m.group(1).strip() if m else "未命名段落"
        # 去掉标题行，剩余为正文
        content = re.sub(r'^##\s*.+?\n', '', part, count=1).strip()
        if content:
            segments.append((title, content))

    return segments or None


def split_text_locally(text: str, max_chars: int = 3000) -> list[tuple[str, str]]:
    """
    本地确定性分段兜底。

    AI 分段失败时不能退回整篇“全文”，否则后续世界书提取会看起来像没有分段。
    """
    text = text.strip()
    if not text:
        return []

    titled = detect_sections(text)
    if titled:
        return titled

    paragraphs = [p.strip() for p in re.split(r"\n\s*\n+", text) if p.strip()]
    if len(paragraphs) >= 2:
        return [(f"段落 {idx}", para) for idx, para in enumerate(paragraphs, 1)]

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) >= 2:
        return [(f"段落 {idx}", line) for idx, line in enumerate(lines, 1)]

    if len(text) <= max_chars:
        return [("全文", text)]

    result = []
    for idx, start in enumerate(range(0, len(text), max_chars), 1):
        result.append((f"片段 {idx}", text[start:start + max_chars]))
    return result


def segment_by_ai(client, text: str, model: str, global_user_prompt: str = "") -> list[tuple[str, str]]:
    """
    AI 语义分段。

    将全文发给 AI，由 AI 在语义转折处插入 <!--BREAK--> 分隔标记，
    客户端按标记切分并提取标题。不依赖空行或 # 标题。
    """
    prompt = _safe_format(SEGMENT_PROMPT, full_text=text)
    try:
        raw = _call_api(client, [{"role": "user", "content": prompt}], model,
                        max_tokens=max(len(text) * 2, 8192),
                        global_user_prompt=global_user_prompt)
        segments = _parse_break_markers(raw)
        if segments:
            return segments
    except Exception:
        pass

    # Fallback：先按自然段/行本地拆分，避免短文直接退回“全文”一段
    local_segments = split_text_locally(text)
    if len(local_segments) > 1 or len(text) <= 5000:
        return local_segments

    # 长文本没有自然段时，按 3000 字均匀切块，每块用 AI 取标题
    n = len(text)
    chunk_size = n // max(1, min(6, n // 3000))
    result = []
    for i in range(0, n, chunk_size):
        chunk = text[i:i + chunk_size]
        title = _title_chunk(client, chunk, model,
                             global_user_prompt=global_user_prompt)
        result.append((title, chunk))
    return result


def extract_world_bible_from_segments(
    client,
    segments: list[tuple[str, str]],
    model: str,
    *,
    progress_callback: Callable[[int, int], None] | None = None,
    global_user_prompt: str = "",
) -> dict:
    """
    对每个语义段落提取世界观信息，合并为完整数据。

    Returns:
        dict with keys: characters, locations, rules, timeline, plot_threads,
                        key_worldbuilding, global_foreshadowing, global_key_dialogues
    """
    merged = {
        "characters": [],
        "locations": [],
        "rules": [],
        "timeline": [],
        "plot_threads": [],
        "key_worldbuilding": [],
        "global_foreshadowing": [],
        "global_key_dialogues": [],
        "_errors": [],
    }
    seen_names = {"characters": set(), "locations": set(), "rules": set(), "plot_threads": set()}
    # 用 chapter_marker 标记语义段落序号（从 1 开始，避免"第0章"）
    chapter_marker = 1
    # 用于 synthesis 步骤的全量累积数据
    all_segment_data = []
    full_text = ""

    for idx, (title, content) in enumerate(segments):
        full_text += content
        if progress_callback:
            progress_callback(idx + 1, len(segments))

        # 控制每段送审长度
        content_sample = content[:6000]

        # 去重上下文
        known_chars = list(seen_names["characters"])
        if known_chars:
            dedup_context = (
                "已有角色列表：" + "、".join(known_chars) + "\n"
                "如果当前文本中的某个角色与已有角色是同一人（如别名、代称），\n"
                "请使用已有名称，并在 aliases 中标注新出现的称呼。\n"
            )
        else:
            dedup_context = ""

        prompt = _safe_format(EXTRACT_PROMPT, title=title, content=content_sample, dedup_context=dedup_context)
        data = None
        last_error = None
        for max_tokens in (8192, 16384):
            try:
                raw = _call_api(
                    client,
                    [{"role": "user", "content": prompt}],
                    model,
                    max_tokens=max_tokens,
                    temperature=0.1,
                    global_user_prompt=global_user_prompt,
                )
                data = _parse_json_with_repair(raw)
                break
            except Exception as exc:
                last_error = exc
                prompt += "\n\n注意：上一轮输出无法解析。请只输出完整、合法的 JSON，不要添加解释，不要截断。"
        if data is None:
            merged["_errors"].append(f"段落 {idx + 1}「{title}」提取失败: {last_error}")
            chapter_marker += 1
            continue

        all_segment_data.append(data)

        # 合并角色
        for ch in data.get("characters", []):
            name = ch.get("name", "").strip()
            if not name:
                continue
            if name in seen_names["characters"]:
                # 更新已有角色（后续段落补充新信息）
                for existing in merged["characters"]:
                    if existing["name"] == name:
                        if ch.get("traits"):
                            existing["traits"] = ch["traits"][:500]
                        if ch.get("status") in ("alive", "dead", "missing", "transformed"):
                            existing["status"] = ch["status"]
                        for alias in ch.get("aliases", []):
                            if alias and alias not in existing["aliases"]:
                                existing["aliases"].append(alias)
                        new_imp = ch.get("importance", "normal")
                        imp_rank = {"major": 3, "normal": 2, "minor": 1}
                        if imp_rank.get(new_imp, 0) > imp_rank.get(existing.get("importance", "normal"), 0):
                            existing["importance"] = new_imp
                        _merge_list_dedup(existing["key_details"], [_verify_verbatim(kd, content) for kd in ch.get("key_details", [])])
                        _merge_list_dedup(existing["key_dialogues"], [_verify_verbatim(kd, content) for kd in ch.get("key_dialogues", [])])
                        if ch.get("motivation"):
                            existing["motivation"] = ch["motivation"][:200]
                        if ch.get("arc"):
                            existing["arc"] = ch["arc"][:200]
                        for r in ch.get("relationships", []):
                            if not any(r.get("target") == rel.get("target") for rel in existing["relationships"]):
                                existing["relationships"].append(r)
                        break
            else:
                seen_names["characters"].add(name)
                merged["characters"].append({
                    "name": name,
                    "aliases": ch.get("aliases", []),
                    "traits": ch.get("traits", "")[:500],
                    "relationships": ch.get("relationships", []),
                    "status": ch.get("status", "alive"),
                    "importance": ch.get("importance", "normal"),
                    "first_appearance": chapter_marker,
                    "key_details": [_verify_verbatim(kd, content) for kd in ch.get("key_details", [])],
                    "key_dialogues": [_verify_verbatim(kd, content) for kd in ch.get("key_dialogues", [])],
                    "motivation": ch.get("motivation", "")[:200],
                    "arc": ch.get("arc", "")[:200],
                })

        # 合并地点
        for loc in data.get("locations", []):
            name = loc.get("name", "").strip()
            if not name:
                continue
            if name in seen_names["locations"]:
                # 更新已有地点
                for existing in merged["locations"]:
                    if existing["name"] == name:
                        if loc.get("description"):
                            existing["description"] = loc["description"][:300]
                        if loc.get("significance"):
                            existing["significance"] = loc["significance"][:200]
                        _merge_list_dedup(existing["key_details"], [_verify_verbatim(kd, content) for kd in loc.get("key_details", [])])
                        if loc.get("atmosphere"):
                            existing["atmosphere"] = loc["atmosphere"][:200]
                        break
            else:
                seen_names["locations"].add(name)
                merged["locations"].append({
                    "name": name,
                    "description": loc.get("description", "")[:300],
                    "significance": loc.get("significance", "")[:200],
                    "first_appearance": chapter_marker,
                    "key_details": [_verify_verbatim(kd, content) for kd in loc.get("key_details", [])],
                    "atmosphere": loc.get("atmosphere", "")[:200],
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
                    "event": event[:200],
                    "significance": t.get("significance", "")[:200],
                    "key_passages": [_verify_verbatim(kp, content) for kp in t.get("key_passages", [])],
                    "foreshadowing_hints": [fh[:50] for fh in t.get("foreshadowing_hints", [])],
                })

        # 合并剧情线
        for pt in data.get("plot_threads", []):
            name = pt.get("name", "").strip()
            if not name:
                continue
            if name in seen_names["plot_threads"]:
                # 更新已有剧情线
                for existing in merged["plot_threads"]:
                    if existing["name"] == name:
                        if pt.get("status") in ("active", "resolved", "dormant"):
                            existing["status"] = pt["status"]
                        if pt.get("description"):
                            existing["description"] = pt["description"][:300]
                        for char in pt.get("involved_characters", []):
                            if char and char not in existing["involved_characters"]:
                                existing["involved_characters"].append(char)
                        new_imp = pt.get("importance", "normal")
                        imp_rank = {"major": 3, "normal": 2, "minor": 1}
                        if imp_rank.get(new_imp, 0) > imp_rank.get(existing.get("importance", "normal"), 0):
                            existing["importance"] = new_imp
                        _merge_list_dedup(existing["key_details"], [_verify_verbatim(kd, content) for kd in pt.get("key_details", [])])
                        _merge_list_dedup(existing["foreshadowing_related"], [fr[:50] for fr in pt.get("foreshadowing_related", [])])
                        break
            else:
                seen_names["plot_threads"].add(name)
                merged["plot_threads"].append({
                    "name": name,
                    "status": pt.get("status", "active"),
                    "importance": pt.get("importance", "normal"),
                    "involved_characters": pt.get("involved_characters", []),
                    "description": pt.get("description", "")[:300],
                    "key_details": [_verify_verbatim(kd, content) for kd in pt.get("key_details", [])],
                    "foreshadowing_related": [fr[:50] for fr in pt.get("foreshadowing_related", [])],
                })

        # 合并顶层字段
        for item in data.get("key_worldbuilding", []):
            topic = item.get("topic", "").strip()
            passage = _verify_verbatim(item.get("passage", "").strip(), content)
            if topic and passage:
                merged["key_worldbuilding"].append({"topic": topic, "passage": passage[:300]})
        for item in data.get("global_key_dialogues", []):
            dialogue = _verify_verbatim(item.get("dialogue", "").strip(), content)
            if dialogue:
                merged["global_key_dialogues"].append({
                    "speaker": item.get("speaker", "").strip(),
                    "dialogue": dialogue,
                    "context": item.get("context", "")[:30],
                })
        for item in data.get("global_foreshadowing", []):
            hint = item.get("hint", "").strip()
            if hint:
                merged["global_foreshadowing"].append({
                    "hint": hint[:50],
                    "relates_to": item.get("relates_to", "")[:20],
                })

        chapter_marker += 1

    # === 跨段落合成（仅段数 >= 3 时触发） ===
    if len(all_segment_data) >= 3:
        try:
            _run_synthesis(client, merged, model, global_user_prompt=global_user_prompt)
        except Exception:
            pass  # 合成失败不影响已有提取结果

    # === 按信息量重算重要等级（取代 AI 单段的主观判断） ===
    for ch in merged.get("characters", []):
        detail_count = len(ch.get("key_details", [])) + len(ch.get("key_dialogues", []))
        if detail_count >= 4:
            ch["importance"] = "major"
        elif detail_count >= 1:
            ch["importance"] = "normal"
        else:
            ch["importance"] = "minor"

    for pt in merged.get("plot_threads", []):
        detail_count = len(pt.get("key_details", [])) + len(pt.get("foreshadowing_related", []))
        if detail_count >= 4:
            pt["importance"] = "major"
        elif detail_count >= 1:
            pt["importance"] = "normal"
        else:
            pt["importance"] = "minor"

    return merged


def _run_synthesis(client, merged: dict, model: str, global_user_prompt: str = "") -> None:
    """跨段落合成步骤：调用 API 去重、合并、识别全局信息"""
    # 构建累积数据摘要
    summary_parts = []
    chars = merged.get("characters", [])
    locs = merged.get("locations", [])
    threads = merged.get("plot_threads", [])

    if chars:
        summary_parts.append("【角色】" + "、".join(c["name"] for c in chars[:10]))
    if locs:
        summary_parts.append("【地点】" + "、".join(l["name"] for l in locs[:10]))
    if threads:
        summary_parts.append("【剧情线】" + "、".join(p["name"] for p in threads[:10]))
    summary_parts.append(f"规则 {len(merged.get('rules', []))} 条")
    summary_parts.append(f"事件 {len(merged.get('timeline', []))} 个")
    summary_text = "\n".join(summary_parts)

    # 将已提取的简要数据传给 synthesis prompt
    import json as _json
    accumulated = _json.dumps({
        "characters": [{"name": c["name"], "traits": c.get("traits", "")[:100], "key_details": c.get("key_details", [])[:3]} for c in chars],
        "locations": [{"name": l["name"], "description": l.get("description", "")[:100], "key_details": l.get("key_details", [])[:3]} for l in locs[:10]],
        "plot_threads": [{"name": p["name"], "description": p.get("description", "")[:100], "key_details": p.get("key_details", [])[:3]} for p in threads[:10]],
        "timeline_count": len(merged.get("timeline", [])),
        "rules_count": len(merged.get("rules", [])),
        "key_worldbuilding": merged.get("key_worldbuilding", [])[:3],
        "global_foreshadowing": merged.get("global_foreshadowing", [])[:3],
    }, ensure_ascii=False)

    prompt = _safe_format(SYNTHESIS_PROMPT, accumulated_data=accumulated)
    try:
        raw = _call_api(client, [{"role": "user", "content": prompt}], model, max_tokens=4096, temperature=0.1, global_user_prompt=global_user_prompt)
        syn_data = _parse_json(raw)
    except Exception:
        return

    # 合并 synthesis 结果回 merged
    # 角色：追加合成发现的新 key_details/key_dialogues
    syn_chars = {c["name"]: c for c in syn_data.get("characters", []) if c.get("name")}
    for existing in merged.get("characters", []):
        syn = syn_chars.get(existing["name"])
        if syn:
            _merge_list_dedup(existing.setdefault("key_details", []), syn.get("key_details", []))
            _merge_list_dedup(existing.setdefault("key_dialogues", []), syn.get("key_dialogues", []))
            if syn.get("motivation") and not existing.get("motivation"):
                existing["motivation"] = syn["motivation"][:200]
            if syn.get("arc") and not existing.get("arc"):
                existing["arc"] = syn["arc"][:200]

    # 剧情线：追加合成发现的关键细节和伏笔
    syn_threads = {p["name"]: p for p in syn_data.get("plot_threads", []) if p.get("name")}
    for existing in merged.get("plot_threads", []):
        syn = syn_threads.get(existing["name"])
        if syn:
            _merge_list_dedup(existing.setdefault("key_details", []), syn.get("key_details", []))
            _merge_list_dedup(existing.setdefault("foreshadowing_related", []), syn.get("foreshadowing_related", []))

    # 顶层字段
    for item in syn_data.get("key_worldbuilding", []):
        topic = item.get("topic", "").strip()
        passage = item.get("passage", "").strip()
        if topic and passage and not any(ex.get("topic") == topic for ex in merged.get("key_worldbuilding", [])):
            merged.setdefault("key_worldbuilding", []).append({"topic": topic, "passage": passage[:300]})
    for item in syn_data.get("global_foreshadowing", []):
        hint = item.get("hint", "").strip()
        if hint and not any(f.get("hint") == hint for f in merged.get("global_foreshadowing", [])):
            merged.setdefault("global_foreshadowing", []).append({"hint": hint[:50], "relates_to": item.get("relates_to", "")[:20]})
    for item in syn_data.get("global_key_dialogues", []):
        dialogue = item.get("dialogue", "").strip()
        if dialogue and not any(d.get("dialogue") == dialogue for d in merged.get("global_key_dialogues", [])):
            merged.setdefault("global_key_dialogues", []).append({
                "speaker": item.get("speaker", "").strip(),
                "dialogue": dialogue,
                "context": item.get("context", "")[:30],
            })


def generate_novel_settings_from_world_bible(
    client,
    world_data: dict,
    model: str,
    global_user_prompt: str = "",
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
    max_entries = 15
    chars_str = "\n".join(
        f"- {c['name']}（{'、'.join(c.get('aliases', []))}）: {c.get('traits', '')[:300]}"
        for c in world_data.get("characters", [])[:max_entries]
    ) or "（无）"
    locs_str = "\n".join(
        f"- {l['name']}: {l.get('description', '')[:200]}"
        for l in world_data.get("locations", [])[:max_entries]
    ) or "（无）"
    rules_str = "\n".join(f"- {r[:150]}" for r in world_data.get("rules", [])[:max_entries]) or "（无）"
    plot_str = "\n".join(
        f"- {p['name']} [{p.get('status', 'active')}]: {p.get('description', '')[:150]}"
        for p in world_data.get("plot_threads", [])[:max_entries]
    ) or "（无）"
    timeline_str = "\n".join(
        f"- {t.get('event', '')[:150]}"
        for t in world_data.get("timeline", [])[:max_entries]
    ) or "（无）"

    prompt = _safe_format(BACKGROUND_PROMPT,
        characters=chars_str,
        locations=locs_str,
        rules=rules_str,
        plot_threads=plot_str,
        timeline=timeline_str,
    )

    try:
        raw = _call_api(client, [{"role": "user", "content": prompt}], model, max_tokens=4096, temperature=0.3, global_user_prompt=global_user_prompt)
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


# ========== 📑 段落检测与 # 标题解析 ==========


def has_proper_sections(text: str) -> bool:
    """
    检查文本是否有正确的一级标题（#）划分。

    要求：
    - 有 2+ 个 # 标题行（仅一级，一个 # + 空格）
    - 没有 ## 或 ### 标题行

    Returns:
        True 表示文本已正确划分，False 表示需要 AI 分段
    """
    h1_count = 0
    has_h2_or_h3 = False
    for line in text.split('\n'):
        stripped = line.strip()
        if re.match(r'^#[^#]', stripped):   # # 开头但不是 ## 或 ###
            h1_count += 1
        elif re.match(r'^#{2,3}\s', stripped):
            has_h2_or_h3 = True
    return h1_count >= 2 and not has_h2_or_h3


def detect_sections(text: str) -> list[tuple[str, str]]:
    """
    按 # 一级标题解析文本段落。

    Returns:
        [(title, content), ...]  每个段落的标题和正文
        如果没有正确的 # 划分则返回空列表
    """
    lines = text.split('\n')
    h1_positions = [i for i, line in enumerate(lines) if re.match(r'^#(?!#)\s*', line.strip())]
    if len(h1_positions) < 2:
        return []

    sections = []
    for idx, pos in enumerate(h1_positions):
        heading = lines[pos].strip().lstrip('#').strip()
        next_pos = h1_positions[idx + 1] if idx + 1 < len(h1_positions) else len(lines)
        content = '\n'.join(lines[pos + 1:next_pos]).strip()
        if heading and content:
            sections.append((heading, content))

    return sections

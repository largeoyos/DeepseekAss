"""
文件分段摘要模块
功能：读取文件，按标题或语义分段，逐段调用 API 生成摘要
"""

import re
from typing import Callable


def split_and_summarize(
    client,
    file_path: str,
    model: str,
    *,
    progress_callback: Callable[[int, int], None] | None = None,
) -> list[dict]:
    """
    读取文件 → 分段 → 逐段调用 API 摘要

    Args:
        client: OpenAI 兼容客户端 (raw_client)
        file_path: 文件路径 (.txt / .md)
        model: 模型名称
        progress_callback: (current, total) 进度回调

    Returns:
        [{"heading": str, "summary": str, "content_preview": str}, ...]
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


def _split_segments(text: str) -> list[tuple[str, str]]:
    """
    按 # 标题分割文本；无标题则按段落分组（每 5 段一组）。
    返回 [(标题, 内容), ...]
    """
    lines = text.split("\n")
    heading_candidates = [i for i, line in enumerate(lines) if re.match(r"^#{1,6}\s+", line)]

    if len(heading_candidates) >= 2:
        # 有标题结构，按标题分割
        segments = []
        for idx, h_pos in enumerate(heading_candidates):
            heading = lines[h_pos].strip().lstrip("#").strip()
            next_pos = heading_candidates[idx + 1] if idx + 1 < len(heading_candidates) else len(lines)
            content = "\n".join(lines[h_pos + 1:next_pos]).strip()
            if content:
                segments.append((heading, content))
        return segments
    elif len(heading_candidates) == 1:
        # 只有一个标题，全部作为一段
        content = "\n".join(lines[heading_candidates[0] + 1:]).strip()
        heading = lines[heading_candidates[0]].strip().lstrip("#").strip()
        return [(heading, content)] if content else [("全文", text)]
    else:
        # 无标题，按段落分组（每 5 段一组）
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


def _summarize_segment(
    client, heading: str, content: str, model: str
) -> str:
    """调用 API 对单段内容生成摘要"""
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

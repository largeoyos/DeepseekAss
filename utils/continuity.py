"""小说长篇连贯性审稿与定向修补工具。"""

from __future__ import annotations

import json

from utils.prompts import Prompts


def _parse_json(text: str) -> dict:
    raw = (text or "").strip()
    if "```json" in raw:
        raw = raw.split("```json", 1)[1].split("```", 1)[0].strip()
    elif "```" in raw:
        raw = raw.split("```", 1)[1].split("```", 1)[0].strip()
    try:
        return json.loads(raw)
    except Exception:
        return {"has_issues": False, "issues": [], "repair_instruction": ""}


def audit_chapter_continuity(
    client,
    *,
    chapter_content: str,
    context: str,
    chapter_title: str,
    model: str,
    global_user_prompt: str = "",
    xp_mode: bool = False,
) -> dict:
    """低温度检查章节与上下文是否存在逻辑矛盾。失败时返回无问题，避免阻塞生成。"""
    parts = [
        f"【章节标题】\n{chapter_title}\n",
        f"【前文上下文/章节契约/世界书】\n{context[:18000]}\n",
        f"【待检查章节正文】\n{chapter_content[:30000]}\n",
    ]
    if global_user_prompt.strip():
        parts.append(f"【用户偏好提示】\n{global_user_prompt}\n")
    if xp_mode:
        parts.append(f"{Prompts.XP_MODE_SYSTEM}\n")

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": Prompts.CONTINUITY_AUDIT_SYSTEM},
                {"role": "user", "content": "\n".join(parts)},
            ],
            max_tokens=2048,
            temperature=0.1,
        )
        data = _parse_json(response.choices[0].message.content or "")
    except Exception:
        data = {"has_issues": False, "issues": [], "repair_instruction": ""}

    issues = data.get("issues", [])
    if not isinstance(issues, list):
        issues = []
    data["issues"] = issues[:8]
    data["has_issues"] = bool(data.get("has_issues") and issues)
    data["repair_instruction"] = str(data.get("repair_instruction", "") or "")
    return data


def repair_chapter_continuity(
    client,
    *,
    chapter_content: str,
    context: str,
    audit_result: dict,
    chapter_title: str,
    model: str,
    temperature: float = 0.4,
    global_user_prompt: str = "",
    xp_mode: bool = False,
) -> str:
    """按审稿意见做最小必要修补。失败或输出异常时返回空字符串。"""
    issues = audit_result.get("issues", [])
    if not issues:
        return ""
    parts = [
        f"【章节标题】\n{chapter_title}\n",
        f"【前文上下文/章节契约/世界书】\n{context[:18000]}\n",
        f"【审稿发现】\n{json.dumps(issues, ensure_ascii=False, indent=2)}\n",
        f"【综合修补指令】\n{audit_result.get('repair_instruction', '')}\n",
        f"【原章节正文】\n{chapter_content}\n",
    ]
    if global_user_prompt.strip():
        parts.append(f"【用户偏好提示】\n{global_user_prompt}\n")
    if xp_mode:
        parts.append(f"{Prompts.XP_MODE_SYSTEM}\n")

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": Prompts.CONTINUITY_REPAIR_SYSTEM},
                {"role": "user", "content": "\n".join(parts)},
            ],
            max_tokens=min(max(len(chapter_content) * 2, 8192), 32768),
            temperature=temperature,
        )
        repaired = response.choices[0].message.content or ""
        if len(repaired.strip()) < max(200, len(chapter_content.strip()) * 0.5):
            return ""
        return repaired.strip()
    except Exception:
        return ""

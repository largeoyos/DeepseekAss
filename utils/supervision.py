"""章节生成后的统一监督、定向修订与复检。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import difflib
import json
import re
from typing import Any, Callable

from utils.prompts import Prompts


OUTLINE_STATUSES = {"fulfilled", "partial", "missing", "conflict"}
SUPERVISION_AUDIT_SYSTEM = """You are a fiction chapter compliance supervisor. Audit task completion, not literary taste.
Split the user's chapter outline into minimal verifiable requirements. Each outline item status must be exactly fulfilled, partial, missing, or conflict. Mark fulfilled only when the chapter actually depicts the necessary process or outcome; a mention, memory, preview, or vague hint is insufficient.
Also detect title mismatch, forbidden-content violations, ignored requirements, major plot deviations, and continuity errors involving time, place, motivation, character knowledge, world rules, foreshadowing, or causality.
If no outline is supplied, do not invent outline items.
Return JSON only with this shape:
{
  "outline_items": [{"id":"1","requirement":"...","status":"fulfilled/partial/missing/conflict","evidence":"...","problem":"...","repair":"..."}],
  "hard_constraint_issues": [{"severity":"major/minor","type":"title/forbidden/requirement/plot_deviation","problem":"...","repair":"..."}],
  "continuity_issues": [{"severity":"major/minor","type":"timeline/place/motivation/knowledge/rule/foreshadowing/causality","problem":"...","repair":"..."}],
  "repair_instruction": "minimum combined repair instruction, or empty when all checks pass"
}"""
SUPERVISION_REPAIR_SYSTEM = """You are a fiction chapter repair editor. Repair only failed supervision items.
Preserve fulfilled plot points, voice, point of view, relationships, core events, and the user-specified ending.
Depict missing processes and outcomes rather than summarizing them in one sentence. Fix continuity with the smallest necessary change and introduce no new contradictions.
When length expansion is required, add relevant scene action, dialogue, sensory detail, and interiority without changing plot direction.
Output the complete repaired chapter only. Do not add explanations, prefaces, outlines, review notes, or afterwords."""


@dataclass
class SupervisionResult:
    status: str = "passed"
    outline_items: list[dict[str, Any]] = field(default_factory=list)
    hard_constraint_issues: list[dict[str, Any]] = field(default_factory=list)
    continuity_issues: list[dict[str, Any]] = field(default_factory=list)
    repair_instruction: str = ""
    repair_rounds: int = 0
    audit_failed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def unresolved_issues(self) -> list[dict[str, Any]]:
        outline = [
            item for item in self.outline_items
            if item.get("status") in {"partial", "missing", "conflict"}
        ]
        return outline + self.hard_constraint_issues + self.continuity_issues

    @property
    def needs_repair(self) -> bool:
        return bool(self.unresolved_issues)


def count_content_units(text: str) -> int:
    """按汉字和连续的拉丁字母/数字词组统计正文长度。"""
    hanzi = len(re.findall(r"[\u3400-\u4dbf\u4e00-\u9fff]", text or ""))
    words = len(re.findall(r"[A-Za-z0-9]+", text or ""))
    return hanzi + words


def format_repair_diff(before: str, after: str, max_chars: int = 6000) -> str:
    diff = ''.join(difflib.unified_diff(
        (before or '').splitlines(keepends=True),
        (after or '').splitlines(keepends=True),
        fromfile='修复前',
        tofile='修复后',
        n=1,
        lineterm='\n',
    )).strip()
    if not diff:
        return '（正文内容未发生可见变化）'
    if max_chars > 0 and len(diff) > max_chars:
        omitted = len(diff) - max_chars
        return f'{diff[:max_chars].rstrip()}\n……（其余 {omitted} 个字符已省略）'
    return diff


def format_repair_diff_for_markdown(diff: str) -> str:
    """Fence a unified diff so Markdown cannot parse its markers as headings."""
    text = str(diff or "")
    longest_run = max((len(item) for item in re.findall(r"`+", text)), default=0)
    fence = "`" * max(3, longest_run + 1)
    return f"{fence}diff\n{text}\n{fence}"


def _parse_json(text: str) -> dict[str, Any] | None:
    raw = (text or "").strip()
    if "```json" in raw:
        raw = raw.split("```json", 1)[1].split("```", 1)[0].strip()
    elif "```" in raw:
        raw = raw.split("```", 1)[1].split("```", 1)[0].strip()
    try:
        value = json.loads(raw)
    except Exception:
        return None
    return value if isinstance(value, dict) else None


def _local_hard_constraint_issues(
    chapter_content: str,
    target_words: int,
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    actual = count_content_units(chapter_content)
    if target_words > 0 and actual < target_words:
        issues.append({
            "severity": "major",
            "type": "word_count",
            "problem": f"正文约 {actual} 字，低于目标 {target_words} 字。",
            "repair": (
                f"在不改变既定剧情方向的前提下，将正文自然扩充到不少于 {target_words} 字；"
                "优先补足场景过程、动作、对话和必要心理活动，不得添加作者说明。"
            ),
            "actual": actual,
            "expected": target_words,
        })
    return issues


def _normalize_issue(item: Any, default_type: str) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    problem = str(item.get("problem", "") or "").strip()
    if not problem:
        return None
    severity = str(item.get("severity", "major") or "major").lower()
    if severity not in {"major", "minor"}:
        severity = "major"
    return {
        "severity": severity,
        "type": str(item.get("type", default_type) or default_type),
        "problem": problem,
        "repair": str(item.get("repair", "") or "").strip(),
    }


def _normalize_audit(
    data: dict[str, Any] | None,
    local_issues: list[dict[str, Any]],
) -> SupervisionResult:
    if data is None:
        return SupervisionResult(
            status="warning" if local_issues else "passed",
            hard_constraint_issues=local_issues,
            audit_failed=True,
        )

    outline_items: list[dict[str, Any]] = []
    for index, raw in enumerate(data.get("outline_items", []) or [], start=1):
        if not isinstance(raw, dict):
            continue
        status = str(raw.get("status", "missing") or "missing").lower()
        if status not in OUTLINE_STATUSES:
            status = "missing"
        outline_items.append({
            "id": str(raw.get("id", index)),
            "requirement": str(raw.get("requirement", "") or "").strip(),
            "status": status,
            "evidence": str(raw.get("evidence", "") or "").strip(),
            "problem": str(raw.get("problem", "") or "").strip(),
            "repair": str(raw.get("repair", "") or "").strip(),
        })

    semantic_hard = [
        normalized
        for item in (data.get("hard_constraint_issues", []) or [])
        if (normalized := _normalize_issue(item, "hard_constraint"))
    ]
    continuity = [
        normalized
        for item in (data.get("continuity_issues", []) or [])
        if (normalized := _normalize_issue(item, "continuity"))
    ]
    result = SupervisionResult(
        outline_items=outline_items,
        hard_constraint_issues=local_issues + semantic_hard,
        continuity_issues=continuity,
        repair_instruction=str(data.get("repair_instruction", "") or "").strip(),
    )
    result.status = "needs_repair" if result.needs_repair else "passed"
    return result


def audit_chapter(
    client,
    *,
    chapter_content: str,
    chapter_title: str,
    chapter_outline: str,
    requirements: str,
    continuity_context: str,
    target_words: int,
    model: str,
    global_user_prompt: str = "",
    xp_mode: bool = False,
) -> SupervisionResult:
    """执行本地硬约束检查和模型语义审计；模型失败时安全降级。"""
    local_issues = _local_hard_constraint_issues(chapter_content, target_words)
    parts = [
        f"【章节标题】\n{chapter_title}\n",
        f"【用户章节概要】\n{chapter_outline or '（未提供）'}\n",
        f"【其他硬性要求/禁写项】\n{requirements or '（未提供）'}\n",
        f"【前文、世界书与连续性契约】\n{continuity_context[:18000]}\n",
        f"【待监督正文】\n{chapter_content[:40000]}\n",
        "注意：字数由程序另行检查，你不需要估算字数。",
    ]
    if global_user_prompt.strip():
        parts.append(f"【用户全局偏好】\n{global_user_prompt}\n")
    if xp_mode:
        parts.append(Prompts.XP_MODE_SYSTEM)
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": SUPERVISION_AUDIT_SYSTEM},
                {"role": "user", "content": "\n".join(parts)},
            ],
            max_tokens=4096,
            temperature=0.1,
        )
        data = _parse_json(response.choices[0].message.content or "")
    except Exception:
        data = None
    return _normalize_audit(data, local_issues)


def repair_chapter(
    client,
    *,
    chapter_content: str,
    audit_result: SupervisionResult,
    chapter_title: str,
    chapter_outline: str,
    requirements: str,
    continuity_context: str,
    target_words: int,
    model: str,
    temperature: float = 0.4,
    global_user_prompt: str = "",
    xp_mode: bool = False,
) -> str:
    """按未通过项最小化修订完整正文。输出异常时返回空字符串。"""
    parts = [
        f"【章节标题】\n{chapter_title}\n",
        f"【用户章节概要】\n{chapter_outline or '（未提供）'}\n",
        f"【硬性要求/禁写项】\n{requirements or '（未提供）'}\n",
        f"【目标字数】\n不少于 {target_words} 字\n" if target_words > 0 else "",
        f"【连续性上下文】\n{continuity_context[:18000]}\n",
        "【未通过的监督项】\n"
        + json.dumps(audit_result.unresolved_issues, ensure_ascii=False, indent=2),
        f"【综合修订指令】\n{audit_result.repair_instruction}\n",
        f"【原章节正文】\n{chapter_content}\n",
    ]
    if global_user_prompt.strip():
        parts.append(f"【用户全局偏好】\n{global_user_prompt}\n")
    if xp_mode:
        parts.append(Prompts.XP_MODE_SYSTEM)
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": SUPERVISION_REPAIR_SYSTEM},
                {"role": "user", "content": "\n".join(parts)},
            ],
            max_tokens=min(max(len(chapter_content) * 2, target_words * 2, 8192), 32768),
            temperature=temperature,
        )
        repaired = (response.choices[0].message.content or "").strip()
    except Exception:
        return ""
    if len(repaired) < max(200, int(len(chapter_content.strip()) * 0.5)):
        return ""
    return repaired


def supervise_chapter(
    client_factory: Callable[[str], Any],
    *,
    chapter_content: str,
    chapter_title: str,
    chapter_outline: str = "",
    requirements: str = "",
    continuity_context: str = "",
    target_words: int = 0,
    model: str,
    temperature: float = 0.4,
    global_user_prompt: str = "",
    xp_mode: bool = False,
    max_repair_rounds: int = 2,
    progress: Callable[[str], None] | None = None,
    repair_change_callback: Callable[[int, str], None] | None = None,
) -> tuple[str, SupervisionResult]:
    """运行审计—定向修订—复检质量闸门。"""
    content = chapter_content
    final_result = SupervisionResult(status="warning", audit_failed=True)
    for round_index in range(max_repair_rounds + 1):
        if progress:
            progress("audit" if round_index == 0 else "reaudit")
        final_result = audit_chapter(
            client_factory("audit"),
            chapter_content=content,
            chapter_title=chapter_title,
            chapter_outline=chapter_outline,
            requirements=requirements,
            continuity_context=continuity_context,
            target_words=target_words,
            model=model,
            global_user_prompt=global_user_prompt,
            xp_mode=xp_mode,
        )
        final_result.repair_rounds = round_index
        if not final_result.needs_repair:
            final_result.status = "passed" if not final_result.audit_failed else "warning"
            return content, final_result
        if round_index >= max_repair_rounds or final_result.audit_failed:
            final_result.status = "warning"
            return content, final_result
        if progress:
            progress("repair")
        repaired = repair_chapter(
            client_factory("repair"),
            chapter_content=content,
            audit_result=final_result,
            chapter_title=chapter_title,
            chapter_outline=chapter_outline,
            requirements=requirements,
            continuity_context=continuity_context,
            target_words=target_words,
            model=model,
            temperature=temperature,
            global_user_prompt=global_user_prompt,
            xp_mode=xp_mode,
        )
        if not repaired:
            final_result.status = "warning"
            return content, final_result
        if repair_change_callback:
            try:
                repair_change_callback(
                    round_index + 1,
                    format_repair_diff_for_markdown(format_repair_diff(content, repaired)),
                )
            except Exception:
                pass
        content = repaired
    return content, final_result

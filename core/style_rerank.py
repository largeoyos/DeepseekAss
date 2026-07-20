"""Content locks and optional multi-candidate prose-style reranking."""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field

from core.style_profiles import ResolvedStyle, calculate_style_match_score, render_style_audit
from core.style_evaluation import evaluate_style_text


@dataclass
class ContentLockManifest:
    chapter_title: str = ""
    ordered_events: list[str] = field(default_factory=list)
    explicit_constraints: list[str] = field(default_factory=list)
    continuity_facts: list[str] = field(default_factory=list)
    ending_constraints: list[str] = field(default_factory=list)
    protected_terms: list[str] = field(default_factory=list)

    @property
    def active(self) -> bool:
        return any((
            self.ordered_events, self.explicit_constraints, self.continuity_facts,
            self.ending_constraints, self.protected_terms,
        ))

    def to_dict(self) -> dict:
        return asdict(self)

    def render(self) -> str:
        if not self.active:
            return ""
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)


def _unique(items: list[str], limit: int) -> list[str]:
    result: list[str] = []
    for item in items:
        value = re.sub(r"\s+", " ", str(item or "")).strip(" -—*#\t\r\n")
        if 2 <= len(value) <= 500 and value not in result:
            result.append(value)
        if len(result) >= limit:
            break
    return result


def _clauses(text: str) -> list[str]:
    return _unique(re.split(r"[\n；;。！？!?]+", str(text or "")), 80)


def build_content_lock(
    *, chapter_title: str, outline: str = "", requirements: str = "",
    continuity_context: str = "",
) -> ContentLockManifest:
    """Build a deterministic manifest without spending another model call."""
    outline_items = _clauses(outline)
    requirement_items = _clauses(requirements)
    context_lines = _unique(str(continuity_context or "").splitlines(), 120)
    hard_pattern = re.compile(r"必须|不得|不可|不能|务必|禁止|保持|不要|只允许|需要|应当")
    ending_pattern = re.compile(r"结尾|章末|最后|收束|悬念|尾声|落点")
    fact_pattern = re.compile(r"当前|事实|状态|关系|时间|地点|身份|设定|规则|契约|已经|仍然")
    terms_source = "\n".join([outline, requirements])
    protected_terms = re.findall(r"《([^》]{1,40})》|“([^”]{1,40})”|【([^】]{1,40})】", terms_source)
    flat_terms = [next((part for part in group if part), "") for group in protected_terms]
    flat_terms.extend(re.findall(r"\b[A-Z][A-Za-z0-9_-]{1,30}\b", terms_source))
    ending_constraints = [item for item in [*outline_items, *requirement_items] if ending_pattern.search(item)]
    explicit = [item for item in requirement_items if hard_pattern.search(item)]
    explicit.extend(item for item in outline_items if hard_pattern.search(item))
    facts = [line for line in context_lines if fact_pattern.search(line) and len(line) <= 500]
    return ContentLockManifest(
        chapter_title=chapter_title.strip(),
        ordered_events=_unique(outline_items, 18),
        explicit_constraints=_unique(explicit or requirement_items, 16),
        continuity_facts=_unique(facts, 24),
        ending_constraints=_unique(ending_constraints, 8),
        protected_terms=_unique(flat_terms, 20),
    )


def render_content_lock(manifest: ContentLockManifest) -> str:
    rendered = manifest.render()
    if not rendered:
        return ""
    return (
        "以下是从章节任务和连续性契约中确定性提取的内容锁。正文可以扩写过程，但不得改变事件顺序、"
        "人物动机、既定事实、对白意图或结尾状态，也不得为了贴合文风而偷换这些内容：\n" + rendered
    )


@dataclass
class CandidateScore:
    candidate_id: str
    local_style_score: float
    judge_style_score: float
    content_score: float
    naturalness_score: float
    total_score: float
    content_lock_violations: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass
class CandidateSelection:
    index: int
    content: str
    scores: list[CandidateScore]
    judge_used: bool = False
    error: str = ""

    def to_dict(self) -> dict:
        return {
            "selected_index": self.index,
            "judge_used": self.judge_used,
            "error": self.error,
            "scores": [asdict(item) for item in self.scores],
        }


def _sample_candidate(text: str, slice_chars: int = 2600) -> str:
    source = str(text or "")
    if len(source) <= slice_chars * 3:
        return source
    middle = max(0, len(source) // 2 - slice_chars // 2)
    return "\n\n[中段抽样]\n".join((
        source[:slice_chars], source[middle:middle + slice_chars], source[-slice_chars:],
    ))


def _parse_judge_response(raw: str) -> dict:
    value = re.sub(r"^```(?:json)?\s*|\s*```$", "", str(raw or "").strip(), flags=re.I)
    start, end = value.find("{"), value.rfind("}")
    if start >= 0 and end >= start:
        value = value[start:end + 1]
    data = json.loads(value)
    if not isinstance(data, dict):
        raise ValueError("竞稿评审未返回 JSON 对象")
    return data


def _bounded_score(value, default: float) -> float:
    try:
        return max(0.0, min(100.0, float(value)))
    except (TypeError, ValueError):
        return default


def select_best_style_candidate(
    client, *, candidates: list[str], resolved_style: ResolvedStyle,
    content_lock: ContentLockManifest, model: str, task_context: str = "",
) -> CandidateSelection:
    """Select by style 45%, content 35%, naturalness 20%; always has a local fallback."""
    if not candidates:
        raise ValueError("没有可供重排的候选正文")
    profile_metrics = resolved_style.profile.metrics if resolved_style.profile else {}
    local_style = [calculate_style_match_score(profile_metrics, item) for item in candidates]
    local_natural: list[float] = []
    for item in candidates:
        evaluation = evaluate_style_text(profile_metrics, item)
        local_natural.append(evaluation.anti_ai_score)

    judge_data: dict = {}
    judge_error = ""
    try:
        payload = {
            "task_context": task_context[:6000],
            "content_lock": content_lock.to_dict(),
            "style_audit": render_style_audit(resolved_style, task_context=task_context),
            "candidates": [
                {
                    "candidate_id": chr(65 + index),
                    "whole_text_style_fingerprint_score": local_style[index],
                    "sample": _sample_candidate(content),
                }
                for index, content in enumerate(candidates)
            ],
        }
        prompt = (
            "你是中文小说双候选盲审编辑。只能依据给定文风档案和内容锁评分，不得偏爱更华丽或更长的稿件。"
            "先检查内容锁：改变事件顺序、人物动机、事实、关系、对白意图或指定结尾都要扣分。"
            "再检查文风是否体现在具体用词、虚词、句法、标点、段落呼吸，而非只看题材。"
            "最后检查自然度，惩罚AI套话、机械排比、解释性旁白和重复反应。只返回严格JSON："
            "{candidates:[{candidate_id,style_score:0-100,content_score:0-100,naturalness_score:0-100,"
            "content_lock_violations:[string],notes:[string]}],winner_id:string}。\n\n"
            + json.dumps(payload, ensure_ascii=False)
        )
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=2400,
        )
        judge_data = _parse_judge_response(response.choices[0].message.content or "")
    except Exception as exc:
        judge_error = str(exc)

    judged = {
        str(item.get("candidate_id", "")): item
        for item in (judge_data.get("candidates") or []) if isinstance(item, dict)
    }
    scores: list[CandidateScore] = []
    for index, content in enumerate(candidates):
        candidate_id = chr(65 + index)
        item = judged.get(candidate_id, {})
        judge_style = _bounded_score(item.get("style_score"), local_style[index])
        content_score = _bounded_score(item.get("content_score"), 100.0)
        naturalness = _bounded_score(item.get("naturalness_score"), local_natural[index])
        violations = _unique(list(item.get("content_lock_violations") or []), 12)
        combined_style = local_style[index] * 0.55 + judge_style * 0.45
        total = combined_style * 0.45 + content_score * 0.35 + naturalness * 0.20
        total -= min(35.0, len(violations) * 12.0)
        scores.append(CandidateScore(
            candidate_id=candidate_id,
            local_style_score=round(local_style[index], 2),
            judge_style_score=round(judge_style, 2),
            content_score=round(content_score, 2),
            naturalness_score=round(naturalness, 2),
            total_score=round(max(0.0, total), 2),
            content_lock_violations=violations,
            notes=_unique(list(item.get("notes") or []), 8),
        ))
    winner = max(range(len(scores)), key=lambda index: scores[index].total_score)
    return CandidateSelection(
        index=winner,
        content=candidates[winner],
        scores=scores,
        judge_used=bool(judged),
        error=judge_error,
    )

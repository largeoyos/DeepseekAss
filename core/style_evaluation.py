"""Deterministic, explainable evaluation for Chinese prose-style profiles."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from core.style_profiles import StyleProfile, calculate_style_match_score, calculate_style_metrics


@dataclass
class MetricDrift:
    key: str
    label: str
    target: float
    actual: float
    delta: float
    normalized_deviation: float
    severity: str
    direction: str


@dataclass
class StyleEvaluationReport:
    profile_id: str = ""
    profile_name: str = ""
    sample_chars: int = 0
    style_match_score: float = 50.0
    anti_ai_score: float = 100.0
    ai_tic_density_per_10000: float = 0.0
    ai_tic_counts: dict[str, int] = field(default_factory=dict)
    metric_drifts: list[MetricDrift] = field(default_factory=list)
    priorities: list[str] = field(default_factory=list)
    status: str = "needs_calibration"
    target_metrics: dict[str, Any] = field(default_factory=dict)
    actual_metrics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    def render_text(self) -> str:
        status_label = {
            "excellent": "高度贴合",
            "good": "基本贴合",
            "needs_calibration": "需要校准",
        }.get(self.status, self.status)
        lines = [
            f"文风档案：{self.profile_name or '未命名'}",
            f"评测文本：{self.sample_chars} 字",
            f"综合状态：{status_label}",
            f"文风指纹匹配：{self.style_match_score:.1f} / 100",
            f"去 AI 痕迹：{self.anti_ai_score:.1f} / 100",
            f"重复性 AI 痕迹密度：{self.ai_tic_density_per_10000:.2f} / 万字",
        ]
        if self.ai_tic_counts:
            lines.append("AI 痕迹计数：" + "、".join(
                f"{key}={value}" for key, value in sorted(self.ai_tic_counts.items())
            ))
        if self.priorities:
            lines.extend(["", "优先调整：", *[f"- {item}" for item in self.priorities]])
        if self.metric_drifts:
            lines.extend(["", "主要量化偏差："])
            for item in self.metric_drifts[:16]:
                lines.append(
                    f"- [{item.severity}] {item.label}：目标 {item.target:.3g}，"
                    f"实际 {item.actual:.3g}（{item.direction}）"
                )
        return "\n".join(lines)


_SCALAR_DIMENSIONS = (
    ("sentence_length_avg", "平均句长", 8.0),
    ("sentence_length_median", "句长中位数", 7.0),
    ("sentence_length_std", "句长波动", 8.0),
    ("short_sentence_ratio", "短句比例", 0.12),
    ("long_sentence_ratio", "长句比例", 0.10),
    ("paragraph_length_avg", "平均段长", 60.0),
    ("paragraph_length_median", "段长中位数", 50.0),
    ("dialogue_ratio", "对白字符比例", 0.12),
    ("dialogue_paragraph_ratio", "对白段比例", 0.15),
    ("four_char_clause_ratio", "四字分句比例", 0.05),
)
_VECTOR_DIMENSIONS = (
    ("lexical_categories_per_1000", "词类", 1.8, 8),
    ("lexical_markers_per_1000", "虚词/连接词", 1.0, 12),
    ("punctuation_per_1000", "标点", 3.0, 8),
    ("sentence_openers", "起句方式", 0.06, 6),
)
_TIC_WEIGHTS = {
    "template_contrast": 2.5,
    "abstract_role": 2.0,
    "canned_reaction": 3.5,
    "abstract_elevation": 4.0,
    "explanatory_summary": 2.5,
    "mechanical_micro_reaction": 2.5,
}
_TIC_LABELS = {
    "template_contrast": "否定式对照/整齐关联句重复",
    "abstract_role": "抽象身份标签重复",
    "canned_reaction": "罐头式反应或氛围句重复",
    "abstract_elevation": "拔高和象征性总结重复",
    "explanatory_summary": "旁白替人物总结含义",
    "mechanical_micro_reaction": "机械微表情反应重复",
}


def _severity(normalized: float) -> str:
    if normalized >= 2.0:
        return "major"
    if normalized >= 1.0:
        return "minor"
    return "matched"


def _append_drift(
    result: list[MetricDrift], *, key: str, label: str,
    target: float, actual: float, tolerance: float,
) -> None:
    delta = actual - target
    normalized = abs(delta) / max(0.0001, tolerance)
    result.append(MetricDrift(
        key=key,
        label=label,
        target=round(target, 4),
        actual=round(actual, 4),
        delta=round(delta, 4),
        normalized_deviation=round(normalized, 3),
        severity=_severity(normalized),
        direction="偏高" if delta > 0 else ("偏低" if delta < 0 else "一致"),
    ))


def evaluate_style_text(
    profile_or_metrics: StyleProfile | dict, text: str, *, profile_name: str = "",
) -> StyleEvaluationReport:
    if isinstance(profile_or_metrics, StyleProfile):
        profile = profile_or_metrics
        target = dict(profile.metrics or {})
        profile_id = profile.profile_id
        name = profile.name
    else:
        target = dict(profile_or_metrics or {})
        profile_id = ""
        name = profile_name
    actual = calculate_style_metrics(text)
    drifts: list[MetricDrift] = []
    for key, label, tolerance in _SCALAR_DIMENSIONS:
        if key in target and key in actual:
            _append_drift(
                drifts, key=key, label=label,
                target=float(target.get(key) or 0), actual=float(actual.get(key) or 0),
                tolerance=tolerance,
            )
    for map_key, map_label, base_tolerance, limit in _VECTOR_DIMENSIONS:
        target_map = dict(target.get(map_key) or {})
        actual_map = dict(actual.get(map_key) or {})
        ordered_keys = sorted(target_map, key=lambda key: abs(float(target_map[key] or 0)), reverse=True)[:limit]
        for key in ordered_keys:
            target_value = float(target_map.get(key) or 0)
            actual_value = float(actual_map.get(key) or 0)
            tolerance = max(base_tolerance, abs(target_value) * 0.30)
            _append_drift(
                drifts, key=f"{map_key}.{key}", label=f"{map_label}“{key}”",
                target=target_value, actual=actual_value, tolerance=tolerance,
            )
    drifts.sort(key=lambda item: item.normalized_deviation, reverse=True)

    from utils.supervision import collect_style_tic_counts

    tic_counts = collect_style_tic_counts(text)
    weighted_excess = sum(
        max(0, count - 1) * _TIC_WEIGHTS.get(key, 2.0)
        for key, count in tic_counts.items()
    )
    hanzi_count = int(actual.get("hanzi_count") or 0)
    tic_density = weighted_excess * 10000 / max(5000, hanzi_count)
    anti_ai_score = max(35.0, 100.0 - min(65.0, tic_density * 2.0))
    style_score = calculate_style_match_score(target, actual)
    priorities = [
        f"{item.label}{item.direction}：从 {item.actual:.3g} 向目标 {item.target:.3g} 靠拢"
        for item in drifts if item.severity != "matched"
    ][:8]
    repeated_tics = [
        f"减少{_TIC_LABELS.get(key, key)}（当前 {count} 次）"
        for key, count in sorted(tic_counts.items(), key=lambda item: item[1], reverse=True)
        if count > 1
    ]
    priorities = [*repeated_tics, *priorities][:10]
    combined = style_score * 0.75 + anti_ai_score * 0.25
    status = "excellent" if combined >= 85 else ("good" if combined >= 72 else "needs_calibration")
    return StyleEvaluationReport(
        profile_id=profile_id,
        profile_name=name,
        sample_chars=len(text or ""),
        style_match_score=round(style_score, 2),
        anti_ai_score=round(anti_ai_score, 2),
        ai_tic_density_per_10000=round(tic_density, 3),
        ai_tic_counts=tic_counts,
        metric_drifts=drifts,
        priorities=priorities,
        status=status,
        target_metrics=target,
        actual_metrics=actual,
    )

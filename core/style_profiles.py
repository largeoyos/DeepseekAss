"""Reusable prose-style profiles, extraction and prompt rendering."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import statistics
import uuid
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Callable, Iterable


STYLE_PROFILE_SCHEMA_VERSION = 2
STYLE_STRENGTHS = ("reference", "standard", "strict")
STYLE_STRENGTH_LABELS = {
    "reference": "参考",
    "standard": "标准",
    "strict": "严格",
}
STYLE_ANCHOR_FACETS = (
    "general", "dialogue", "action", "psychology", "environment", "ending",
)


_LEXICAL_MARKERS = (
    "的", "地", "得", "了", "着", "过", "把", "被", "将", "让",
    "却", "倒", "便", "就", "才", "又", "仍", "还", "只", "也",
    "很", "更", "最", "太", "几乎", "仿佛", "似乎", "忽然", "突然",
    "于是", "然后", "不过", "但是", "然而", "因此", "所以",
    "呢", "吧", "啊", "吗", "么", "罢了",
)
_LEXICAL_CATEGORIES = {
    "结构助词": ("的", "地", "得"),
    "时体助词": ("了", "着", "过"),
    "处置被动": ("把", "被", "将", "让"),
    "转折连接": ("却", "不过", "但是", "然而", "可是"),
    "递进因果": ("于是", "然后", "因此", "所以", "而且", "甚至"),
    "程度副词": ("很", "更", "最", "太", "极", "颇", "十分"),
    "推测比拟": ("仿佛", "似乎", "好像", "宛如", "犹如"),
    "突发时间": ("忽然", "突然", "猛然", "骤然", "旋即"),
    "语气词": ("呢", "吧", "啊", "呀", "吗", "么", "罢了"),
    "第一人称": ("我", "我们", "咱", "咱们"),
    "第三人称": ("他", "她", "它", "他们", "她们", "它们"),
}


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return "\n".join(_text(item) for item in value if _text(item))
    if isinstance(value, dict):
        return "\n".join(f"{key}：{_text(item)}" for key, item in value.items() if _text(item))
    return str(value).strip()


def _string_list(value, *, limit: int = 40) -> list[str]:
    if isinstance(value, str):
        items = re.split(r"[\n；;]+", value)
    elif isinstance(value, list):
        items = value
    else:
        items = []
    result: list[str] = []
    for item in items:
        normalized = _text(item)
        if normalized and normalized not in result:
            result.append(normalized[:500])
        if len(result) >= limit:
            break
    return result


@dataclass
class StyleAnchor:
    anchor_id: str = field(default_factory=lambda: f"anchor_{uuid.uuid4().hex}")
    facet: str = "general"
    text: str = ""
    source_name: str = ""
    reason: str = ""


@dataclass
class StyleProfile:
    profile_id: str = field(default_factory=lambda: f"style_{uuid.uuid4().hex}")
    schema_version: int = STYLE_PROFILE_SCHEMA_VERSION
    revision: int = 1
    name: str = "未命名文风"
    description: str = ""
    source_kind: str = "file"
    source_names: list[str] = field(default_factory=list)
    sample_chars: int = 0
    chunk_count: int = 0
    extraction_model: str = ""
    confidence: str = "medium"
    narrative_person: str = ""
    viewpoint_distance: str = ""
    sentence_rhythm: str = ""
    dialogue_habits: str = ""
    diction: str = ""
    description_balance: str = ""
    imagery: str = ""
    emotion_expression: str = ""
    transitions: str = ""
    endings: str = ""
    stable_rules: list[str] = field(default_factory=list)
    scene_facets: dict[str, list[str]] = field(default_factory=dict)
    avoid_rules: list[str] = field(default_factory=list)
    metrics: dict[str, float | int | dict] = field(default_factory=dict)
    anchors: list[StyleAnchor] = field(default_factory=list)
    extraction_notes: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)

    @classmethod
    def from_dict(cls, data: dict) -> "StyleProfile":
        source = dict(data or {})
        valid = cls.__dataclass_fields__
        values = {key: value for key, value in source.items() if key in valid and key != "anchors"}
        values["anchors"] = [
            StyleAnchor(**{key: value for key, value in item.items() if key in StyleAnchor.__dataclass_fields__})
            for item in source.get("anchors", []) if isinstance(item, dict)
        ]
        profile = cls(**values)
        profile.stable_rules = _string_list(profile.stable_rules)
        profile.avoid_rules = _string_list(profile.avoid_rules)
        profile.scene_facets = {
            str(key): _string_list(value, limit=20)
            for key, value in dict(profile.scene_facets or {}).items()
        }
        if profile.confidence not in {"low", "medium", "high"}:
            profile.confidence = "medium"
        return profile

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class StyleSourceDocument:
    name: str
    text: str


@dataclass
class ResolvedStyle:
    profile: StyleProfile | None = None
    strength: str = "standard"

    @property
    def active(self) -> bool:
        return self.profile is not None


class StyleExtractionCancelled(RuntimeError):
    pass


class StyleProfileRepository:
    """Encrypted user-level profile repository rooted beside book folders."""

    def __init__(self, novel_manager) -> None:
        self.manager = novel_manager
        self.path = os.path.join(self.manager._bookshelf_root, "style_profiles.json")

    def _load(self) -> dict:
        data = self.manager._read_encrypted_json(self.path)
        if not isinstance(data, dict):
            data = {}
        return {
            "schema_version": int(data.get("schema_version", STYLE_PROFILE_SCHEMA_VERSION) or STYLE_PROFILE_SCHEMA_VERSION),
            "profiles": list(data.get("profiles") or []),
            "runs": dict(data.get("runs") or {}),
        }

    def _save(self, data: dict) -> None:
        data["schema_version"] = STYLE_PROFILE_SCHEMA_VERSION
        self.manager._write_encrypted_json_atomic(self.path, data)

    def list_profiles(self) -> list[StyleProfile]:
        profiles = [StyleProfile.from_dict(item) for item in self._load()["profiles"] if isinstance(item, dict)]
        return sorted(profiles, key=lambda item: (item.name.lower(), item.created_at))

    def get(self, profile_id: str) -> StyleProfile | None:
        return next((item for item in self.list_profiles() if item.profile_id == profile_id), None)

    def save(self, profile: StyleProfile) -> StyleProfile:
        profile.updated_at = _now()
        data = self._load()
        payload = profile.to_dict()
        for index, item in enumerate(data["profiles"]):
            if str(item.get("profile_id", "")) == profile.profile_id:
                data["profiles"][index] = payload
                break
        else:
            data["profiles"].append(payload)
        self._save(data)
        return profile

    def duplicate(self, profile_id: str, name: str = "") -> StyleProfile:
        source = self.get(profile_id)
        if source is None:
            raise KeyError("文风档案不存在")
        result = copy.deepcopy(source)
        result.profile_id = f"style_{uuid.uuid4().hex}"
        result.name = name.strip() or f"{source.name} - 副本"
        result.revision = 1
        result.created_at = result.updated_at = _now()
        for anchor in result.anchors:
            anchor.anchor_id = f"anchor_{uuid.uuid4().hex}"
        return self.save(result)

    def delete(self, profile_id: str) -> list[str]:
        affected: list[str] = []
        for title in self.manager.list_books():
            meta = self.manager.load_meta(title)
            if getattr(meta, "style_profile_id", "") == profile_id:
                affected.append(title)
                self.manager.save_meta(title, style_profile_id="")
        data = self._load()
        data["profiles"] = [item for item in data["profiles"] if str(item.get("profile_id", "")) != profile_id]
        self._save(data)
        return affected

    def save_checkpoint(self, run_id: str, payload: dict) -> None:
        data = self._load()
        data["runs"][run_id] = copy.deepcopy(payload)
        self._save(data)

    def load_checkpoint(self, run_id: str) -> dict:
        return copy.deepcopy(self._load()["runs"].get(run_id) or {})

    def clear_checkpoint(self, run_id: str) -> None:
        data = self._load()
        if run_id in data["runs"]:
            del data["runs"][run_id]
            self._save(data)


def split_style_text(text: str, *, target: int = 5000, minimum: int = 4000, maximum: int = 6000) -> list[str]:
    """Split without dropping characters, preferring chapter/paragraph boundaries."""
    source = str(text or "")
    if not source:
        return []
    chunks: list[str] = []
    start = 0
    while start < len(source):
        remaining = len(source) - start
        if remaining <= maximum:
            chunks.append(source[start:])
            break
        low = min(len(source), start + minimum)
        ideal = min(len(source), start + target)
        high = min(len(source), start + maximum)
        window = source[low:high]
        candidates: list[int] = []
        for pattern in (r"\n(?=(?:第.{1,12}[章节回卷部]|#{1,6}\s))", r"\n\s*\n", r"[。！？!?]\s*\n"):
            candidates.extend(low + match.end() for match in re.finditer(pattern, window))
        if candidates:
            cut = min(candidates, key=lambda value: abs(value - ideal))
        else:
            punctuation = [low + match.end() for match in re.finditer(r"[。！？!?；;]\s*", window)]
            cut = min(punctuation, key=lambda value: abs(value - ideal)) if punctuation else high
        if cut <= start:
            cut = high
        chunks.append(source[start:cut])
        start = cut
    return chunks


def _per_thousand(count: int | float, total: int) -> float:
    return round(float(count) * 1000 / max(1, total), 3)


def calculate_style_metrics(text: str) -> dict[str, float | int | dict]:
    source = str(text or "")
    sentences = [item.strip() for item in re.split(r"[。！？!?]+", source) if item.strip()]
    paragraphs = [item.strip() for item in re.split(r"\n\s*\n|\n", source) if item.strip()]
    hanzi_count = len(re.findall(r"[\u3400-\u9fff]", source))
    sentence_lengths = [len(re.findall(r"[\u3400-\u9fffA-Za-z0-9]", item)) for item in sentences]
    paragraph_lengths = [len(re.findall(r"[\u3400-\u9fffA-Za-z0-9]", item)) for item in paragraphs]
    dialogue_chars = sum(len(item) for item in re.findall(r"[“\"『「](.*?)[”\"』」]", source, re.S))
    dialogue_paragraphs = sum(
        1 for item in paragraphs if re.search(r"[“\"『「].+?[”\"』」]", item, re.S)
    )
    first_person = len(re.findall(r"我们|咱们|我", source))
    third_person = len(re.findall(r"他们|她们|他|她", source))
    punctuation = Counter(char for char in source if char in "，。！？；：、……—,.!?;:")
    marker_counts = {marker: source.count(marker) for marker in _LEXICAL_MARKERS}
    category_counts = {
        name: sum(source.count(marker) for marker in markers)
        for name, markers in _LEXICAL_CATEGORIES.items()
    }
    clauses = [
        re.sub(r"[^\u3400-\u9fff]", "", item)
        for item in re.split(r"[，。！？；：、,.!?;:]", source)
    ]
    clauses = [item for item in clauses if item]
    four_char_clauses = sum(1 for item in clauses if len(item) == 4)
    opener_counts = Counter()
    for sentence in sentences:
        cleaned = sentence.lstrip(" \t\r\n“\"『「（(")
        if re.match(r"(?:我|我们|咱们)", cleaned):
            opener_counts["第一人称"] += 1
        elif re.match(r"(?:他|她|它|他们|她们|它们)", cleaned):
            opener_counts["第三人称"] += 1
        elif re.match(r"(?:这时|此时|随后|接着|然后|忽然|突然|片刻后|第二天|次日)", cleaned):
            opener_counts["时间转场"] += 1
        elif sentence.startswith(("“", "\"", "『", "「")):
            opener_counts["对白起句"] += 1
    return {
        "total_chars": len(source),
        "hanzi_count": hanzi_count,
        "sentence_count": len(sentences),
        "sentence_length_avg": round(statistics.mean(sentence_lengths), 2) if sentence_lengths else 0,
        "sentence_length_median": round(statistics.median(sentence_lengths), 2) if sentence_lengths else 0,
        "sentence_length_std": round(statistics.pstdev(sentence_lengths), 2) if len(sentence_lengths) > 1 else 0,
        "short_sentence_ratio": round(sum(1 for item in sentence_lengths if item <= 12) / max(1, len(sentence_lengths)), 4),
        "long_sentence_ratio": round(sum(1 for item in sentence_lengths if item >= 35) / max(1, len(sentence_lengths)), 4),
        "paragraph_count": len(paragraphs),
        "paragraph_length_avg": round(statistics.mean(paragraph_lengths), 2) if paragraph_lengths else 0,
        "paragraph_length_median": round(statistics.median(paragraph_lengths), 2) if paragraph_lengths else 0,
        "dialogue_ratio": round(dialogue_chars / max(1, len(source)), 4),
        "dialogue_paragraph_ratio": round(dialogue_paragraphs / max(1, len(paragraphs)), 4),
        "first_person_hits": first_person,
        "third_person_hits": third_person,
        "punctuation": dict(punctuation.most_common(12)),
        "punctuation_per_1000": {
            mark: _per_thousand(count, hanzi_count)
            for mark, count in punctuation.most_common(16)
        },
        "lexical_markers_per_1000": {
            marker: _per_thousand(count, hanzi_count)
            for marker, count in marker_counts.items() if count
        },
        "lexical_categories_per_1000": {
            name: _per_thousand(count, hanzi_count)
            for name, count in category_counts.items()
        },
        "four_char_clause_ratio": round(four_char_clauses / max(1, len(clauses)), 4),
        "sentence_openers": {
            name: round(count / max(1, len(sentences)), 4)
            for name, count in opener_counts.items()
        },
    }


def render_lexical_fingerprint(metrics: dict) -> str:
    """Render a compact, observable Chinese style definition for prompting."""
    if not metrics:
        return ""
    parts: list[str] = []
    if metrics.get("sentence_length_median") or metrics.get("sentence_length_avg"):
        parts.append(
            f"句长均值/中位数 {float(metrics.get('sentence_length_avg') or 0):.1f}/"
            f"{float(metrics.get('sentence_length_median') or 0):.1f} 字，"
            f"短句 {float(metrics.get('short_sentence_ratio') or 0) * 100:.1f}%，"
            f"长句 {float(metrics.get('long_sentence_ratio') or 0) * 100:.1f}%"
        )
    if metrics.get("paragraph_length_median") or metrics.get("paragraph_length_avg"):
        parts.append(
            f"段长均值/中位数 {float(metrics.get('paragraph_length_avg') or 0):.1f}/"
            f"{float(metrics.get('paragraph_length_median') or 0):.1f} 字，对白字符约 "
            f"{float(metrics.get('dialogue_ratio') or 0) * 100:.1f}%"
        )
    categories = dict(metrics.get("lexical_categories_per_1000") or {})
    if categories:
        ordered = sorted(categories.items(), key=lambda item: float(item[1]), reverse=True)
        parts.append("每千汉字词类频率：" + "、".join(f"{key}{float(value):.1f}" for key, value in ordered))
    markers = dict(metrics.get("lexical_markers_per_1000") or {})
    if markers:
        ordered = sorted(markers.items(), key=lambda item: float(item[1]), reverse=True)[:14]
        parts.append("高辨识虚词/连接词（每千字）：" + "、".join(f"{key}{float(value):.1f}" for key, value in ordered))
    punctuation_rates = dict(metrics.get("punctuation_per_1000") or {})
    if punctuation_rates:
        ordered = sorted(punctuation_rates.items(), key=lambda item: float(item[1]), reverse=True)[:10]
        parts.append("标点频率（每千字）：" + "、".join(f"{key}{float(value):.1f}" for key, value in ordered))
    if metrics.get("four_char_clause_ratio") is not None:
        parts.append(f"四字分句约 {float(metrics.get('four_char_clause_ratio') or 0) * 100:.1f}%")
    return "\n".join(f"- {item}" for item in parts)


def _parse_json_response(raw: str) -> dict:
    value = str(raw or "").strip()
    value = re.sub(r"^```(?:json)?\s*", "", value, flags=re.I)
    value = re.sub(r"\s*```$", "", value)
    try:
        data = json.loads(value)
    except json.JSONDecodeError:
        start, end = value.find("{"), value.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("模型未返回 JSON")
        data = json.loads(value[start:end + 1])
    if not isinstance(data, dict):
        raise ValueError("模型返回的文风分析不是对象")
    return data


def _completion_text(response) -> str:
    return str(response.choices[0].message.content or "")


class StyleExtractionService:
    def __init__(self, client, repository: StyleProfileRepository | None = None) -> None:
        self.client = getattr(client, "raw_client", client)
        self.repository = repository

    def estimate_calls(self, documents: Iterable[StyleSourceDocument]) -> int:
        docs = list(documents)
        chunks = sum(len(split_style_text(item.text)) for item in docs)
        return chunks + max(1, len(docs))

    def extract_documents(
        self,
        documents: list[StyleSourceDocument],
        model: str,
        *,
        base_name: str = "导入文风",
        source_kind: str = "file",
        progress: Callable[[str, int, int], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
        run_id: str = "",
    ) -> list[StyleProfile]:
        docs = [StyleSourceDocument(item.name, str(item.text or "")) for item in documents if str(item.text or "").strip()]
        if not docs:
            raise ValueError("没有可用于提取文风的文本")
        profiles = [
            self._extract_one(
                document, model,
                name=base_name if len(docs) == 1 else os.path.splitext(document.name)[0],
                source_kind=source_kind,
                progress=progress,
                cancelled=cancelled,
                run_id=f"{run_id}:{index}" if run_id else "",
            )
            for index, document in enumerate(docs)
        ]
        groups: list[list[StyleProfile]] = []
        for profile in profiles:
            for group in groups:
                if style_profile_similarity(profile, group[0]) >= 0.62:
                    group.append(profile)
                    break
            else:
                groups.append([profile])
        merged = [self._merge_group(group, base_name, model, source_kind) for group in groups]
        if len(merged) > 1:
            for index, profile in enumerate(merged, 1):
                profile.name = f"{base_name} · 风格 {index}"
        merged.sort(key=lambda item: item.sample_chars, reverse=True)
        return merged

    def _extract_one(
        self, document: StyleSourceDocument, model: str, *, name: str, source_kind: str,
        progress, cancelled, run_id: str,
    ) -> StyleProfile:
        chunks = split_style_text(document.text)
        source_sha256 = hashlib.sha256(document.text.encode("utf-8")).hexdigest()
        checkpoint = self.repository.load_checkpoint(run_id) if self.repository and run_id else {}
        saved = dict(checkpoint.get("analyses") or {}) if (
            checkpoint.get("source_sha256") == source_sha256
        ) else {}
        analyses: list[dict] = []
        for index, chunk in enumerate(chunks):
            if cancelled and cancelled():
                raise StyleExtractionCancelled("文风提取已取消")
            if progress:
                progress(f"分析 {document.name} 第 {index + 1}/{len(chunks)} 块", index, len(chunks) + 1)
            cached = saved.get(str(index))
            if isinstance(cached, dict):
                analyses.append(cached)
                continue
            analysis = self._analyze_chunk(chunk, model)
            analyses.append(analysis)
            saved[str(index)] = analysis
            if self.repository and run_id:
                self.repository.save_checkpoint(run_id, {
                    "document": document.name,
                    "chunks": len(chunks),
                    "source_sha256": source_sha256,
                    "analyses": saved,
                    "updated_at": _now(),
                })
        if cancelled and cancelled():
            raise StyleExtractionCancelled("文风提取已取消")
        if progress:
            progress(f"聚合 {document.name} 的稳定文风", len(chunks), len(chunks) + 1)
        combined = self._aggregate(analyses, calculate_style_metrics(document.text), model)
        profile = self._profile_from_analysis(
            combined, name=name, source_kind=source_kind, source_names=[document.name],
            sample_chars=len(document.text), chunk_count=len(chunks), model=model,
            anchors=select_style_anchors([document]),
            metrics=calculate_style_metrics(document.text),
        )
        if self.repository and run_id:
            self.repository.clear_checkpoint(run_id)
        return profile

    def _analyze_chunk(self, chunk: str, model: str) -> dict:
        prompt = (
            "你是中文小说文风分析器。只分析可迁移的写作形式，不提取或复述人物名、地点名、剧情、设定和主题。"
            "返回严格 JSON，字段为 narrative_person, viewpoint_distance, sentence_rhythm, dialogue_habits, "
            "diction, description_balance, imagery, emotion_expression, transitions, endings, "
            "stable_rules:[string], scene_facets:{general:[string],dialogue:[string],action:[string],psychology:[string],environment:[string]}, "
            "avoid_rules:[string]。规则必须具体、可执行，不要文学评论。\n\n【样本文本】\n" + chunk
        )
        error: Exception | None = None
        for _attempt in range(3):
            try:
                response = self.client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.1,
                    max_tokens=2400,
                )
                return _parse_json_response(_completion_text(response))
            except Exception as exc:
                error = exc
        raise RuntimeError(f"文风分块分析失败：{error}")

    def _aggregate(self, analyses: list[dict], metrics: dict, model: str) -> dict:
        prompt = (
            "你是文风归纳编辑。下面是同一文本各分块的形式分析。合并共同且稳定的特征，冲突项不要强行确定。"
            "禁止加入人物、地点、剧情、世界观或主题。返回与输入相同字段的严格 JSON；stable_rules 保留 6-12 条，"
            "avoid_rules 保留 3-8 条，scene_facets 每类最多 6 条。\n\n"
            f"【全文统计】\n{json.dumps(metrics, ensure_ascii=False)}\n\n"
            f"【分块分析】\n{json.dumps(analyses, ensure_ascii=False)}"
        )
        error: Exception | None = None
        for _attempt in range(3):
            try:
                response = self.client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.1,
                    max_tokens=3200,
                )
                return _parse_json_response(_completion_text(response))
            except Exception as exc:
                error = exc
        raise RuntimeError(f"文风聚合失败：{error}")

    @staticmethod
    def _profile_from_analysis(data: dict, *, name: str, source_kind: str, source_names: list[str],
                               sample_chars: int, chunk_count: int, model: str,
                               anchors: list[StyleAnchor], metrics: dict | None = None) -> StyleProfile:
        profile = StyleProfile(
            name=name.strip() or "导入文风",
            source_kind=source_kind,
            source_names=source_names,
            sample_chars=sample_chars,
            chunk_count=chunk_count,
            extraction_model=model,
            confidence="high" if chunk_count >= 4 else ("medium" if chunk_count >= 2 else "low"),
            narrative_person=_text(data.get("narrative_person")),
            viewpoint_distance=_text(data.get("viewpoint_distance")),
            sentence_rhythm=_text(data.get("sentence_rhythm")),
            dialogue_habits=_text(data.get("dialogue_habits")),
            diction=_text(data.get("diction")),
            description_balance=_text(data.get("description_balance")),
            imagery=_text(data.get("imagery")),
            emotion_expression=_text(data.get("emotion_expression")),
            transitions=_text(data.get("transitions")),
            endings=_text(data.get("endings")),
            stable_rules=_string_list(data.get("stable_rules"), limit=12),
            scene_facets={
                str(key): _string_list(value, limit=6)
                for key, value in dict(data.get("scene_facets") or {}).items()
            },
            avoid_rules=_string_list(data.get("avoid_rules"), limit=8),
            metrics=metrics or {},
            anchors=anchors[:20],
        )
        return profile

    def _merge_group(self, group: list[StyleProfile], base_name: str, model: str, source_kind: str) -> StyleProfile:
        if len(group) == 1:
            profile = group[0]
            if base_name and profile.name == "导入文风":
                profile.name = base_name
            if not profile.metrics:
                profile.metrics = {}
            return profile
        first = copy.deepcopy(group[0])
        first.profile_id = f"style_{uuid.uuid4().hex}"
        first.name = base_name or first.name
        first.source_kind = source_kind
        first.source_names = [name for item in group for name in item.source_names]
        first.sample_chars = sum(item.sample_chars for item in group)
        first.chunk_count = sum(item.chunk_count for item in group)
        first.extraction_model = model
        first.confidence = "high" if first.chunk_count >= 4 else "medium"
        first.stable_rules = _dedup_strings(item for profile in group for item in profile.stable_rules)[:12]
        first.avoid_rules = _dedup_strings(item for profile in group for item in profile.avoid_rules)[:8]
        facets: dict[str, list[str]] = {}
        for profile in group:
            for key, rules in profile.scene_facets.items():
                facets[key] = _dedup_strings([*facets.get(key, []), *rules])[:6]
        first.scene_facets = facets
        first.anchors = _dedup_anchors(anchor for profile in group for anchor in profile.anchors)[:20]
        first.metrics = _merge_profile_metrics(group)
        first.created_at = first.updated_at = _now()
        return first


def _dedup_strings(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        normalized = _text(value)
        if normalized and not any(_char_ngram_similarity(normalized, old) > 0.82 for old in result):
            result.append(normalized)
    return result


def _dedup_anchors(values: Iterable[StyleAnchor]) -> list[StyleAnchor]:
    result: list[StyleAnchor] = []
    for value in values:
        if value.text and not any(_char_ngram_similarity(value.text, old.text) > 0.75 for old in result):
            result.append(copy.deepcopy(value))
    return result


def _char_ngram_similarity(left: str, right: str, size: int = 2) -> float:
    def grams(value: str) -> set[str]:
        cleaned = re.sub(r"\s+", "", value)
        return {cleaned[index:index + size] for index in range(max(0, len(cleaned) - size + 1))}
    a, b = grams(left), grams(right)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _merge_profile_metrics(profiles: list[StyleProfile]) -> dict:
    """Merge whole-corpus metrics instead of inheriting the first document."""
    weighted = [(item.metrics or {}, max(1, int(item.sample_chars or 0))) for item in profiles]
    additive = {
        "total_chars", "hanzi_count", "sentence_count", "paragraph_count",
        "first_person_hits", "third_person_hits",
    }
    zero_filled_maps = {
        "punctuation_per_1000", "lexical_markers_per_1000",
        "lexical_categories_per_1000", "sentence_openers",
    }

    def merge_maps(items: list[tuple[dict, int]], path: tuple[str, ...] = ()) -> dict:
        result: dict = {}
        keys = {key for mapping, _weight in items for key in mapping}
        for key in keys:
            if path and path[-1] in zero_filled_maps:
                values = [(mapping.get(key, 0), weight) for mapping, weight in items]
            else:
                values = [(mapping[key], weight) for mapping, weight in items if key in mapping]
            if not values:
                continue
            if all(isinstance(value, dict) for value, _weight in values):
                result[key] = merge_maps([(value, weight) for value, weight in values], (*path, key))
                continue
            numeric = [(float(value), weight) for value, weight in values if isinstance(value, (int, float))]
            if len(numeric) != len(values):
                continue
            if key in additive or (path and path[-1] == "punctuation"):
                result[key] = round(sum(value for value, _weight in numeric), 3)
            else:
                total_weight = sum(weight for _value, weight in numeric)
                result[key] = round(sum(value * weight for value, weight in numeric) / max(1, total_weight), 4)
        return result

    return merge_maps(weighted)


def calculate_style_match_score(profile_metrics: dict, text_or_metrics: str | dict) -> float:
    """Return a deterministic 0-100 style-fingerprint similarity score."""
    target = profile_metrics or {}
    actual = calculate_style_metrics(text_or_metrics) if isinstance(text_or_metrics, str) else (text_or_metrics or {})
    scores: list[tuple[float, float]] = []

    def add_scalar(key: str, scale: float, weight: float = 1.0) -> None:
        if key not in target or key not in actual:
            return
        left = float(target.get(key) or 0)
        right = float(actual.get(key) or 0)
        scores.append((max(0.0, 1.0 - abs(left - right) / max(scale, 0.0001)), weight))

    for key, scale, weight in (
        ("sentence_length_avg", 24, 1.2), ("sentence_length_median", 20, 1.2),
        ("sentence_length_std", 20, 0.8), ("short_sentence_ratio", 0.45, 1.0),
        ("long_sentence_ratio", 0.35, 1.0), ("paragraph_length_avg", 180, 0.8),
        ("paragraph_length_median", 150, 0.8), ("dialogue_ratio", 0.45, 1.1),
        ("dialogue_paragraph_ratio", 0.55, 0.8), ("four_char_clause_ratio", 0.22, 0.7),
    ):
        add_scalar(key, scale, weight)

    def add_vector(key: str, base_scale: float, weight: float) -> None:
        left_map = dict(target.get(key) or {})
        right_map = dict(actual.get(key) or {})
        for marker, left_value in left_map.items():
            left = float(left_value or 0)
            right = float(right_map.get(marker, 0) or 0)
            scale = max(base_scale, abs(left) * 1.5)
            scores.append((max(0.0, 1.0 - abs(left - right) / scale), weight))

    add_vector("lexical_categories_per_1000", 3.0, 0.42)
    add_vector("lexical_markers_per_1000", 1.5, 0.18)
    add_vector("punctuation_per_1000", 4.0, 0.2)
    add_vector("sentence_openers", 0.08, 0.28)
    if not scores:
        return 50.0
    weighted_score = sum(score * weight for score, weight in scores) / sum(weight for _score, weight in scores)
    return round(weighted_score * 100, 2)


def style_profile_similarity(left: StyleProfile, right: StyleProfile) -> float:
    fingerprint = calculate_style_match_score(left.metrics or {}, right.metrics or {}) / 100
    form_left = "\n".join([left.narrative_person, left.viewpoint_distance, left.sentence_rhythm, left.dialogue_habits, left.diction])
    form_right = "\n".join([right.narrative_person, right.viewpoint_distance, right.sentence_rhythm, right.dialogue_habits, right.diction])
    semantic = _char_ngram_similarity(form_left, form_right)
    return round(fingerprint * 0.6 + semantic * 0.4, 4)


def _anchor_facet(text: str, *, is_ending: bool = False) -> str:
    if is_ending:
        return "ending"
    dialogue = len(re.findall(r"[“\"『「].+?[”\"』」]", text, re.S))
    if dialogue >= 2:
        return "dialogue"
    if re.search(r"冲|撞|挥|砍|跑|追|扑|躲|抓|踢|打|杀|剑|枪", text):
        return "action"
    if re.search(r"想|意识到|记得|明白|犹豫|后悔|觉得|心里", text):
        return "psychology"
    if re.search(r"天空|街道|房间|风|雨|雪|光|影|山|河|夜|清晨", text):
        return "environment"
    return "general"


def select_style_anchors(documents: list[StyleSourceDocument]) -> list[StyleAnchor]:
    candidates: list[StyleAnchor] = []
    for document in documents:
        raw_paragraphs = [item.strip() for item in re.split(r"\n\s*\n|\n", document.text) if item.strip()]
        passages: list[str] = []
        buffer = ""
        for paragraph in raw_paragraphs:
            if len(paragraph) > 700:
                if buffer:
                    passages.append(buffer)
                    buffer = ""
                passages.extend(split_style_text(paragraph, target=420, minimum=260, maximum=600))
                continue
            combined = "\n".join(filter(None, [buffer, paragraph]))
            if len(combined) <= 700:
                buffer = combined
            else:
                if buffer:
                    passages.append(buffer)
                buffer = paragraph
            if len(buffer) >= 240:
                passages.append(buffer)
                buffer = ""
        if buffer:
            passages.append(buffer)
        passages = [item.strip() for item in passages if len(item.strip()) >= 120]
        if len(passages) < 12 and len(document.text) >= 1200:
            step = max(1, (len(document.text) - 500) // 11)
            passages.extend(document.text[index * step:index * step + 500].strip() for index in range(12))
        for index, passage in enumerate(passages):
            text = passage[:500]
            candidates.append(StyleAnchor(
                facet=_anchor_facet(text, is_ending=index == len(passages) - 1),
                text=text, source_name=document.name,
                reason="保留句法、节奏和叙述组织方式，不复用其中事实",
            ))
    chosen: list[StyleAnchor] = []
    for facet in STYLE_ANCHOR_FACETS:
        for item in (candidate for candidate in candidates if candidate.facet == facet):
            if not any(_char_ngram_similarity(item.text, old.text) > 0.78 for old in chosen):
                chosen.append(item)
            if sum(1 for old in chosen if old.facet == facet) >= 3:
                break
    for item in candidates:
        if len(chosen) >= 20:
            break
        if not any(_char_ngram_similarity(item.text, old.text) > 0.78 for old in chosen):
            chosen.append(item)
    if len(chosen) < 12:
        for item in candidates:
            if item not in chosen and not any(item.text == old.text for old in chosen):
                chosen.append(item)
                if len(chosen) >= 12:
                    break
    return chosen[:20]


def resolve_style(novel_manager, title: str, *, profile_id: str = "follow_book",
                  strength: str = "follow_book") -> ResolvedStyle:
    meta = novel_manager.load_meta(title)
    selected_id = getattr(meta, "style_profile_id", "") if profile_id in {"", "follow_book"} else profile_id
    selected_strength = getattr(meta, "style_strength", "standard") if strength in {"", "follow_book"} else strength
    if selected_strength not in STYLE_STRENGTHS:
        selected_strength = "standard"
    profile = StyleProfileRepository(novel_manager).get(selected_id) if selected_id else None
    return ResolvedStyle(profile=profile, strength=selected_strength)


def _select_runtime_anchors_legacy(profile: StyleProfile, task_context: str, count: int) -> list[StyleAnchor]:
    context = str(task_context or "")
    preferred: list[str] = []
    for facet, pattern in (
        ("dialogue", r"对话|交谈|争吵|谈判|聊天"),
        ("action", r"战斗|追逐|动作|打斗|逃跑|冲突"),
        ("psychology", r"心理|回忆|犹豫|反思|内心"),
        ("environment", r"环境|风景|场景|天气|氛围"),
        ("ending", r"结尾|收束|尾声|悬念|章末"),
    ):
        if re.search(pattern, context):
            preferred.append(facet)

    buckets: dict[str, list[StyleAnchor]] = {facet: [] for facet in STYLE_ANCHOR_FACETS}
    for item in profile.anchors:
        facet = item.facet if item.facet in buckets else "general"
        buckets[facet].append(item)
    chosen: list[StyleAnchor] = []

    def add(item: StyleAnchor) -> None:
        if len(chosen) >= count:
            return
        if item in chosen:
            return
        if any(_char_ngram_similarity(item.text, old.text) > 0.88 for old in chosen):
            return
        chosen.append(item)

    for facet in preferred:
        for item in buckets.get(facet, []):
            add(item)
            if sum(1 for old in chosen if old.facet == facet) >= 2:
                break

    for item in buckets["general"]:
        add(item)
        if any(old.facet == "general" for old in chosen):
            break

    if count >= 7:
        for item in buckets["ending"]:
            add(item)
            if any(old.facet == "ending" for old in chosen):
                break

    order = list(dict.fromkeys([*preferred, "general", *STYLE_ANCHOR_FACETS]))
    positions = {facet: 0 for facet in order}
    while len(chosen) < count:
        added = False
        for facet in order:
            items = buckets.get(facet, [])
            while positions[facet] < len(items):
                item = items[positions[facet]]
                positions[facet] += 1
                before = len(chosen)
                add(item)
                if len(chosen) > before:
                    added = True
                    break
            if len(chosen) >= count:
                break
        if not added:
            break
    return chosen[:count]


def _select_runtime_anchors(profile: StyleProfile, task_context: str, count: int) -> list[StyleAnchor]:
    """Select a small, facet-diverse set; topical similarity must not collapse style coverage."""
    context = str(task_context or "")
    preferred: list[str] = []
    for facet, pattern in (
        ("dialogue", r"对话|交谈|争吵|谈判|聊天"),
        ("action", r"战斗|追逐|动作|打斗|逃跑|冲突"),
        ("psychology", r"心理|回忆|犹豫|反思|内心"),
        ("environment", r"环境|风景|场景|天气|氛围"),
        ("ending", r"结尾|收束|尾声|悬念|章末"),
    ):
        if re.search(pattern, context):
            preferred.append(facet)

    buckets: dict[str, list[StyleAnchor]] = {facet: [] for facet in STYLE_ANCHOR_FACETS}
    for item in profile.anchors:
        buckets[item.facet if item.facet in buckets else "general"].append(item)
    chosen: list[StyleAnchor] = []

    def add(item: StyleAnchor) -> bool:
        if len(chosen) >= count or item in chosen:
            return False
        if any(_char_ngram_similarity(item.text, old.text) > 0.88 for old in chosen):
            return False
        chosen.append(item)
        return True

    # First pass: cover task-relevant facets, then general/ending, then every
    # remaining prose facet once. This deliberately resists topic-only retrieval.
    coverage_order = list(dict.fromkeys([
        *preferred, "general", "ending", "dialogue", "action", "psychology", "environment",
    ]))
    for facet in coverage_order:
        for item in buckets.get(facet, []):
            if add(item):
                break
        if len(chosen) >= count:
            return chosen

    # Second pass may add another task-relevant example, but only after broad
    # style coverage has been attempted.
    fill_order = list(dict.fromkeys([*preferred, *STYLE_ANCHOR_FACETS]))
    positions = {facet: 0 for facet in fill_order}
    while len(chosen) < count:
        changed = False
        for facet in fill_order:
            items = buckets.get(facet, [])
            while positions[facet] < len(items):
                item = items[positions[facet]]
                positions[facet] += 1
                if add(item):
                    changed = True
                    break
            if len(chosen) >= count:
                break
        if not changed:
            break
    return chosen[:count]


def render_style_prompt(resolved: ResolvedStyle, *, task_context: str = "") -> str:
    profile = resolved.profile
    if profile is None:
        return ""
    strength = resolved.strength if resolved.strength in STYLE_STRENGTHS else "standard"
    anchor_count = {"reference": 3, "standard": 5, "strict": 6}[strength]
    rules = profile.stable_rules[:4] if strength == "reference" else profile.stable_rules[:12]
    parts = [
        f"【指定文风档案：{profile.name}｜{STYLE_STRENGTH_LABELS[strength]}】",
        "中文词法指纹与下列核心例文共同构成文风的首要依据；抽象规则只用于解释可观察写法。",
        "重点贴近例文可观察到的用词范围、虚实词比例、动词和形容词选择、句子长度、分句连接、标点停顿、段落推进、对白衔接、意象密度与段尾落点。",
        "不得复用例文中的人物、地点、剧情、设定、专名或连续原句；模仿的是语言组织方式，不是例文事实。",
        "不得用模型惯用套话替代例文没有的表达，也不要擅自添加例文未体现的空泛比喻、情绪总结、整齐排比或拔高式段尾。",
        "执行优先级：安全约束和用户明确要求 > 世界书与连续性 > 本文风档案 > 题材/基调 > 通用 humanizer。",
        "若与轻快、严肃、文艺等粗粒度写作基调冲突，以本文风档案为准。",
        "若通用 humanizer 与档案的具体写法冲突，以档案为准；防注水、防重复和禁止机械复述仍然有效。",
    ]
    fingerprint = render_lexical_fingerprint(profile.metrics or {})
    if fingerprint:
        parts.append("【中文词法指纹（先校准用词、虚词和停顿）】\n" + fingerprint)
        parts.append("这些数值是整章分布目标，不要求逐句机械凑数；明显偏离时优先校准高频虚词、连接词和标点习惯。")
    anchors = _select_runtime_anchors(profile, task_context, anchor_count)
    if anchors:
        parts.append("【核心模仿例文（首要依据；只学语言，不续接内容）】")
        parts.extend(f"例文{i}（{item.facet}）：\n{item.text}" for i, item in enumerate(anchors, 1))
        parts.append("先在内部归纳以上例文反复出现的词语层级、句法骨架和段落呼吸，再以同一语言习惯写新内容；不要输出归纳过程。")
    dimensions = [
        ("叙事人称与视角", "；".join(filter(None, [profile.narrative_person, profile.viewpoint_distance]))),
        ("句段节奏", profile.sentence_rhythm),
        ("对白习惯", profile.dialogue_habits),
        ("措辞", profile.diction),
        ("描写比例", profile.description_balance),
        ("意象", profile.imagery),
        ("情绪表达", profile.emotion_expression),
        ("转场与收束", "；".join(filter(None, [profile.transitions, profile.endings]))),
    ]
    for label, value in dimensions:
        if value and (strength != "reference" or label in {"叙事人称与视角", "句段节奏", "措辞"}):
            parts.append(f"- {label}：{value}")
    if rules:
        parts.append("【稳定写法】\n" + "\n".join(f"- {item}" for item in rules))
    if strength == "strict":
        facet_rules = _dedup_strings(item for values in profile.scene_facets.values() for item in values)
        if facet_rules:
            parts.append("【场景写法】\n" + "\n".join(f"- {item}" for item in facet_rules[:12]))
    if strength != "reference" and profile.avoid_rules:
        parts.append("【避免】\n" + "\n".join(f"- {item}" for item in profile.avoid_rules[:8]))
    if strength == "reference":
        parts.append("以核心例文为柔性校准，只修正明显脱离例文语言习惯的段落。")
    elif strength == "strict":
        parts.append("严格以核心例文而非通用写作套路为最终判据，持续保持其用词、造句、停顿、段落推进和收束习惯。")
        parts.append(
            "写作前在内部逐篇校准例文；每写完二至三段就对照最相近的例文检查一次，改掉模板化抒情和不属于该例文语域的词句。只输出正文，不输出检查过程。"
        )
    else:
        parts.append("正文应首先像核心例文本身，再满足抽象规则；出现选择时优先采用例文中已有依据的措辞和句法习惯。")
    return "\n\n".join(parts)

def render_style_audit(resolved: ResolvedStyle, *, task_context: str = "") -> str:
    if not resolved.active:
        return ""
    profile = resolved.profile
    assert profile is not None
    parts = [
        f"检查正文是否符合文风档案“{profile.name}”（强度：{STYLE_STRENGTH_LABELS[resolved.strength]}），重点检查"
        "叙事人称与视角距离、句段节奏、对白组织、措辞层次、描写比例、情绪表达和段尾收束。"
        "只把重复或系统性偏离列为 style_mismatch；参考强度仅报告严重偏离，标准强度报告明显系统性偏离，"
        "严格强度报告严重偏离及重复出现的轻微偏离。范例内容本身不属于必须复现的事实。"
    ]

    dimensions = [
        profile.narrative_person, profile.viewpoint_distance, profile.sentence_rhythm,
        profile.dialogue_habits, profile.diction, profile.description_balance,
        profile.imagery, profile.emotion_expression, profile.transitions, profile.endings,
    ]
    if any(dimensions):
        parts.append("档案核心特征：" + "；".join(item for item in dimensions if item))
    if profile.stable_rules:
        parts.append("必须对照的稳定规则：" + "；".join(profile.stable_rules[:12]))
    if profile.avoid_rules:
        parts.append("必须避免：" + "；".join(profile.avoid_rules[:8]))
    if resolved.strength == "strict" and profile.metrics:
        fingerprint = render_lexical_fingerprint(profile.metrics)
        if fingerprint:
            parts.append("中文词法指纹对照：\n" + fingerprint)
    audit_count = {"reference": 1, "standard": 3, "strict": 6}.get(resolved.strength, 3)
    anchors = _select_runtime_anchors(profile, task_context, audit_count)
    if anchors:
        parts.append(
            "审查时以下列例文的实际用词、句法、停顿和段落组织为主要对照；规则只作辅助，"
            "不得要求正文复现例文事实或原句：\n"
            + "\n\n".join(f"对照例文{i}（{item.facet}）：\n{item.text}" for i, item in enumerate(anchors, 1))
        )
    return "\n".join(parts)

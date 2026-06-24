"""Progressive, inspectable context assembly for long-form generation."""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field, is_dataclass


DEFAULT_BUDGETS = {
    "preferences": 1600,
    "author_plan": 2400,
    "recent_summary": 6000,
    "continuity": 6000,
    "world_resident": 10000,
    "world_auto": 10000,
    "world_index": 6000,
    "manual": 8000,
    "history": 4000,
    "retrieval": 6000,
}


@dataclass
class ContextSection:
    source: str
    title: str
    content: str
    reason: str
    budget: int
    original_chars: int = 0
    omitted_chars: int = 0


@dataclass
class ContextReport:
    sections: list[ContextSection] = field(default_factory=list)
    total_chars: int = 0
    omitted_chars: int = 0

    def render(self) -> str:
        return "\n\n".join(
            f"【{section.title}】\n{section.content}"
            for section in self.sections if section.content.strip()
        )

    def preview(self) -> str:
        lines = [
            f"上下文总计 {self.total_chars} 字，省略 {self.omitted_chars} 字。",
        ]
        for section in self.sections:
            suffix = f"，截断 {section.omitted_chars} 字" if section.omitted_chars else ""
            lines.append(
                f"- {section.title}: {len(section.content)} 字{suffix}；"
                f"来源={section.source}；原因={section.reason}"
            )
        return "\n".join(lines)


def _clip(text: str, budget: int) -> tuple[str, int]:
    text = str(text or "").strip()
    if budget <= 0 or len(text) <= budget:
        return text, 0
    marker = f"\n\n（本节已按 {budget} 字预算截断；省略 {len(text) - budget} 字。）"
    usable = max(0, budget - len(marker))
    return text[:usable].rstrip() + marker, len(text) - usable


def _plain(value) -> dict:
    if is_dataclass(value):
        return asdict(value)
    return dict(value) if isinstance(value, dict) else {}


def _entity_text(kind: str, item) -> str:
    data = _plain(item)
    name = str(data.get("name") or data.get("hint") or data.get("event") or "").strip()
    ignored = {
        "id", "name", "hidden", "source_refs", "fact_sources",
        "chapter_snapshots", "chapter_world_entries", "diagnostics",
    }
    lines = [f"### {name or kind}"]
    for key, value in data.items():
        if key in ignored or value in ("", None, [], {}, False, 0):
            continue
        if isinstance(value, (dict, list)):
            rendered = json.dumps(value, ensure_ascii=False)
        else:
            rendered = str(value)
        lines.append(f"- {key}: {rendered}")
    return "\n".join(lines)


def _world_entities(bible) -> list[tuple[str, str, str, object]]:
    groups = [
        ("character", "characters"),
        ("location", "locations"),
        ("plot_thread", "active_plot_threads"),
        ("rule", "world_rules"),
        ("timeline", "timeline"),
    ]
    result: list[tuple[str, str, str, object]] = []
    for kind, attr in groups:
        for index, item in enumerate(getattr(bible, attr, []) or []):
            data = _plain(item)
            entity_id = str(data.get("id") or f"{kind}_{index}")
            name = str(data.get("name") or data.get("event") or entity_id)
            if data.get("hidden"):
                continue
            result.append((entity_id, kind, name, item))
    for index, rule in enumerate(getattr(bible, "rules", []) or []):
        entity_id = f"legacy_rule_{index}"
        result.append((entity_id, "rule", str(rule)[:40], {"id": entity_id, "name": "规则", "content": rule}))
    for index, item in enumerate(getattr(bible, "global_foreshadowing", []) or []):
        entity_id = str(item.get("id") or f"foreshadow_{index}")
        result.append((entity_id, "foreshadowing", str(item.get("hint") or entity_id), item))
    return result


class ContextAssembler:
    def __init__(self, novel_manager, budgets: dict[str, int] | None = None) -> None:
        self.novel_manager = novel_manager
        self.budgets = {**DEFAULT_BUDGETS, **(budgets or {})}

    def assemble_chapter(
        self,
        title: str,
        chapter_num: int,
        chapter_title: str,
        plot_content: str = "",
        *,
        global_prompt: str = "",
        manual_entity_ids: list[str] | None = None,
        max_recent: int = 3,
        client=None,
        model: str = "deepseek-v4-flash",
    ) -> ContextReport:
        report = ContextReport()
        query = "\n".join([chapter_title, plot_content, global_prompt])

        self._add(report, "preferences", "用户偏好", global_prompt, "用户全局偏好")
        try:
            self._add(
                report, "author_plan", "作者规划",
                self.novel_manager.build_author_planning_prompt(title),
                "当前小说作者规划",
            )
        except Exception:
            pass
        try:
            summary = self.novel_manager.load_smart_summary(
                title,
                client=client,
                next_chapter_num=chapter_num,
                max_recent=max_recent,
                model=model,
                global_user_prompt=global_prompt,
            )
            self._add(report, "recent_summary", "前情提要", summary, "活跃章节路径摘要")
        except Exception:
            pass
        try:
            contract = self.novel_manager.build_continuity_contract(
                title, chapter_num, chapter_title, plot_content
            )
            self._add(report, "continuity", "本章连贯性契约", contract, "角色状态、剧情线和伏笔")
        except Exception:
            pass

        self._add_world_sections(
            report,
            title,
            query=query,
            manual_entity_ids=set(manual_entity_ids or []),
        )
        try:
            backend = self.novel_manager.retrieval_backend()
            retrieved = backend.search(
                title,
                query,
                filters={"manual_entity_ids": list(manual_entity_ids or [])},
                limit=8,
            )
            retrieval_text = "\n\n".join(
                f"[{item.source_type}:{item.source_id}] {item.reason}\n{item.content}"
                for item in retrieved
            )
            self._add(
                report,
                "retrieval",
                "混合检索补充",
                retrieval_text,
                "关键词、实体与语义检索融合结果",
            )
        except Exception:
            pass
        try:
            history = self.novel_manager.build_history_summary(title, exclude_chapter=chapter_num)
            if history not in ("暂无历史记录。", "暂无历史记录（排除当前章节后）。"):
                self._add(report, "history", "历史生成记录", history, "保持既有风格和参数")
        except Exception:
            pass
        return report

    def assemble_continuation(
        self,
        title: str,
        chapter_num: int,
        chapter_title: str,
        source_text: str,
        requirement: str,
        plot: str,
        **kwargs,
    ) -> ContextReport:
        report = self.assemble_chapter(
            title,
            chapter_num,
            chapter_title,
            "\n".join([plot, requirement, source_text[-2000:]]),
            max_recent=10,
            **kwargs,
        )
        self._add(
            report,
            "manual",
            "原文内容",
            source_text[-self.budgets["manual"]:],
            "用户选择的续写源文本",
            insert_at=0,
        )
        return report

    def _add_world_sections(
        self,
        report: ContextReport,
        title: str,
        *,
        query: str,
        manual_entity_ids: set[str],
    ) -> None:
        try:
            bible = self.novel_manager.load_world_bible(title)
            workspace = self.novel_manager.get_workspace(title)
            policies = workspace.load_context_policies()
        except Exception:
            return
        resident: list[str] = []
        automatic: list[str] = []
        manual: list[str] = []
        index: list[str] = []
        normalized_query = re.sub(r"\s+", "", query).lower()
        for entity_id, kind, name, item in _world_entities(bible):
            policy = {
                "enabled": True,
                "load_mode": "auto",
                "brief_description": "",
                "keywords": [],
                "priority": 50,
                **dict(policies.get(entity_id) or {}),
            }
            if not policy["enabled"]:
                continue
            text = _entity_text(kind, item)
            brief = str(policy.get("brief_description") or "").strip()
            keywords = [str(value).strip() for value in policy.get("keywords", []) if str(value).strip()]
            signals = [name, brief, *keywords]
            matched = any(
                re.sub(r"\s+", "", signal).lower() in normalized_query
                for signal in signals if len(re.sub(r"\s+", "", signal)) >= 2
            )
            mode = str(policy.get("load_mode") or "auto")
            if entity_id in manual_entity_ids:
                manual.append(text)
            elif mode == "resident":
                resident.append(text)
            elif mode == "auto" and matched:
                automatic.append(text)
            index.append(
                f"- {entity_id} | {kind} | {name}"
                + (f" | {brief}" if brief else "")
                + f" | 加载={mode}"
            )
        self._add(report, "world_resident", "世界书常驻条目", "\n\n".join(resident), "策略设为 resident")
        self._add(report, "world_auto", "世界书自动命中", "\n\n".join(automatic), "名称、简介或关键词命中本章")
        self._add(report, "manual", "世界书手动引用", "\n\n".join(manual), "用户显式选择")
        self._add(report, "world_index", "世界书索引", "\n".join(index), "未展开条目的检索索引")

    def _add(
        self,
        report: ContextReport,
        source: str,
        title: str,
        content: str,
        reason: str,
        *,
        insert_at: int | None = None,
    ) -> None:
        content = str(content or "").strip()
        if not content:
            return
        budget = int(self.budgets.get(source, 4000))
        clipped, omitted = _clip(content, budget)
        section = ContextSection(
            source=source,
            title=title,
            content=clipped,
            reason=reason,
            budget=budget,
            original_chars=len(content),
            omitted_chars=omitted,
        )
        if insert_at is None:
            report.sections.append(section)
        else:
            report.sections.insert(insert_at, section)
        report.total_chars += len(clipped)
        report.omitted_chars += omitted

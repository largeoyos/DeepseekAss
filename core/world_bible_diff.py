"""Diff helpers for reviewing world-bible changes before saving."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.world_bible import world_bible_to_dict


@dataclass(frozen=True)
class WorldBibleDiffItem:
    category: str
    key: str
    change_type: str
    risk: str
    summary: str
    before: Any = None
    after: Any = None


_IGNORE_KEYS = {"diagnostics", "consistency_warnings"}


def diff_world_bibles(before, after) -> list[WorldBibleDiffItem]:
    before_data = _as_dict(before)
    after_data = _as_dict(after)
    items: list[WorldBibleDiffItem] = []
    for category in sorted((set(before_data) | set(after_data)) - _IGNORE_KEYS):
        old = before_data.get(category)
        new = after_data.get(category)
        if old == new:
            continue
        items.extend(_diff_category(category, old, new))
    return items


def summarize_world_bible_diff(items: list[WorldBibleDiffItem]) -> dict[str, int]:
    summary = {"total": len(items), "high": 0, "medium": 0, "low": 0, "added": 0, "removed": 0, "modified": 0}
    for item in items:
        summary[item.risk] = summary.get(item.risk, 0) + 1
        summary[item.change_type] = summary.get(item.change_type, 0) + 1
    return summary


def _as_dict(value) -> dict:
    if isinstance(value, dict):
        return value
    return world_bible_to_dict(value)


def _diff_category(category: str, before: Any, after: Any) -> list[WorldBibleDiffItem]:
    if isinstance(before, list) or isinstance(after, list):
        return _diff_list(category, before or [], after or [])
    if isinstance(before, dict) or isinstance(after, dict):
        return _diff_mapping(category, before or {}, after or {})
    return [WorldBibleDiffItem(
        category=category,
        key=category,
        change_type="modified",
        risk="medium",
        summary=f"{category} changed",
        before=before,
        after=after,
    )]


def _diff_mapping(category: str, before: dict, after: dict) -> list[WorldBibleDiffItem]:
    items: list[WorldBibleDiffItem] = []
    for key in sorted(set(before) | set(after)):
        if before.get(key) == after.get(key):
            continue
        if key not in before:
            change = "added"
        elif key not in after:
            change = "removed"
        else:
            change = "modified"
        items.append(WorldBibleDiffItem(
            category=category,
            key=str(key),
            change_type=change,
            risk=_risk(change, before.get(key), after.get(key)),
            summary=f"{category}.{key} {change}",
            before=before.get(key),
            after=after.get(key),
        ))
    return items


def _diff_list(category: str, before: list, after: list) -> list[WorldBibleDiffItem]:
    before_map = {_identity(item, index): item for index, item in enumerate(before)}
    after_map = {_identity(item, index): item for index, item in enumerate(after)}
    items: list[WorldBibleDiffItem] = []
    for key in sorted(set(before_map) | set(after_map)):
        old = before_map.get(key)
        new = after_map.get(key)
        if old == new:
            continue
        if key not in before_map:
            change = "added"
        elif key not in after_map:
            change = "removed"
        else:
            change = "modified"
        items.append(WorldBibleDiffItem(
            category=category,
            key=str(key),
            change_type=change,
            risk=_risk(change, old, new),
            summary=f"{_display_name(new if change == 'added' else old, key)} {change}",
            before=old,
            after=new,
        ))
    return items


def _identity(item: Any, index: int) -> str:
    if isinstance(item, dict):
        for key in ("id", "name", "topic", "event", "title", "content", "dialogue"):
            value = item.get(key)
            if value:
                return f"{key}:{value}"
    return f"index:{index}:{repr(item)[:80]}"


def _display_name(item: Any, fallback: str) -> str:
    if isinstance(item, dict):
        return str(item.get("name") or item.get("topic") or item.get("event") or item.get("title") or fallback)
    return str(item if item is not None else fallback)


def _risk(change_type: str, before: Any, after: Any) -> str:
    payload = after if after is not None else before
    if change_type == "removed":
        return "high"
    if isinstance(payload, dict) and (payload.get("locked") or payload.get("importance") == "major"):
        return "high"
    if change_type == "modified":
        return "medium"
    return "low"

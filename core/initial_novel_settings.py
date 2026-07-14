"""Small, side-effect-free helpers for first-chapter setting automation."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any


def world_bible_to_setting_input(world_bible: Any) -> dict:
    """Return the compact World Bible shape expected by setting generation."""
    return {
        "characters": [asdict(item) for item in getattr(world_bible, "characters", [])],
        "locations": [asdict(item) for item in getattr(world_bible, "locations", [])],
        "rules": list(getattr(world_bible, "rules", []) or []),
        "plot_threads": [
            asdict(item) for item in getattr(world_bible, "active_plot_threads", [])
        ],
        "timeline": [asdict(item) for item in getattr(world_bible, "timeline", [])],
        "key_worldbuilding": list(
            getattr(world_bible, "key_worldbuilding_passages", []) or []
        ),
        "global_foreshadowing": list(
            getattr(world_bible, "global_foreshadowing", []) or []
        ),
        "global_key_dialogues": list(
            getattr(world_bible, "global_key_dialogues", []) or []
        ),
    }


def select_missing_initial_setting_updates(
    meta: Any,
    generated: dict | None,
    *,
    fill_background: bool,
    fill_writing_demand: bool,
) -> dict[str, str]:
    """Keep author-written values and select only requested, non-empty AI results."""
    generated = generated or {}
    updates: dict[str, str] = {}
    if (
        fill_background
        and not str(getattr(meta, "background_story", "") or "").strip()
        and str(generated.get("background_story", "") or "").strip()
    ):
        updates["background_story"] = str(generated["background_story"]).strip()
    if (
        fill_writing_demand
        and not str(getattr(meta, "writing_demand", "") or "").strip()
        and str(generated.get("writing_demand", "") or "").strip()
    ):
        updates["writing_demand"] = str(generated["writing_demand"]).strip()
    return updates

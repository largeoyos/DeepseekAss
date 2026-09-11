"""Resolve the shared DeepseekAss data root in source and frozen builds."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def resolve_data_root() -> str:
    """Return the directory that owns ``users`` and other shared app data.

    ``DEEPSEEKASS_DATA_DIR`` is an explicit override.  A PyInstaller onedir
    executable normally lives at ``<project>/dist/<app>/<app>.exe``; prefer an
    existing users directory while walking out of that layout, then fall back
    to the project directory implied by ``dist``.  Source runs use the
    repository root containing this module's ``core`` package.
    """

    override = str(os.environ.get("DEEPSEEKASS_DATA_DIR") or "").strip()
    if override:
        return str(Path(override).expanduser().resolve())

    if getattr(sys, "frozen", False):
        executable_dir = Path(sys.executable).resolve().parent
        candidates = [executable_dir, executable_dir.parent, executable_dir.parent.parent]
        for candidate in candidates:
            if (candidate / "users" / "users.json").is_file() or (candidate / "users").is_dir():
                return str(candidate)
        if executable_dir.parent.name.casefold() == "dist":
            return str(executable_dir.parent.parent)
        return str(executable_dir)

    return str(Path(__file__).resolve().parents[1])


DATA_ROOT = resolve_data_root()

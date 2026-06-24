from __future__ import annotations

from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import QDialog


def apply_responsive_dialog_size(
    dialog: QDialog,
    preferred_width: int,
    preferred_height: int,
    *,
    minimum_width: int = 420,
    minimum_height: int = 280,
    width_ratio: float = 0.82,
    height_ratio: float = 0.78,
) -> None:
    """Keep a dialog resizable and inside the current screen's usable area."""
    screen = dialog.screen() or QGuiApplication.primaryScreen()
    if screen is None:
        dialog.resize(preferred_width, preferred_height)
        dialog.setMinimumSize(minimum_width, minimum_height)
        dialog.setSizeGripEnabled(True)
        return

    available = screen.availableGeometry()
    max_width = max(320, int(available.width() * width_ratio))
    max_height = max(240, int(available.height() * height_ratio))
    width = min(preferred_width, max_width)
    height = min(preferred_height, max_height)
    dialog.setMinimumSize(min(minimum_width, width), min(minimum_height, height))
    dialog.resize(width, height)
    dialog.setSizeGripEnabled(True)

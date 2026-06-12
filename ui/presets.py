"""
Parameter preset definitions for the generation parameter panel.

Slider values are pre-scaled integers matching the GUI controls:
  temp 0-200 (maps to 0.0-2.0), top_p 0-100, fp -200-200, max_tokens raw
"""
from core.settings_manager import DEFAULT_PRESETS


PRESETS = DEFAULT_PRESETS.copy()
CUSTOM_LABEL = "自定义"
COMBO_ITEMS = [CUSTOM_LABEL, *PRESETS.keys()]

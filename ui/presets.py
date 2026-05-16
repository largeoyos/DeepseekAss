"""
Parameter preset definitions for the generation parameter panel.
Slider values are pre-scaled integers matching the GUI controls:
  temp 0-200 (maps to 0.0-2.0), top_p 0-100, fp -200-200, max_tokens raw
"""

PRESETS = {
    "保守": {"temp": 30, "top_p": 50, "fp": 30, "max_tokens": 32768},
    "中庸": {"temp": 70, "top_p": 90, "fp": 0, "max_tokens": 32768},
    "狂野": {"temp": 150, "top_p": 100, "fp": -30, "max_tokens": 32768},
}

CUSTOM_LABEL = "自定义"
COMBO_ITEMS = [CUSTOM_LABEL, "保守", "中庸", "狂野"]

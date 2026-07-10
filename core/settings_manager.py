"""
User-level application settings.

This keeps non-secret UI preferences separate from conversation and book data.
When an encrypted user session is active, the settings file follows the same
Fernet storage convention as the rest of the user's data.
"""
import json
import os
from copy import deepcopy

from config import Config


DEFAULT_PRESETS = {
    "保守": {"temp": 30, "top_p": 85, "fp": 30, "max_tokens": 32768},
    "中庸": {"temp": 70, "top_p": 90, "fp": 0, "max_tokens": 32768},
    "狂野": {"temp": 120, "top_p": 95, "fp": -20, "max_tokens": 32768},
}

DEFAULT_SETTINGS = {
    "last_model": Config.MODEL_V4_FLASH,
    "favorite_models": [Config.MODEL_V4_FLASH, Config.MODEL_V4_PRO],
    "custom_models": [],
    "current_preset": "狂野",
    "presets": DEFAULT_PRESETS,
    "theme": "dark",
    "snapshot_timed_enabled": True,
    "snapshot_interval_minutes": 30,
    "novel_generation_mode": "classic",
    "controlled_agent_enabled": False,
    "agent_skills_enabled": True,
    "agent_web_enabled": False,
    "agent_web_endpoint": "",
    "agent_web_method": "POST",
    "agent_web_api_key": "",
    "agent_web_auth_header": "Authorization",
    "agent_web_auth_prefix": "Bearer ",
    "agent_web_query_field": "query",
    "agent_web_results_path": "results",
    "agent_web_title_field": "title",
    "agent_web_url_field": "url",
    "agent_web_snippet_field": "content",
    "agent_web_max_results": 5,
    "agent_web_timeout_seconds": 15,
    "agent_runtime_backend": "legacy",
    "retrieval_backend": "classic",
    "retrieval_default_limit": 8,
    "retrieval_keyword_weight": 55,
    "retrieval_semantic_weight": 45,
    "retrieval_min_score": 0,
    "embedding_base_url": "",
    "embedding_api_key": "",
    "embedding_model": "",
    "embedding_batch_size": 8,
    "embedding_timeout_seconds": 20,
    "embedding_max_retries": 1,
    "framework_auto_fallback": True,
}


class SettingsManager:
    """Persists model, preset, and UI preferences for one user."""

    def __init__(self, root_dir: str, crypto=None, enc_key: bytes | None = None) -> None:
        self._root_dir = root_dir
        self._crypto = crypto
        self._enc_key = enc_key
        os.makedirs(self._root_dir, exist_ok=True)

    def _path(self) -> str:
        return os.path.join(self._root_dir, "settings.json")

    def _actual_path(self) -> str:
        return self._path() + ".enc" if self._enc_key else self._path()

    def load(self) -> dict:
        settings = deepcopy(DEFAULT_SETTINGS)
        actual = self._actual_path()
        if os.path.exists(actual):
            try:
                if self._enc_key:
                    data = self._crypto.decrypt_json(self._enc_key, actual) or {}
                else:
                    with open(self._path(), "r", encoding="utf-8") as f:
                        data = json.load(f)
                if isinstance(data, dict):
                    settings.update(data)
                    if "novel_generation_mode" not in data:
                        settings["novel_generation_mode"] = (
                            "agent" if bool(data.get("controlled_agent_enabled", False)) else "classic"
                        )
                    merged_presets = deepcopy(DEFAULT_PRESETS)
                    merged_presets.update(data.get("presets", {}) or {})
                    settings["presets"] = merged_presets
            except Exception:
                pass
        if settings.get("novel_generation_mode") not in {"classic", "agent"}:
            settings["novel_generation_mode"] = "classic"
        settings["controlled_agent_enabled"] = (
            settings["novel_generation_mode"] == "agent"
        )
        return settings

    def save(self, settings: dict) -> None:
        data = deepcopy(DEFAULT_SETTINGS)
        data.update(settings)
        if data.get("novel_generation_mode") not in {"classic", "agent"}:
            data["novel_generation_mode"] = "classic"
        # Keep the legacy flag synchronized for one compatibility cycle.
        data["controlled_agent_enabled"] = data["novel_generation_mode"] == "agent"
        if self._enc_key:
            self._crypto.encrypt_json(self._enc_key, self._actual_path(), data)
        else:
            os.makedirs(os.path.dirname(self._path()), exist_ok=True)
            with open(self._path(), "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

    def reset_presets(self) -> dict:
        settings = self.load()
        settings["presets"] = deepcopy(DEFAULT_PRESETS)
        settings["current_preset"] = "狂野"
        self.save(settings)
        return settings

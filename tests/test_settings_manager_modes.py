import json
import tempfile
import unittest

from core.novel_manager import NovelManager
from core.settings_manager import SettingsManager


class SettingsManagerNovelModeTests(unittest.TestCase):
    def test_default_mode_is_classic(self):
        with tempfile.TemporaryDirectory() as root:
            settings = SettingsManager(root).load()
        self.assertEqual("classic", settings["novel_generation_mode"])
        self.assertFalse(settings["controlled_agent_enabled"])

    def test_writing_automation_defaults_are_opt_in(self):
        with tempfile.TemporaryDirectory() as root:
            settings = SettingsManager(root).load()
        self.assertFalse(settings["snapshot_timed_enabled"])
        self.assertFalse(settings["auto_fill_first_chapter_background"])
        self.assertFalse(settings["auto_fill_first_chapter_writing_demand"])

    def test_legacy_timed_snapshot_default_is_disabled(self):
        with tempfile.TemporaryDirectory() as root:
            with open(f"{root}/settings.json", "w", encoding="utf-8") as stream:
                json.dump({"snapshot_timed_enabled": True}, stream)
            settings = SettingsManager(root).load()
        self.assertFalse(settings["snapshot_timed_enabled"])

    def test_explicit_timed_snapshot_preference_is_preserved(self):
        with tempfile.TemporaryDirectory() as root:
            with open(f"{root}/settings.json", "w", encoding="utf-8") as stream:
                json.dump(
                    {
                        "snapshot_timed_enabled": True,
                        "snapshot_timed_user_configured": True,
                    },
                    stream,
                )
            settings = SettingsManager(root).load()
        self.assertTrue(settings["snapshot_timed_enabled"])

    def test_legacy_enabled_flag_migrates_to_agent_mode(self):
        with tempfile.TemporaryDirectory() as root:
            with open(f"{root}/settings.json", "w", encoding="utf-8") as stream:
                json.dump({"controlled_agent_enabled": True}, stream)
            settings = SettingsManager(root).load()
        self.assertEqual("agent", settings["novel_generation_mode"])
        self.assertTrue(settings["controlled_agent_enabled"])

    def test_explicit_mode_takes_precedence_over_legacy_flag(self):
        with tempfile.TemporaryDirectory() as root:
            with open(f"{root}/settings.json", "w", encoding="utf-8") as stream:
                json.dump(
                    {
                        "novel_generation_mode": "classic",
                        "controlled_agent_enabled": True,
                    },
                    stream,
                )
            settings = SettingsManager(root).load()
        self.assertEqual("classic", settings["novel_generation_mode"])
        self.assertFalse(settings["controlled_agent_enabled"])


    def test_save_keeps_legacy_flag_synchronized(self):
        with tempfile.TemporaryDirectory() as root:
            manager = SettingsManager(root)
            manager.save({"novel_generation_mode": "agent"})
            with open(f"{root}/settings.json", "r", encoding="utf-8") as stream:
                raw = json.load(stream)
        self.assertEqual("agent", raw["novel_generation_mode"])
        self.assertTrue(raw["controlled_agent_enabled"])


class GenerationRecordModeTests(unittest.TestCase):
    def _save_record(self, root: str, **mode_fields):
        manager = NovelManager(root)
        manager.save_generation_record(
            title="测试书",
            chapter_num=1,
            chapter_title="第一章",
            version=1,
            prompt="prompt",
            model="model",
            temperature=0.7,
            top_p=0.9,
            max_tokens=4096,
            frequency_penalty=0.0,
            content_preview="preview",
            **mode_fields,
        )
        return manager.load_generation_record("测试书", 1, 1)

    def test_classic_record_has_explicit_mode(self):
        with tempfile.TemporaryDirectory() as root:
            record = self._save_record(root)
        self.assertEqual("classic", record["generation_mode"])
        self.assertIsNone(record["agent_run_id"])

    def test_agent_record_keeps_run_id(self):
        with tempfile.TemporaryDirectory() as root:
            record = self._save_record(
                root,
                generation_mode="agent",
                agent_run_id="run-123",
                operation="chapter_polish",
                polish_requirement="改善节奏",
                polish_plan={"constraints": ["不改变剧情"]},
                fidelity_report={"passed": True},
            )
        self.assertEqual("agent", record["generation_mode"])
        self.assertEqual("run-123", record["agent_run_id"])
        self.assertEqual("chapter_polish", record["operation"])
        self.assertEqual("改善节奏", record["polish_requirement"])
        self.assertTrue(record["fidelity_report"]["passed"])

if __name__ == "__main__":
    unittest.main()
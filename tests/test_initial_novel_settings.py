import unittest

from core.initial_novel_settings import (
    select_missing_initial_setting_updates,
    world_bible_to_setting_input,
)
from core.novel_manager import NovelMeta
from core.world_bible import CharacterEntry, PlotThread, WorldBible


class InitialNovelSettingsTests(unittest.TestCase):
    def test_selects_only_enabled_missing_fields(self):
        meta = NovelMeta(
            background_story="作者写好的世界观",
            writing_demand="",
        )
        updates = select_missing_initial_setting_updates(
            meta,
            {
                "background_story": "AI 世界观",
                "writing_demand": "克制、悬疑、第三人称",
            },
            fill_background=True,
            fill_writing_demand=True,
        )
        self.assertEqual({"writing_demand": "克制、悬疑、第三人称"}, updates)

    def test_respects_disabled_fields_and_empty_model_output(self):
        meta = NovelMeta(background_story="", writing_demand="")
        updates = select_missing_initial_setting_updates(
            meta,
            {"background_story": "AI 世界观", "writing_demand": "  "},
            fill_background=False,
            fill_writing_demand=True,
        )
        self.assertEqual({}, updates)

    def test_builds_world_data_supported_by_setting_generator(self):
        bible = WorldBible(
            characters=[CharacterEntry(name="林舟")],
            rules=["夜间禁止离城"],
            active_plot_threads=[PlotThread(name="失踪案")],
        )
        data = world_bible_to_setting_input(bible)
        self.assertEqual("林舟", data["characters"][0]["name"])
        self.assertEqual(["夜间禁止离城"], data["rules"])
        self.assertEqual("失踪案", data["plot_threads"][0]["name"])


if __name__ == "__main__":
    unittest.main()

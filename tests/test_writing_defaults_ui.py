import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QComboBox, QGroupBox, QLabel, QVBoxLayout, QWidget

from core.auth_manager import AuthManager
from core.novel_manager import NovelManager
from core.settings_manager import SettingsManager
from core.style_profiles import (
    ResolvedStyle,
    StyleAnchor,
    StyleProfile,
    StyleProfileRepository,
    render_style_audit,
    render_style_prompt,
)
from ui.main_window import DeepSeekChatGUI
from ui.settings_dialog import SettingsDialog


class _SettingsParent(QWidget):
    def __init__(self, manager):
        super().__init__()
        self._novel_manager = manager
        self._client = SimpleNamespace(model="deepseek-v4-flash")

    def _get_current_book_title(self):
        return ""

    def _usage_logged_client(self, _operation):
        return self._client


class WritingDefaultsUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_settings_dialog_saves_writing_defaults(self):
        with tempfile.TemporaryDirectory() as root:
            manager = NovelManager(os.path.join(root, "books"))
            repository = StyleProfileRepository(manager)
            profile = repository.save(StyleProfile(name="测试文风"))
            settings_manager = SettingsManager(root)
            parent = _SettingsParent(manager)
            dialog = SettingsDialog(
                parent,
                settings_manager=settings_manager,
                auth=AuthManager,
                username="tester",
                user_dir=root,
                encrypted=False,
                api_config={
                    "text": {"base_url": "https://example.test/v1", "api_key": "key", "model": "api-model"},
                    "image": {},
                },
                api_config_callback=lambda _value: None,
                api_test_callback=lambda _kind, _value: (True, "ok"),
                settings_changed_callback=lambda: None,
                password_changed_callback=lambda _key: None,
            )
            dialog._default_model_combo.setCurrentText("custom-writing-model")
            dialog._default_preset_combo.setCurrentText("中庸")
            dialog._default_genre_combo.setCurrentIndex(dialog._default_genre_combo.findData("suspense"))
            dialog._default_style_combo.setCurrentIndex(dialog._default_style_combo.findData(profile.profile_id))
            dialog._default_style_strength_combo.setCurrentIndex(
                dialog._default_style_strength_combo.findData("strict")
            )
            with patch("ui.settings_dialog.QMessageBox.information"):
                dialog._save_writing_defaults()
            settings = settings_manager.load()
            self.assertEqual(settings["last_model"], "custom-writing-model")
            self.assertEqual(settings["current_preset"], "中庸")
            self.assertEqual(settings["default_genre"], "suspense")
            self.assertEqual(settings["default_style_profile_id"], profile.profile_id)
            self.assertEqual(settings["default_style_strength"], "strict")
            dialog.close()
            parent.close()

    def test_new_book_inherits_user_defaults(self):
        with tempfile.TemporaryDirectory() as root:
            manager = NovelManager(os.path.join(root, "books"))
            settings_manager = SettingsManager(root)
            settings = settings_manager.load()
            settings.update({
                "default_genre": "sci_fi",
                "default_style_profile_id": "style-default",
                "default_style_strength": "strict",
            })
            settings_manager.save(settings)
            fake_window = SimpleNamespace(
                _novel_manager=manager,
                _settings_manager=settings_manager,
            )
            DeepSeekChatGUI._create_book_with_defaults(fake_window, "新书")
            meta = manager.load_meta("新书")
            self.assertEqual(meta.genre, "sci_fi")
            self.assertEqual(meta.style_profile_id, "style-default")
            self.assertEqual(meta.style_strength, "strict")

    def test_main_groups_are_true_foldouts(self):
        group = QGroupBox("模型选择")
        layout = QVBoxLayout(group)
        child = QLabel("model")
        layout.addWidget(child)
        DeepSeekChatGUI._set_group_collapsible(group, expanded=False)
        self.assertTrue(child.isHidden())
        self.assertLessEqual(group.maximumHeight(), 34)
        group.setChecked(True)
        self.assertFalse(child.isHidden())

    def test_book_style_selection_is_saved_immediately(self):
        with tempfile.TemporaryDirectory() as root:
            manager = NovelManager(root)
            manager.create_book("当前书")
            bookshelf = QComboBox()
            bookshelf.addItem("当前书")
            profiles = QComboBox()
            profiles.addItem("指定档案", "style-123")
            strengths = QComboBox()
            strengths.addItem("严格", "strict")
            fake_window = SimpleNamespace(
                _bookshelf_combo=bookshelf,
                _novel_style_profile_combo=profiles,
                _novel_style_strength_combo=strengths,
                _novel_manager=manager,
            )
            DeepSeekChatGUI._on_novel_style_default_changed(fake_window)
            meta = manager.load_meta("当前书")
            self.assertEqual(meta.style_profile_id, "style-123")
            self.assertEqual(meta.style_strength, "strict")

    def test_strict_style_includes_metrics_and_full_audit_rules(self):
        profile = StyleProfile(
            name="严谨文风",
            stable_rules=["对白后必须跟随人物动作"],
            avoid_rules=["避免直接总结情绪"],
            metrics={"sentence_length_avg": 14.2, "paragraph_length_avg": 92, "dialogue_ratio": 0.31},
            anchors=[StyleAnchor(text=f"形式范例 {index}：不同节奏片段") for index in range(7)],
        )
        resolved = ResolvedStyle(profile, "strict")
        prompt = render_style_prompt(resolved)
        audit = render_style_audit(resolved)
        self.assertIn("量化节奏参考", prompt)
        self.assertIn("内部逐篇校准例文", prompt)
        self.assertIn("对白后必须跟随人物动作", audit)
        self.assertIn("避免直接总结情绪", audit)
        self.assertIn("sentence_length_avg", audit)
        self.assertIn("审查时以下列例文", audit)


if __name__ == "__main__":
    unittest.main()

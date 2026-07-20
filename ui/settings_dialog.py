import os
import shutil
import threading
import zipfile
from copy import deepcopy

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QCheckBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QScrollArea,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from config import Config
from core.auth_manager import AuthError, AuthManager
from core.settings_manager import DEFAULT_PRESETS, SettingsManager
from core.style_profiles import STYLE_STRENGTH_LABELS, StyleProfileRepository
from utils.genre_styles import GENRES


class _SettingsAsyncSignals(QObject):
    completed = pyqtSignal(str, bool, str)



class SettingsDialog(QDialog):
    """Central settings dialog for API, models, presets, account, and data."""

    def __init__(
        self,
        parent,
        *,
        settings_manager: SettingsManager,
        auth: AuthManager,
        username: str,
        user_dir: str,
        encrypted: bool,
        api_config: dict,
        api_config_callback,
        api_test_callback,
        settings_changed_callback,
        password_changed_callback,
        mode_change_guard=None,
    ):
        super().__init__(parent)
        self._settings_manager = settings_manager
        self._settings = settings_manager.load()
        self._auth = auth
        self._username = username
        self._user_dir = user_dir
        self._encrypted = encrypted
        self._api_config = deepcopy(api_config)
        self._api_config_callback = api_config_callback
        self._api_test_callback = api_test_callback
        self._settings_changed_callback = settings_changed_callback
        self._password_changed_callback = password_changed_callback
        self._mode_change_guard = mode_change_guard
        self._updating_agent_mode = False
        self._async_signals = _SettingsAsyncSignals(self)
        self._async_signals.completed.connect(self._on_async_completed)
        self._async_buttons: dict[str, QPushButton] = {}

        self.setWindowTitle("设置中心")
        self.setFixedSize(780, 640)
        self._init_ui()

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        tabs = QTabWidget()
        tabs.addTab(self._build_api_tab(), "API 与模型")
        tabs.addTab(self._build_models_tab(), "写作默认值与参数")
        tabs.addTab(self._build_account_tab(), "账号安全")
        tabs.addTab(self._build_data_tab(), "数据管理")
        tabs.addTab(self._build_appearance_tab(), "外观")
        tabs.addTab(self._build_agent_tab(), "Agent")
        tabs.addTab(self._build_writing_automation_tab(), "写作自动化")
        layout.addWidget(tabs, stretch=1)

        row = QHBoxLayout()
        row.addStretch()
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.accept)
        row.addWidget(close_btn)
        layout.addLayout(row)

    def _build_models_tab(self) -> QWidget:
        page = QScrollArea()
        page.setWidgetResizable(True)
        content = QWidget()
        layout = QVBoxLayout(content)

        defaults_group = QGroupBox("新建小说与运行默认值")
        defaults_layout = QVBoxLayout(defaults_group)
        defaults_form = QFormLayout()

        self._default_model_combo = QComboBox()
        self._default_model_combo.setEditable(True)
        self._default_model_combo.addItem("正文 Pro · 其余任务 Flash（省费）", "pro_body_flash_aux")
        known_models = []
        configured_model = str((self._api_config.get("text") or {}).get("model", ""))
        for model in [
            configured_model,
            Config.MODEL_V4_FLASH,
            Config.MODEL_V4_PRO,
            *(self._settings.get("favorite_models") or []),
            *(self._settings.get("custom_models") or []),
            str(self._settings.get("last_model") or ""),
        ]:
            if model and model not in known_models:
                known_models.append(model)
                self._default_model_combo.addItem(model, model)
        model_route = str(self._settings.get("model_routing_mode") or "standard")
        model_value = "pro_body_flash_aux" if model_route == "pro_body_flash_aux" else str(self._settings.get("last_model") or configured_model)
        model_index = self._default_model_combo.findData(model_value)
        self._default_model_combo.setCurrentIndex(max(0, model_index))

        self._default_preset_combo = QComboBox()
        self._default_genre_combo = QComboBox()
        for genre in GENRES:
            self._default_genre_combo.addItem(genre.display_name, genre.key)
        genre_index = self._default_genre_combo.findData(str(self._settings.get("default_genre") or "none"))
        self._default_genre_combo.setCurrentIndex(max(0, genre_index))

        self._default_style_combo = QComboBox()
        self._default_style_strength_combo = QComboBox()
        for key, label in STYLE_STRENGTH_LABELS.items():
            self._default_style_strength_combo.addItem(label, key)
        strength_index = self._default_style_strength_combo.findData(
            str(self._settings.get("default_style_strength") or "standard")
        )
        self._default_style_strength_combo.setCurrentIndex(max(0, strength_index))
        self._refresh_default_style_profiles()

        defaults_form.addRow("默认模型", self._default_model_combo)
        defaults_form.addRow("默认生成参数", self._default_preset_combo)
        defaults_form.addRow("默认题材", self._default_genre_combo)
        defaults_form.addRow("默认文风", self._default_style_combo)
        defaults_form.addRow("默认文风强度", self._default_style_strength_combo)
        defaults_layout.addLayout(defaults_form)
        default_actions = QHBoxLayout()
        edit_style_btn = QPushButton("管理 / 修改文风档案")
        edit_style_btn.clicked.connect(self._manage_style_profiles)
        save_defaults_btn = QPushButton("保存默认项")
        save_defaults_btn.clicked.connect(self._save_writing_defaults)
        default_actions.addWidget(edit_style_btn)
        default_actions.addStretch()
        default_actions.addWidget(save_defaults_btn)
        defaults_layout.addLayout(default_actions)
        layout.addWidget(defaults_group)

        preset_group = QGroupBox("参数预设")
        preset_layout = QVBoxLayout(preset_group)
        self._preset_list = QListWidget()
        self._preset_list.currentTextChanged.connect(self._load_selected_preset)
        preset_layout.addWidget(self._preset_list)

        form = QFormLayout()
        self._preset_name = QLineEdit()
        self._preset_temp = QSpinBox()
        self._preset_temp.setRange(0, 200)
        self._preset_top_p = QSpinBox()
        self._preset_top_p.setRange(0, 100)
        self._preset_fp = QSpinBox()
        self._preset_fp.setRange(-200, 200)
        self._preset_max_tokens = QSpinBox()
        self._preset_max_tokens.setRange(1, 300000)
        self._preset_max_tokens.setSingleStep(512)
        form.addRow("名称", self._preset_name)
        form.addRow("temperature x100", self._preset_temp)
        form.addRow("top_p x100", self._preset_top_p)
        form.addRow("freq_penalty x100", self._preset_fp)
        form.addRow("max_tokens", self._preset_max_tokens)
        preset_layout.addLayout(form)

        row = QHBoxLayout()
        save_btn = QPushButton("保存预设")
        save_btn.clicked.connect(self._save_preset)
        delete_btn = QPushButton("删除预设")
        delete_btn.clicked.connect(self._delete_preset)
        reset_btn = QPushButton("恢复默认")
        reset_btn.clicked.connect(self._reset_presets)
        row.addWidget(save_btn)
        row.addWidget(delete_btn)
        row.addWidget(reset_btn)
        preset_layout.addLayout(row)
        layout.addWidget(preset_group)

        layout.addStretch()
        page.setWidget(content)
        self._refresh_model_and_preset_lists()
        return page

    def _refresh_default_style_profiles(self) -> None:
        if not hasattr(self, "_default_style_combo"):
            return
        selected = str(self._default_style_combo.currentData() or "")
        if not selected:
            selected = str(self._settings.get("default_style_profile_id") or "")
        self._default_style_combo.blockSignals(True)
        self._default_style_combo.clear()
        self._default_style_combo.addItem("不指定", "")
        parent = self.parent()
        manager = getattr(parent, "_novel_manager", None)
        if manager is not None:
            for profile in StyleProfileRepository(manager).list_profiles():
                self._default_style_combo.addItem(f"{profile.name} · v{profile.revision}", profile.profile_id)
        index = self._default_style_combo.findData(selected)
        self._default_style_combo.setCurrentIndex(index if index >= 0 else 0)
        self._default_style_combo.blockSignals(False)

    def _manage_style_profiles(self) -> None:
        parent = self.parent()
        manager = getattr(parent, "_novel_manager", None)
        client = getattr(parent, "_client", None)
        if manager is None or client is None:
            QMessageBox.warning(self, "无法打开", "当前窗口无法访问文风档案库。")
            return
        from ui.style_profile_dialog import StyleProfileDialog
        logged_client = (
            parent._usage_logged_client("style_profile_extract")
            if hasattr(parent, "_usage_logged_client") else client
        )
        book_title = parent._get_current_book_title() if hasattr(parent, "_get_current_book_title") else ""
        dialog = StyleProfileDialog(
            self, manager, logged_client, client.model, book_title=book_title or ""
        )
        dialog.exec()
        self._refresh_default_style_profiles()

    def _save_writing_defaults(self) -> None:
        settings = self._settings_manager.load()
        model_text = self._default_model_combo.currentText().strip()
        model_index = self._default_model_combo.currentIndex()
        item_text = (
            self._default_model_combo.itemText(model_index)
            if model_index >= 0 else ""
        )
        model_value = str(self._default_model_combo.currentData() or model_text)
        if model_text and model_text != item_text:
            model_value = model_text
        if model_value == "pro_body_flash_aux":
            settings["model_routing_mode"] = "pro_body_flash_aux"
            settings["last_model"] = Config.MODEL_V4_FLASH
        elif model_value:
            settings["model_routing_mode"] = "standard"
            settings["last_model"] = model_value
            known = list(settings.get("custom_models") or [])
            if model_value not in known:
                known.append(model_value)
                settings["custom_models"] = known
        settings["current_preset"] = str(self._default_preset_combo.currentText() or "狂野")
        settings["default_genre"] = str(self._default_genre_combo.currentData() or "none")
        settings["default_style_profile_id"] = str(self._default_style_combo.currentData() or "")
        settings["default_style_strength"] = str(
            self._default_style_strength_combo.currentData() or "standard"
        )
        self._settings_manager.save(settings)
        self._settings = settings
        self._settings_changed_callback()
        QMessageBox.information(self, "已保存", "新建小说、模型和生成参数默认项已保存。")

    def _build_api_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(14)

        title = QLabel("模型服务")
        title.setObjectName("apiTitle")
        subtitle = QLabel("分别配置文字生成与图片生成服务。配置保存在当前用户的加密文件中。")
        subtitle.setObjectName("apiSubtitle")
        subtitle.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(subtitle)

        self._api_fields: dict[str, dict[str, QLineEdit]] = {}
        layout.addWidget(self._build_api_section(
            "text", "文字 API", "用于对话、续写、分析、世界书与 Agent。", required=True
        ))
        layout.addWidget(self._build_api_section(
            "image", "图片 API", "用于封面、插图等图片生成；暂不使用时可以留空。", required=False
        ))

        actions = QHBoxLayout()
        actions.addStretch()
        save_btn = QPushButton("保存全部配置")
        save_btn.setObjectName("primaryButton")
        save_btn.clicked.connect(self._save_api_config)
        actions.addWidget(save_btn)
        layout.addLayout(actions)
        layout.addStretch()

        page.setStyleSheet("""
            QLabel#apiTitle { font-size: 22px; font-weight: 700; }
            QLabel#apiSubtitle { color: #8b98a9; margin-bottom: 4px; }
            QGroupBox#apiCard {
                border: 1px solid #394758; border-radius: 10px;
                margin-top: 12px; padding: 14px;
                font-size: 15px; font-weight: 700;
            }
            QGroupBox#apiCard::title { subcontrol-origin: margin; left: 14px; padding: 0 6px; }
            QPushButton#primaryButton { background: #2774c8; color: white; padding: 8px 18px; font-weight: 700; }
        """)
        return page

    def _build_api_section(self, kind: str, title: str, description: str, *, required: bool) -> QGroupBox:
        config = self._api_config.get(kind, {}) or {}
        group = QGroupBox(title)
        group.setObjectName("apiCard")
        layout = QVBoxLayout(group)

        note = QLabel(description)
        note.setWordWrap(True)
        note.setStyleSheet("color: #8b98a9; font-weight: 400;")
        layout.addWidget(note)

        base_url = QLineEdit(str(config.get("base_url", "")))
        base_url.setPlaceholderText("https://api.example.com/v1")
        api_key = QLineEdit(str(config.get("api_key", "")))
        api_key.setEchoMode(QLineEdit.EchoMode.Password)
        api_key.setPlaceholderText("sk-...")
        model = QLineEdit(str(config.get("model", "")))
        model.setPlaceholderText("例如 deepseek-chat / gpt-image-1")

        form = QFormLayout()
        form.addRow("调用地址" + (" *" if required else ""), base_url)
        form.addRow("API Key" + (" *" if required else ""), api_key)
        form.addRow("模型名称" + (" *" if required else ""), model)
        layout.addLayout(form)

        row = QHBoxLayout()
        reveal = QCheckBox("显示 API Key")
        reveal.toggled.connect(
            lambda checked, field=api_key: field.setEchoMode(
                QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
            )
        )
        test_btn = QPushButton("测试连接")
        test_btn.clicked.connect(lambda _=False, api_kind=kind: self._test_api(api_kind))
        row.addWidget(reveal)
        row.addStretch()
        row.addWidget(test_btn)
        layout.addLayout(row)

        self._api_fields[kind] = {"base_url": base_url, "api_key": api_key, "model": model}
        return group

    def _api_values(self, kind: str) -> dict:
        fields = self._api_fields[kind]
        return {name: field.text().strip() for name, field in fields.items()}

    def _save_api_config(self) -> None:
        text_config = self._api_values("text")
        image_config = self._api_values("image")
        if not all(text_config.values()):
            QMessageBox.warning(self, "配置不完整", "文字 API 的调用地址、API Key 和模型名称均不能为空。")
            return
        if any(image_config.values()) and not all(image_config.values()):
            QMessageBox.warning(self, "配置不完整", "图片 API 如需启用，调用地址、API Key 和模型名称必须全部填写。")
            return
        try:
            self._api_config_callback({"text": text_config, "image": image_config})
        except Exception as exc:
            QMessageBox.critical(self, "保存失败", str(exc))
            return
        self._api_config = {"text": text_config, "image": image_config}
        QMessageBox.information(self, "已保存", "文字 API 与图片 API 配置已加密保存并生效。")

    def _test_api(self, kind: str) -> None:
        config = self._api_values(kind)
        if not all(config.values()):
            QMessageBox.warning(self, "配置不完整", "请先填写调用地址、API Key 和模型名称。")
            return
        ok, message = self._api_test_callback(kind, config)
        if ok:
            QMessageBox.information(self, "连接成功", message)
        else:
            QMessageBox.critical(self, "连接失败", message)

    def _build_account_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        status = QLabel(f"当前用户：{self._username}\n加密状态：{'已启用' if self._encrypted else '未启用'}")
        status.setWordWrap(True)
        layout.addWidget(status)

        self._old_password = QLineEdit()
        self._old_password.setEchoMode(QLineEdit.EchoMode.Password)
        self._new_password = QLineEdit()
        self._new_password.setEchoMode(QLineEdit.EchoMode.Password)
        self._confirm_password = QLineEdit()
        self._confirm_password.setEchoMode(QLineEdit.EchoMode.Password)
        form = QFormLayout()
        form.addRow("旧密码", self._old_password)
        form.addRow("新密码", self._new_password)
        form.addRow("确认新密码", self._confirm_password)
        layout.addLayout(form)

        btn = QPushButton("修改密码")
        btn.clicked.connect(self._change_password)
        layout.addWidget(btn)
        layout.addStretch()
        return page

    def _build_data_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(QLabel("导出/导入会操作当前用户目录下的全部数据。"))
        export_btn = QPushButton("导出用户数据包")
        export_btn.clicked.connect(self._export_user_data)
        import_btn = QPushButton("导入用户数据包")
        import_btn.clicked.connect(self._import_user_data)
        clear_btn = QPushButton("清空当前用户数据")
        clear_btn.clicked.connect(self._clear_user_data)
        layout.addWidget(export_btn)
        layout.addWidget(import_btn)
        layout.addWidget(clear_btn)
        layout.addStretch()
        return page

    def _build_appearance_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        self._dark_theme = QCheckBox("暗色主题")
        self._dark_theme.setChecked(self._settings.get("theme", "dark") == "dark")
        self._dark_theme.stateChanged.connect(self._save_theme)
        layout.addWidget(self._dark_theme)
        layout.addStretch()
        return page

    def _build_writing_automation_tab(self) -> QWidget:
        """Options that affect writing workflow rather than one generation mode."""
        page = QWidget()
        layout = QVBoxLayout(page)

        fill_group = QGroupBox("首章完成后补全空白设定")
        fill_layout = QVBoxLayout(fill_group)
        notice = QLabel(
            "仅在一本小说的首章首次生成并成功更新世界书后运行；"
            "只补全仍为空的字段，绝不会覆盖作者已经填写的内容。"
        )
        notice.setWordWrap(True)
        fill_layout.addWidget(notice)
        self._auto_fill_first_chapter_background = QCheckBox("自动概括世界观 / 背景故事")
        self._auto_fill_first_chapter_background.setToolTip("会额外调用一次模型，从首章世界书归纳背景设定")
        self._auto_fill_first_chapter_background.setChecked(
            bool(self._settings.get("auto_fill_first_chapter_background", False))
        )
        self._auto_fill_first_chapter_writing_demand = QCheckBox("自动概括写作要求")
        self._auto_fill_first_chapter_writing_demand.setToolTip("会额外调用一次模型，归纳首章已呈现的风格、节奏与约束")
        self._auto_fill_first_chapter_writing_demand.setChecked(
            bool(self._settings.get("auto_fill_first_chapter_writing_demand", False))
        )
        fill_layout.addWidget(self._auto_fill_first_chapter_background)
        fill_layout.addWidget(self._auto_fill_first_chapter_writing_demand)
        layout.addWidget(fill_group)

        style_group = QGroupBox("高保真文风")
        style_layout = QVBoxLayout(style_group)
        self._style_candidate_rerank = QCheckBox("严格文风启用双候选竞稿")
        self._style_candidate_rerank.setToolTip(
            "仅在绑定文风档案且强度为“严格”时生效；生成两份正文并按文风、内容锁和自然度选优，约增加一倍正文生成成本。"
        )
        self._style_candidate_rerank.setChecked(
            bool(self._settings.get("style_candidate_rerank_enabled", False))
        )
        style_layout.addWidget(self._style_candidate_rerank)
        layout.addWidget(style_group)

        snapshot_group = QGroupBox("项目快照")
        snapshot_form = QFormLayout(snapshot_group)
        self._snapshot_timed_enabled = QCheckBox("定时创建项目快照")
        self._snapshot_timed_enabled.setToolTip("后台快照仅保存有改动的项目；关闭后不再显示 Timed project snapshot 日志")
        self._snapshot_timed_enabled.setChecked(
            bool(self._settings.get("snapshot_timed_enabled", False))
        )
        self._snapshot_interval_minutes = QSpinBox()
        self._snapshot_interval_minutes.setRange(5, 240)
        self._snapshot_interval_minutes.setSuffix(" 分钟")
        self._snapshot_interval_minutes.setValue(
            max(5, int(self._settings.get("snapshot_interval_minutes", 30)))
        )
        snapshot_form.addRow(self._snapshot_timed_enabled)
        snapshot_form.addRow("快照间隔", self._snapshot_interval_minutes)
        snapshot_note = QLabel("章节保存后的版本快照不受此开关影响，仍可用于恢复章节历史。")
        snapshot_note.setWordWrap(True)
        snapshot_form.addRow(snapshot_note)
        layout.addWidget(snapshot_group)

        save_btn = QPushButton("保存写作自动化设置")
        save_btn.clicked.connect(self._save_writing_automation_settings)
        layout.addWidget(save_btn)
        layout.addStretch()
        return page

    def _save_writing_automation_settings(self) -> None:
        settings = self._settings_manager.load()
        settings["auto_fill_first_chapter_background"] = (
            self._auto_fill_first_chapter_background.isChecked()
        )
        settings["auto_fill_first_chapter_writing_demand"] = (
            self._auto_fill_first_chapter_writing_demand.isChecked()
        )
        settings["style_candidate_rerank_enabled"] = self._style_candidate_rerank.isChecked()
        settings["snapshot_timed_enabled"] = self._snapshot_timed_enabled.isChecked()
        settings["snapshot_timed_user_configured"] = True
        settings["snapshot_interval_minutes"] = self._snapshot_interval_minutes.value()
        self._settings_manager.save(settings)
        self._settings = settings
        self._settings_changed_callback()

    def _build_agent_tab(self) -> QWidget:
        page = QScrollArea()
        page.setWidgetResizable(True)
        content = QWidget()
        layout = QVBoxLayout(content)
        notice = QLabel(
            "选择小说写作的全局生成模式。两种模式共享书籍、章节树和世界书，"
            "但生成入口与运行状态互相隔离。"
        )
        notice.setWordWrap(True)
        layout.addWidget(notice)

        mode_group = QGroupBox("小说写作模式")
        mode_layout = QVBoxLayout(mode_group)
        self._classic_mode_radio = QRadioButton("原版写作模式")
        self._classic_mode_radio.setToolTip("直接使用现有章节生成、审稿、修复和保存流程")
        self._agent_mode_radio = QRadioButton("Agent 写作模式")
        self._agent_mode_radio.setToolTip("先规划并确认上下文，再调用现有章节生成流水线")
        self._agent_mode_group = QButtonGroup(self)
        self._agent_mode_group.addButton(self._classic_mode_radio)
        self._agent_mode_group.addButton(self._agent_mode_radio)
        mode = self._settings.get("novel_generation_mode", "classic")
        self._agent_mode_radio.setChecked(mode == "agent")
        self._classic_mode_radio.setChecked(mode != "agent")
        self._classic_mode_radio.toggled.connect(self._on_agent_mode_toggled)
        self._agent_mode_radio.toggled.connect(self._on_agent_mode_toggled)
        mode_layout.addWidget(self._classic_mode_radio)
        mode_layout.addWidget(self._agent_mode_radio)
        layout.addWidget(mode_group)

        self._agent_skills = QCheckBox("启用内置及书籍级加密 Skills")
        self._agent_skills.setChecked(bool(self._settings.get("agent_skills_enabled", True)))
        self._agent_skills.stateChanged.connect(self._save_agent_settings)
        layout.addWidget(self._agent_skills)

        self._agent_multi_plan = QCheckBox("启用三方案章节规划与 Critic 推荐（额外消耗一次比较调用）")
        self._agent_multi_plan.setToolTip(
            "关闭时沿用原来的单方案章节规划；开启后生成三种剧情策略并允许在生成正文前选择。"
        )
        self._agent_multi_plan.setChecked(bool(self._settings.get("agent_multi_plan_enabled", False)))
        self._agent_multi_plan.stateChanged.connect(self._save_agent_settings)
        layout.addWidget(self._agent_multi_plan)

        framework_group = QGroupBox("Agent 框架与混合检索（开发预览）")
        framework_form = QFormLayout(framework_group)
        self._agent_runtime_backend = QComboBox()
        self._agent_runtime_backend.addItem("现有自研运行时", "legacy")
        self._agent_runtime_backend.addItem("LangChain + LangGraph", "langgraph")
        runtime_index = self._agent_runtime_backend.findData(self._settings.get("agent_runtime_backend", "legacy"))
        self._agent_runtime_backend.setCurrentIndex(max(0, runtime_index))
        framework_form.addRow("Agent 运行时", self._agent_runtime_backend)
        self._retrieval_backend = QComboBox()
        self._retrieval_backend.addItem("现有关键词检索", "classic")
        self._retrieval_backend.addItem("LlamaIndex 混合检索", "hybrid")
        retrieval_index = self._retrieval_backend.findData(self._settings.get("retrieval_backend", "classic"))
        self._retrieval_backend.setCurrentIndex(max(0, retrieval_index))
        framework_form.addRow("上下文检索", self._retrieval_backend)
        self._embedding_base_url = QLineEdit(str(self._settings.get("embedding_base_url", "")))
        self._embedding_base_url.setPlaceholderText("留空则继承当前 OpenAI 兼容 API 地址")
        self._embedding_api_key = QLineEdit(str(self._settings.get("embedding_api_key", "")))
        self._embedding_api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self._embedding_api_key.setPlaceholderText("留空则继承当前 API Key")
        self._embedding_model = QLineEdit(str(self._settings.get("embedding_model", "")))
        self._embedding_model.setPlaceholderText("例如 text-embedding-3-small")
        framework_form.addRow("Embedding 地址", self._embedding_base_url)
        framework_form.addRow("Embedding Key", self._embedding_api_key)
        framework_form.addRow("Embedding 模型", self._embedding_model)
        self._framework_auto_fallback = QCheckBox("框架异常时自动回退现有实现")
        self._framework_auto_fallback.setChecked(bool(self._settings.get("framework_auto_fallback", True)))
        framework_form.addRow(self._framework_auto_fallback)
        framework_actions = QHBoxLayout()
        framework_save = QPushButton("保存框架配置")
        framework_save.clicked.connect(self._save_agent_settings)
        embedding_test = QPushButton("测试 Embedding")
        embedding_test.clicked.connect(self._test_embedding)
        rebuild_index = QPushButton("重建当前书籍索引")
        rebuild_index.clicked.connect(self._rebuild_retrieval_index)
        clear_index = QPushButton("清除当前书籍索引")
        clear_index.clicked.connect(self._clear_retrieval_index)
        for button in (framework_save, embedding_test, rebuild_index, clear_index):
            framework_actions.addWidget(button)
        self._async_buttons["embedding"] = embedding_test
        self._async_buttons["rebuild"] = rebuild_index
        framework_form.addRow(framework_actions)
        self._framework_status = QLabel("索引按需创建；未启用混合检索时不会加载新框架。")
        self._framework_status.setWordWrap(True)
        framework_form.addRow(self._framework_status)
        layout.addWidget(framework_group)

        web_group = QGroupBox("联网搜索")
        web_form = QFormLayout(web_group)
        self._agent_web = QCheckBox("启用受控网页搜索工具")
        self._agent_web.setChecked(bool(self._settings.get("agent_web_enabled", False)))
        self._agent_web.stateChanged.connect(self._save_agent_settings)
        web_form.addRow(self._agent_web)
        self._agent_web_endpoint = QLineEdit(str(self._settings.get("agent_web_endpoint", "")))
        self._agent_web_endpoint.setPlaceholderText("https://api.example.com/search")
        web_form.addRow("HTTPS Endpoint", self._agent_web_endpoint)
        self._agent_web_method = QLineEdit(str(self._settings.get("agent_web_method", "POST")))
        web_form.addRow("请求方法", self._agent_web_method)
        self._agent_web_key = QLineEdit(str(self._settings.get("agent_web_api_key", "")))
        self._agent_web_key.setEchoMode(QLineEdit.EchoMode.Password)
        web_form.addRow("API Key", self._agent_web_key)
        self._agent_web_auth_header = QLineEdit(str(self._settings.get("agent_web_auth_header", "Authorization")))
        self._agent_web_auth_prefix = QLineEdit(str(self._settings.get("agent_web_auth_prefix", "Bearer ")))
        self._agent_web_query_field = QLineEdit(str(self._settings.get("agent_web_query_field", "query")))
        web_form.addRow("认证 Header", self._agent_web_auth_header)
        web_form.addRow("认证前缀", self._agent_web_auth_prefix)
        web_form.addRow("查询字段", self._agent_web_query_field)
        self._agent_web_results_path = QLineEdit(str(self._settings.get("agent_web_results_path", "results")))
        web_form.addRow("结果路径", self._agent_web_results_path)
        self._agent_web_title_field = QLineEdit(str(self._settings.get("agent_web_title_field", "title")))
        self._agent_web_url_field = QLineEdit(str(self._settings.get("agent_web_url_field", "url")))
        self._agent_web_snippet_field = QLineEdit(str(self._settings.get("agent_web_snippet_field", "content")))
        web_form.addRow("标题字段", self._agent_web_title_field)
        web_form.addRow("URL 字段", self._agent_web_url_field)
        web_form.addRow("摘要字段", self._agent_web_snippet_field)
        self._agent_web_max_results = QSpinBox()
        self._agent_web_max_results.setRange(1, 10)
        self._agent_web_max_results.setValue(int(self._settings.get("agent_web_max_results", 5)))
        self._agent_web_timeout = QSpinBox()
        self._agent_web_timeout.setRange(1, 30)
        self._agent_web_timeout.setValue(int(self._settings.get("agent_web_timeout_seconds", 15)))
        self._agent_web_timeout.setSuffix(" 秒")
        web_form.addRow("最大结果数", self._agent_web_max_results)
        web_form.addRow("请求超时", self._agent_web_timeout)
        web_actions = QHBoxLayout()
        web_save = QPushButton("保存搜索配置")
        web_save.clicked.connect(self._save_agent_settings)
        web_test = QPushButton("测试搜索")
        web_test.clicked.connect(self._test_agent_web_search)
        web_actions.addWidget(web_save)
        web_actions.addWidget(web_test)
        web_form.addRow(web_actions)
        layout.addWidget(web_group)
        layout.addStretch()
        self._async_buttons["web_search"] = web_test
        page.setWidget(content)
        return page

    def _on_agent_mode_toggled(self, checked: bool) -> None:
        if not checked or self._updating_agent_mode:
            return
        requested = "agent" if self._agent_mode_radio.isChecked() else "classic"
        current = self._settings_manager.load().get("novel_generation_mode", "classic")
        if requested == current:
            return
        if self._mode_change_guard is not None:
            allowed, reason = self._mode_change_guard(requested)
            if not allowed:
                QMessageBox.warning(self, "无法切换写作模式", reason)
                self._updating_agent_mode = True
                self._agent_mode_radio.setChecked(current == "agent")
                self._classic_mode_radio.setChecked(current != "agent")
                self._updating_agent_mode = False
                return
        self._save_agent_settings()

    def _save_agent_settings(self) -> None:
        settings = self._settings_manager.load()
        mode = "agent" if self._agent_mode_radio.isChecked() else "classic"
        settings["novel_generation_mode"] = mode
        settings["controlled_agent_enabled"] = mode == "agent"
        settings["agent_skills_enabled"] = self._agent_skills.isChecked()
        settings["agent_multi_plan_enabled"] = self._agent_multi_plan.isChecked()
        settings["agent_web_enabled"] = self._agent_web.isChecked()
        settings["agent_web_endpoint"] = self._agent_web_endpoint.text().strip()
        settings["agent_web_method"] = self._agent_web_method.text().strip().upper() or "POST"
        settings["agent_web_api_key"] = self._agent_web_key.text().strip()
        settings["agent_web_auth_header"] = self._agent_web_auth_header.text().strip() or "Authorization"
        settings["agent_web_auth_prefix"] = self._agent_web_auth_prefix.text()
        settings["agent_web_query_field"] = self._agent_web_query_field.text().strip() or "query"
        settings["agent_web_results_path"] = self._agent_web_results_path.text().strip() or "results"
        settings["agent_web_title_field"] = self._agent_web_title_field.text().strip() or "title"
        settings["agent_web_url_field"] = self._agent_web_url_field.text().strip() or "url"
        settings["agent_web_snippet_field"] = self._agent_web_snippet_field.text().strip() or "content"
        settings["agent_web_max_results"] = self._agent_web_max_results.value()
        settings["agent_web_timeout_seconds"] = self._agent_web_timeout.value()
        settings["agent_runtime_backend"] = str(self._agent_runtime_backend.currentData() or "legacy")
        settings["retrieval_backend"] = str(self._retrieval_backend.currentData() or "classic")
        settings["embedding_base_url"] = self._embedding_base_url.text().strip()
        settings["embedding_api_key"] = self._embedding_api_key.text().strip()
        settings["embedding_model"] = self._embedding_model.text().strip()
        settings["framework_auto_fallback"] = self._framework_auto_fallback.isChecked()
        self._settings_manager.save(settings)
        self._settings = settings
        self._settings_changed_callback()

    def _test_embedding(self) -> None:
        self._save_agent_settings()
        self._framework_status.setText("正在后台测试 Embedding，请稍候……")

        parent = self.parent()
        manager = getattr(parent, "_novel_manager", None)
        if manager is None:
            QMessageBox.warning(self, "Embedding 测试失败", "当前窗口无法访问书籍管理器")
            return
        settings = self._settings_manager.load()

        def task():
            from core.retrieval import LlamaIndexHybridBackend
            backend = LlamaIndexHybridBackend(manager, settings)
            vector = backend._embedder.get_query_embedding("小说语义检索测试")
            return f"Embedding 测试成功，向量维度：{len(vector)}"

        self._run_async("embedding", task)

    def _current_retrieval_target(self):
        parent = self.parent()
        manager = getattr(parent, "_novel_manager", None)
        title = parent._get_current_book_title() if hasattr(parent, "_get_current_book_title") else ""
        if manager is None or not title:
            raise RuntimeError("请先在主界面选择一本小说")
        manager.configure_retrieval(self._settings_manager.load())
        return manager, title

    def _rebuild_retrieval_index(self) -> None:
        self._save_agent_settings()
        self._framework_status.setText("正在后台重建当前书籍索引……")
        try:
            manager, title = self._current_retrieval_target()
        except Exception as exc:
            QMessageBox.warning(self, "索引重建失败", str(exc))
            return

        def task():
            report = manager.retrieval_backend().rebuild(title)
            return (
                f"索引重建完成：{report.document_count} 个文档，"
                f"新增向量 {report.embedded_count}，revision={report.revision}"
            )

        self._run_async("rebuild", task)

    def _clear_retrieval_index(self) -> None:
        try:
            manager, title = self._current_retrieval_target()
            backend = manager.retrieval_backend()
            cleared = bool(getattr(backend, "clear", lambda _title: False)(title))
            self._framework_status.setText("当前书籍派生索引已清除。" if cleared else "当前后端没有可清除的派生索引。")
        except Exception as exc:
            QMessageBox.warning(self, "清除索引失败", str(exc))

    def _test_agent_web_search(self) -> None:
        self._save_agent_settings()
        self._framework_status.setText("正在后台测试联网搜索……")

        settings = self._settings_manager.load()

        def task():
            from core.agent.web_search import WebSearchClient, WebSearchConfig
            config = WebSearchConfig.from_settings(settings)
            response = WebSearchClient(config).search("小说创作素材测试", max_results=1)
            results = response.get("results", [])
            if results:
                return f"搜索测试成功：{results[0].get('title', '')}"
            return "搜索接口请求成功，但没有返回结果。"

        self._run_async("web_search", task)

    def _run_async(self, operation: str, task) -> None:
        button = self._async_buttons.get(operation)
        if button is not None:
            button.setEnabled(False)

        def worker():
            try:
                message = str(task())
                self._async_signals.completed.emit(operation, True, message)
            except Exception as exc:
                self._async_signals.completed.emit(operation, False, str(exc))

        threading.Thread(target=worker, daemon=True).start()

    def _on_async_completed(self, operation: str, ok: bool, message: str) -> None:
        button = self._async_buttons.get(operation)
        if button is not None:
            button.setEnabled(True)
        self._framework_status.setText(message if ok else f"操作失败：{message}")
        if not ok:
            title = {
                "embedding": "Embedding 测试失败",
                "rebuild": "索引重建失败",
                "web_search": "搜索测试失败",
            }.get(operation, "操作失败")
            QMessageBox.warning(self, title, message)
    def _refresh_model_and_preset_lists(self) -> None:
        self._settings = self._settings_manager.load()
        if hasattr(self, "_default_preset_combo"):
            selected = str(self._settings.get("current_preset") or "狂野")
            self._default_preset_combo.blockSignals(True)
            self._default_preset_combo.clear()
            self._default_preset_combo.addItems(list((self._settings.get("presets") or {}).keys()))
            self._default_preset_combo.setCurrentText(selected)
            self._default_preset_combo.blockSignals(False)
        self._refresh_default_style_profiles()
        self._preset_list.clear()
        self._preset_list.addItems(list((self._settings.get("presets") or {}).keys()))
        if self._preset_list.count():
            self._preset_list.setCurrentRow(0)

    def _load_selected_preset(self, name: str) -> None:
        preset = (self._settings.get("presets") or {}).get(name)
        if not preset:
            return
        self._preset_name.setText(name)
        self._preset_temp.setValue(int(preset.get("temp", 70)))
        self._preset_top_p.setValue(int(preset.get("top_p", 90)))
        self._preset_fp.setValue(int(preset.get("fp", 0)))
        self._preset_max_tokens.setValue(int(preset.get("max_tokens", 32768)))

    def _save_preset(self) -> None:
        name = self._preset_name.text().strip()
        if not name:
            QMessageBox.warning(self, "提示", "预设名称不能为空。")
            return
        settings = self._settings_manager.load()
        presets = deepcopy(settings.get("presets") or {})
        presets[name] = {
            "temp": self._preset_temp.value(),
            "top_p": self._preset_top_p.value(),
            "fp": self._preset_fp.value(),
            "max_tokens": self._preset_max_tokens.value(),
        }
        settings["presets"] = presets
        settings["current_preset"] = name
        self._settings_manager.save(settings)
        self._settings_changed_callback()
        self._refresh_model_and_preset_lists()

    def _delete_preset(self) -> None:
        name = self._preset_name.text().strip()
        if name in DEFAULT_PRESETS:
            QMessageBox.warning(self, "提示", "默认预设不能删除，可恢复默认值。")
            return
        settings = self._settings_manager.load()
        presets = deepcopy(settings.get("presets") or {})
        presets.pop(name, None)
        settings["presets"] = presets
        self._settings_manager.save(settings)
        self._settings_changed_callback()
        self._refresh_model_and_preset_lists()

    def _reset_presets(self) -> None:
        self._settings_manager.reset_presets()
        self._settings_changed_callback()
        self._refresh_model_and_preset_lists()

    def _save_theme(self) -> None:
        settings = self._settings_manager.load()
        settings["theme"] = "dark" if self._dark_theme.isChecked() else "light"
        self._settings_manager.save(settings)
        self._settings_changed_callback()

    def _password_strength_ok(self, password: str) -> bool:
        return len(password) >= 6 and any(c.isalpha() for c in password) and any(c.isdigit() for c in password)

    def _change_password(self) -> None:
        old = self._old_password.text()
        new = self._new_password.text()
        confirm = self._confirm_password.text()
        if new != confirm:
            QMessageBox.warning(self, "提示", "两次新密码输入不一致。")
            return
        if not self._password_strength_ok(new):
            QMessageBox.warning(self, "提示", "新密码至少 6 位，并同时包含字母和数字。")
            return
        reply = QMessageBox.question(
            self,
            "确认改密",
            "修改密码会重新加密当前用户全部数据。确定继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            new_key = self._auth.change_password(self._username, old, new)
        except AuthError as exc:
            QMessageBox.critical(self, "修改失败", str(exc))
            return
        self._password_changed_callback(new_key)
        QMessageBox.information(self, "完成", "密码已修改，数据已用新密码重新加密。")

    def _export_user_data(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "导出用户数据包", f"{self._username}_data.zip", "ZIP 文件 (*.zip)")
        if not path:
            return
        try:
            with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
                for root, _, files in os.walk(self._user_dir):
                    for fname in files:
                        fpath = os.path.join(root, fname)
                        zf.write(fpath, os.path.relpath(fpath, self._user_dir))
            QMessageBox.information(self, "导出完成", f"用户数据已导出到：\n{path}")
        except Exception as exc:
            QMessageBox.critical(self, "导出失败", str(exc))

    def _import_user_data(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "导入用户数据包", "", "ZIP 文件 (*.zip)")
        if not path:
            return
        reply = QMessageBox.question(
            self,
            "确认导入",
            "导入会覆盖同名用户数据文件。确定继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            with zipfile.ZipFile(path, "r") as zf:
                zf.extractall(self._user_dir)
            QMessageBox.information(self, "导入完成", "数据已导入。建议重启应用以完全刷新状态。")
        except Exception as exc:
            QMessageBox.critical(self, "导入失败", str(exc))

    def _clear_user_data(self) -> None:
        reply = QMessageBox.question(
            self,
            "确认清空",
            "此操作会删除当前用户的书架、对话、设置和日志，且不可恢复。确定继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        for name in ("bookshelf", "conversations"):
            path = os.path.join(self._user_dir, name)
            if os.path.isdir(path):
                shutil.rmtree(path)
            os.makedirs(path, exist_ok=True)
        for fname in ("settings.json", "settings.json.enc", "token_log.json", "token_log.json.enc"):
            fpath = os.path.join(self._user_dir, fname)
            if os.path.exists(fpath):
                os.remove(fpath)
        QMessageBox.information(self, "完成", "当前用户数据已清空。建议重启应用。")

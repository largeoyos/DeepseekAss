import json
import os
import shutil
import threading
import uuid
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
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
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
from core.model_config import STAGE_LABELS, ModelConfig, create_provider_template, sanitize_extra_body, validate_provider
from core.model_types import ModelProfile, ProviderProtocol, RoutePolicy, TaskStage
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
        model_config: dict | None = None,
        model_config_callback=None,
        book_routes_load_callback=None,
        book_routes_save_callback=None,
        model_test_callback=None,
        model_discover_callback=None,
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
        self._model_config = ModelConfig.from_dict(model_config or api_config, self._settings)
        self._model_config_callback = model_config_callback
        self._book_routes_load_callback = book_routes_load_callback
        self._book_routes_save_callback = book_routes_save_callback
        self._model_test_callback = model_test_callback
        self._model_discover_callback = model_discover_callback
        self._editing_provider_id = ""
        self._editing_model_id = ""
        self._route_scope = "global"
        self._book_routes: dict = {}
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
        tabs.addTab(self._build_api_tab(), "模型中心")
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

        # 模型默认值由“模型中心 → 任务路由”管理。保留控件对象仅用于读取旧偏好和插件兼容。
        self._default_model_combo.hide()
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
        layout.setSpacing(10)

        title = QLabel("多模型中心")
        title.setObjectName("apiTitle")
        subtitle = QLabel(
            "先建立服务连接，再定义模型能力，最后把六类写作任务路由到不同模型。"
            " API Key、自定义 Header 和高级 JSON 只保存在当前用户的加密配置中。"
        )
        subtitle.setObjectName("apiSubtitle")
        subtitle.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(subtitle)

        self._api_fields: dict[str, dict[str, QLineEdit]] = {}
        center_tabs = QTabWidget()
        center_tabs.addTab(self._build_provider_center(), "1  服务连接")
        center_tabs.addTab(self._build_model_profiles_center(), "2  模型档案")
        center_tabs.addTab(self._build_routes_center(), "3  任务路由")
        image_page = QWidget()
        image_layout = QVBoxLayout(image_page)
        image_layout.addWidget(self._build_api_section(
            "image", "图片 API", "图片生成仍保持独立配置；不使用时可全部留空。", required=False
        ))
        image_layout.addStretch()
        center_tabs.addTab(image_page, "图片 API")
        layout.addWidget(center_tabs, 1)

        actions = QHBoxLayout()
        risk = QLabel("跨服务回退时，当前任务上下文可能发送给备用服务。")
        risk.setStyleSheet("color: #9a5d00;")
        actions.addWidget(risk)
        actions.addStretch()
        save_btn = QPushButton("保存模型中心")
        save_btn.setObjectName("primaryButton")
        save_btn.clicked.connect(self._save_model_center)
        actions.addWidget(save_btn)
        layout.addLayout(actions)

        page.setStyleSheet("""
            QLabel#apiTitle { font-size: 22px; font-weight: 700; }
            QLabel#apiSubtitle { color: #5f6b76; margin-bottom: 4px; }
            QGroupBox#apiCard {
                border: 1px solid #cbd2d9; border-radius: 2px;
                margin-top: 12px; padding: 12px;
                font-size: 15px; font-weight: 700;
            }
            QGroupBox#apiCard::title { subcontrol-origin: margin; left: 14px; padding: 0 6px; }
            QPushButton#primaryButton { background: #166534; color: white; padding: 8px 18px; font-weight: 700; }
        """)
        self._refresh_provider_list()
        self._refresh_model_list()
        self._refresh_route_model_choices()
        return page

    def _build_provider_center(self) -> QWidget:
        page = QWidget()
        root = QHBoxLayout(page)
        left = QVBoxLayout()
        self._provider_list = QListWidget()
        self._provider_list.currentItemChanged.connect(self._on_provider_selected)
        left.addWidget(self._provider_list, 1)
        templates = QHBoxLayout()
        for label, template in (("+ DeepSeek", "deepseek"), ("+ Responses", "openai_responses"), ("+ 兼容", "generic"), ("+ Ollama", "ollama")):
            button = QPushButton(label)
            button.clicked.connect(lambda _=False, value=template: self._add_provider_template(value))
            templates.addWidget(button)
        left.addLayout(templates)
        delete_btn = QPushButton("删除服务")
        delete_btn.clicked.connect(self._delete_provider)
        left.addWidget(delete_btn)
        root.addLayout(left, 2)

        form = QFormLayout()
        self._provider_name = QLineEdit()
        self._provider_protocol = QComboBox()
        for protocol, label in (
            (ProviderProtocol.OPENAI_CHAT, "OpenAI Chat 兼容"),
            (ProviderProtocol.OPENAI_RESPONSES, "OpenAI Responses"),
            (ProviderProtocol.OLLAMA_NATIVE, "Ollama 原生"),
        ):
            self._provider_protocol.addItem(label, protocol.value)
        self._provider_url = QLineEdit()
        self._provider_key = QLineEdit()
        self._provider_key.setEchoMode(QLineEdit.EchoMode.Password)
        self._provider_auth_header = QLineEdit()
        self._provider_auth_prefix = QLineEdit()
        self._provider_timeout = QSpinBox(); self._provider_timeout.setRange(1, 3600)
        self._provider_retries = QSpinBox(); self._provider_retries.setRange(0, 10)
        self._provider_headers = QPlainTextEdit(); self._provider_headers.setMaximumHeight(72)
        self._provider_headers.setPlaceholderText('{"X-Project": "value"}')
        self._provider_enabled = QCheckBox("启用")
        self._provider_insecure = QCheckBox("允许非本机 HTTP（有明文传输风险）")
        form.addRow("服务名称", self._provider_name)
        form.addRow("协议", self._provider_protocol)
        form.addRow("调用地址", self._provider_url)
        form.addRow("API Key（可空）", self._provider_key)
        form.addRow("认证 Header", self._provider_auth_header)
        form.addRow("认证前缀", self._provider_auth_prefix)
        form.addRow("超时（秒）", self._provider_timeout)
        form.addRow("服务内重试", self._provider_retries)
        form.addRow("自定义 Headers JSON", self._provider_headers)
        form.addRow("", self._provider_enabled)
        form.addRow("", self._provider_insecure)
        root.addLayout(form, 3)
        return page

    def _build_model_profiles_center(self) -> QWidget:
        page = QWidget()
        root = QHBoxLayout(page)
        left = QVBoxLayout()
        self._model_profile_list = QListWidget()
        self._model_profile_list.currentItemChanged.connect(self._on_model_selected)
        left.addWidget(self._model_profile_list, 1)
        row = QHBoxLayout()
        add_btn = QPushButton("+ 模型档案"); add_btn.clicked.connect(self._add_model_profile)
        delete_btn = QPushButton("删除"); delete_btn.clicked.connect(self._delete_model_profile)
        row.addWidget(add_btn); row.addWidget(delete_btn)
        left.addLayout(row)
        test_btn = QPushButton("连接 / 能力测试"); test_btn.clicked.connect(self._test_selected_model)
        discover_btn = QPushButton("从服务获取模型列表"); discover_btn.clicked.connect(self._discover_models)
        left.addWidget(test_btn); left.addWidget(discover_btn)
        root.addLayout(left, 2)

        form = QFormLayout()
        self._model_provider = QComboBox()
        self._model_name = QLineEdit(); self._model_real_name = QLineEdit()
        self._model_context = QSpinBox(); self._model_context.setRange(512, 2_000_000)
        self._model_output = QSpinBox(); self._model_output.setRange(1, 500_000)
        self._model_runtime_context = QSpinBox(); self._model_runtime_context.setRange(0, 2_000_000)
        self._model_runtime_context.setSpecialValueText("不下发")
        self._model_runtime_context.setToolTip("仅 Ollama 原生下发 num_ctx；更大上下文会显著增加显存/内存占用。")
        self._model_template = QComboBox()
        for value, label in (("generic", "通用"), ("deepseek", "DeepSeek"), ("openai_responses", "Responses"), ("ollama", "Ollama")):
            self._model_template.addItem(label, value)
        capabilities = QWidget(); caps = QHBoxLayout(capabilities); caps.setContentsMargins(0, 0, 0, 0)
        self._cap_stream = QCheckBox("流式"); self._cap_tools = QCheckBox("工具")
        self._cap_search = QCheckBox("原生搜索"); self._cap_reasoning = QCheckBox("推理")
        self._cap_json = QCheckBox("JSON")
        for item in (self._cap_stream, self._cap_tools, self._cap_search, self._cap_reasoning, self._cap_json): caps.addWidget(item)
        self._model_reasoning_levels = QLineEdit(); self._model_reasoning_levels.setPlaceholderText("off, low, medium, high, xhigh, max")
        self._model_default_reasoning = QComboBox()
        for value in ("off", "low", "medium", "high", "xhigh", "max"): self._model_default_reasoning.addItem(value, value)
        self._model_default_search = QComboBox()
        for value, label in (("off", "关闭"), ("on_demand", "按需"), ("always", "总是")): self._model_default_search.addItem(label, value)
        params = QWidget(); params_layout = QHBoxLayout(params); params_layout.setContentsMargins(0, 0, 0, 0)
        self._param_temperature = QCheckBox("temperature"); self._param_top_p = QCheckBox("top_p")
        self._param_frequency = QCheckBox("frequency_penalty"); self._param_max_tokens = QCheckBox("max_tokens")
        for item in (self._param_temperature, self._param_top_p, self._param_frequency, self._param_max_tokens): params_layout.addWidget(item)
        self._model_default_temperature = QLineEdit(); self._model_default_top_p = QLineEdit(); self._model_default_frequency = QLineEdit()
        self._model_extra = QPlainTextEdit(); self._model_extra.setMaximumHeight(84); self._model_extra.setPlaceholderText("{}")
        self._model_enabled = QCheckBox("启用模型档案")
        form.addRow("所属服务", self._model_provider)
        form.addRow("档案名称", self._model_name)
        form.addRow("真实模型名", self._model_real_name)
        form.addRow("上下文窗口", self._model_context)
        form.addRow("最大输出", self._model_output)
        form.addRow("Ollama num_ctx", self._model_runtime_context)
        form.addRow("请求模板", self._model_template)
        form.addRow("能力", capabilities)
        form.addRow("支持的推理等级", self._model_reasoning_levels)
        form.addRow("默认推理", self._model_default_reasoning)
        form.addRow("默认联网", self._model_default_search)
        form.addRow("发送参数", params)
        defaults = QWidget(); defaults_layout = QHBoxLayout(defaults); defaults_layout.setContentsMargins(0, 0, 0, 0)
        for label, field in (("temp", self._model_default_temperature), ("top_p", self._model_default_top_p), ("freq", self._model_default_frequency)):
            defaults_layout.addWidget(QLabel(label)); defaults_layout.addWidget(field)
        form.addRow("档案默认参数", defaults)
        form.addRow("高级请求 JSON", self._model_extra)
        form.addRow("", self._model_enabled)
        root.addLayout(form, 3)
        return page

    def _build_routes_center(self) -> QWidget:
        page = QScrollArea(); page.setWidgetResizable(True)
        content = QWidget(); root = QVBoxLayout(content)
        scope_row = QHBoxLayout()
        self._route_scope_combo = QComboBox()
        self._route_scope_combo.addItem("用户默认", "global")
        book = ""
        parent = self.parent()
        if parent is not None and hasattr(parent, "_get_current_book_title"):
            book = parent._get_current_book_title() or ""
        self._route_book_title = book
        self._route_scope_combo.addItem(f"当前书籍：{book or '未选择'}", "book")
        self._route_scope_combo.currentIndexChanged.connect(self._on_route_scope_changed)
        scope_row.addWidget(QLabel("作用域")); scope_row.addWidget(self._route_scope_combo); scope_row.addStretch()
        root.addLayout(scope_row)
        self._route_controls: dict[str, dict] = {}
        for stage in TaskStage:
            group = QGroupBox(STAGE_LABELS[stage]); group.setObjectName("apiCard")
            form = QFormLayout(group)
            inherit = QCheckBox("继承用户默认（仅当前书籍）")
            primary = QComboBox()
            fallback = QLineEdit(); fallback.setPlaceholderText("按顺序填档案名，逗号分隔")
            reasoning = QComboBox(); reasoning.addItem("继承档案", "")
            for value in ("off", "low", "medium", "high", "xhigh", "max"): reasoning.addItem(value, value)
            web_search = QComboBox(); web_search.addItem("继承档案", "")
            for value, label in (("off", "关闭"), ("on_demand", "按需"), ("always", "总是")): web_search.addItem(label, value)
            context = QSpinBox(); context.setRange(0, 2_000_000); context.setSpecialValueText("继承档案")
            output = QSpinBox(); output.setRange(0, 500_000); output.setSpecialValueText("继承档案")
            form.addRow("", inherit); form.addRow("主模型", primary); form.addRow("备用链", fallback)
            form.addRow("推理等级", reasoning); form.addRow("联网策略", web_search)
            form.addRow("上下文上限", context); form.addRow("输出上限", output)
            self._route_controls[stage.value] = {
                "inherit": inherit, "primary": primary, "fallback": fallback,
                "reasoning": reasoning, "web_search": web_search, "context": context, "output": output,
            }
            root.addWidget(group)
        root.addStretch(); page.setWidget(content)
        return page

    @staticmethod
    def _set_combo_data(combo: QComboBox, value) -> None:
        index = combo.findData(value)
        combo.setCurrentIndex(index if index >= 0 else 0)

    def _refresh_provider_list(self, selected_id: str = "") -> None:
        if not hasattr(self, "_provider_list"):
            return
        selected_id = selected_id or self._editing_provider_id
        self._provider_list.blockSignals(True)
        self._provider_list.clear()
        for provider in self._model_config.providers.values():
            item = QListWidgetItem(f"{provider.name}\n{provider.protocol.value}")
            item.setData(256, provider.provider_id)
            self._provider_list.addItem(item)
        self._provider_list.blockSignals(False)
        for index in range(self._provider_list.count()):
            if self._provider_list.item(index).data(256) == selected_id:
                self._provider_list.setCurrentRow(index)
                break
        else:
            if self._provider_list.count(): self._provider_list.setCurrentRow(0)

    def _on_provider_selected(self, current, _previous=None) -> None:
        if current is None:
            return
        if self._editing_provider_id:
            self._store_provider_editor(show_errors=False)
        provider_id = str(current.data(256) or "")
        provider = self._model_config.providers.get(provider_id)
        if not provider:
            return
        self._editing_provider_id = provider_id
        self._provider_name.setText(provider.name)
        self._set_combo_data(self._provider_protocol, provider.protocol.value)
        self._provider_url.setText(provider.base_url)
        self._provider_key.setText(provider.api_key)
        self._provider_auth_header.setText(provider.auth_header)
        self._provider_auth_prefix.setText(provider.auth_prefix)
        self._provider_timeout.setValue(int(provider.timeout_seconds))
        self._provider_retries.setValue(int(provider.max_retries))
        self._provider_headers.setPlainText(json.dumps(provider.headers, ensure_ascii=False, indent=2))
        self._provider_enabled.setChecked(provider.enabled)
        self._provider_insecure.setChecked(provider.allow_insecure_http)

    def _store_provider_editor(self, *, show_errors: bool) -> bool:
        provider = self._model_config.providers.get(self._editing_provider_id)
        if not provider:
            return True
        try:
            headers = json.loads(self._provider_headers.toPlainText().strip() or "{}")
            if not isinstance(headers, dict):
                raise ValueError("Headers JSON 必须是对象")
        except Exception as exc:
            if show_errors: QMessageBox.warning(self, "Headers JSON 无效", str(exc))
            return False
        provider.name = self._provider_name.text().strip() or provider.name
        provider.protocol = ProviderProtocol(str(self._provider_protocol.currentData()))
        provider.base_url = self._provider_url.text().strip().rstrip("/")
        provider.api_key = self._provider_key.text().strip()
        provider.auth_header = self._provider_auth_header.text().strip() or "Authorization"
        provider.auth_prefix = self._provider_auth_prefix.text()
        provider.timeout_seconds = float(self._provider_timeout.value())
        provider.max_retries = self._provider_retries.value()
        provider.headers = {str(key): str(value) for key, value in headers.items()}
        provider.enabled = self._provider_enabled.isChecked()
        provider.allow_insecure_http = self._provider_insecure.isChecked()
        return True

    def _add_provider_template(self, template: str) -> None:
        self._store_provider_editor(show_errors=False)
        provider, profile = create_provider_template(template)
        provider.provider_id = f"provider_{uuid.uuid4().hex[:12]}"
        profile.provider_id = provider.provider_id
        profile.model_id = f"model_{uuid.uuid4().hex[:12]}"
        self._model_config.providers[provider.provider_id] = provider
        self._model_config.models[profile.model_id] = profile
        self._editing_provider_id = provider.provider_id
        self._refresh_provider_list(provider.provider_id)
        self._refresh_model_list(profile.model_id)
        self._refresh_route_model_choices()

    def _delete_provider(self) -> None:
        provider_id = self._editing_provider_id
        provider = self._model_config.providers.get(provider_id)
        if not provider:
            return
        if QMessageBox.question(self, "删除服务", f"删除「{provider.name}」及其全部模型档案？") != QMessageBox.StandardButton.Yes:
            return
        model_ids = {item.model_id for item in self._model_config.models.values() if item.provider_id == provider_id}
        self._model_config.providers.pop(provider_id, None)
        for model_id in model_ids: self._model_config.models.pop(model_id, None)
        self._editing_provider_id = ""; self._editing_model_id = ""
        self._model_config.ensure_valid_routes()
        self._refresh_provider_list(); self._refresh_model_list(); self._refresh_route_model_choices()

    def _refresh_model_list(self, selected_id: str = "") -> None:
        if not hasattr(self, "_model_profile_list"):
            return
        selected_id = selected_id or self._editing_model_id
        self._model_profile_list.blockSignals(True)
        self._model_profile_list.clear()
        self._model_provider.blockSignals(True); self._model_provider.clear()
        for provider in self._model_config.providers.values():
            self._model_provider.addItem(provider.name, provider.provider_id)
        self._model_provider.blockSignals(False)
        for profile in self._model_config.models.values():
            provider = self._model_config.providers.get(profile.provider_id)
            item = QListWidgetItem(f"{profile.name}\n{provider.name if provider else '服务已删除'}")
            item.setData(256, profile.model_id)
            self._model_profile_list.addItem(item)
        self._model_profile_list.blockSignals(False)
        for index in range(self._model_profile_list.count()):
            if self._model_profile_list.item(index).data(256) == selected_id:
                self._model_profile_list.setCurrentRow(index); break
        else:
            if self._model_profile_list.count(): self._model_profile_list.setCurrentRow(0)

    def _on_model_selected(self, current, _previous=None) -> None:
        if current is None:
            return
        if self._editing_model_id:
            self._store_model_editor(show_errors=False)
        model_id = str(current.data(256) or "")
        profile = self._model_config.models.get(model_id)
        if not profile:
            return
        self._editing_model_id = model_id
        self._set_combo_data(self._model_provider, profile.provider_id)
        self._model_name.setText(profile.name); self._model_real_name.setText(profile.model)
        self._model_context.setValue(profile.context_window); self._model_output.setValue(profile.max_output_tokens)
        self._model_runtime_context.setValue(int(profile.runtime_context or 0))
        self._set_combo_data(self._model_template, profile.template)
        caps = profile.capabilities
        self._cap_stream.setChecked(caps.streaming); self._cap_tools.setChecked(caps.tools)
        self._cap_search.setChecked(caps.native_web_search); self._cap_reasoning.setChecked(caps.reasoning)
        self._cap_json.setChecked(caps.structured_output)
        self._model_reasoning_levels.setText(", ".join(caps.supported_reasoning_levels or ["off"]))
        self._set_combo_data(self._model_default_reasoning, profile.defaults.reasoning_level)
        self._set_combo_data(self._model_default_search, profile.defaults.web_search)
        support = profile.parameter_support
        self._param_temperature.setChecked(bool(support.get("temperature", True)))
        self._param_top_p.setChecked(bool(support.get("top_p", True)))
        self._param_frequency.setChecked(bool(support.get("frequency_penalty", True)))
        self._param_max_tokens.setChecked(bool(support.get("max_tokens", True)))
        self._model_default_temperature.setText("" if profile.defaults.temperature is None else str(profile.defaults.temperature))
        self._model_default_top_p.setText("" if profile.defaults.top_p is None else str(profile.defaults.top_p))
        self._model_default_frequency.setText("" if profile.defaults.frequency_penalty is None else str(profile.defaults.frequency_penalty))
        self._model_extra.setPlainText(json.dumps(profile.extra_body, ensure_ascii=False, indent=2))
        self._model_enabled.setChecked(profile.enabled)

    @staticmethod
    def _optional_float(text: str) -> float | None:
        return None if not str(text or "").strip() else float(text)

    def _store_model_editor(self, *, show_errors: bool) -> bool:
        profile = self._model_config.models.get(self._editing_model_id)
        if not profile:
            return True
        try:
            extra = json.loads(self._model_extra.toPlainText().strip() or "{}")
            if not isinstance(extra, dict): raise ValueError("高级 JSON 必须是对象")
            temperature = self._optional_float(self._model_default_temperature.text())
            top_p = self._optional_float(self._model_default_top_p.text())
            frequency = self._optional_float(self._model_default_frequency.text())
        except Exception as exc:
            if show_errors: QMessageBox.warning(self, "模型档案无效", str(exc))
            return False
        profile.provider_id = str(self._model_provider.currentData() or profile.provider_id)
        profile.name = self._model_name.text().strip() or profile.name
        profile.model = self._model_real_name.text().strip() or profile.model
        profile.context_window = self._model_context.value(); profile.max_output_tokens = self._model_output.value()
        profile.runtime_context = self._model_runtime_context.value() or None
        profile.template = str(self._model_template.currentData() or "generic")
        caps = profile.capabilities
        caps.streaming = self._cap_stream.isChecked(); caps.tools = self._cap_tools.isChecked()
        caps.native_web_search = self._cap_search.isChecked(); caps.reasoning = self._cap_reasoning.isChecked()
        caps.structured_output = self._cap_json.isChecked()
        caps.supported_reasoning_levels = [item.strip() for item in self._model_reasoning_levels.text().split(",") if item.strip()] or ["off"]
        profile.defaults.reasoning_level = str(self._model_default_reasoning.currentData() or "off")
        profile.defaults.web_search = str(self._model_default_search.currentData() or "off")
        profile.defaults.temperature = temperature; profile.defaults.top_p = top_p; profile.defaults.frequency_penalty = frequency
        profile.parameter_support = {
            "temperature": self._param_temperature.isChecked(), "top_p": self._param_top_p.isChecked(),
            "frequency_penalty": self._param_frequency.isChecked(), "max_tokens": self._param_max_tokens.isChecked(),
        }
        profile.extra_body = sanitize_extra_body(extra); profile.enabled = self._model_enabled.isChecked()
        return True

    def _add_model_profile(self) -> None:
        self._store_model_editor(show_errors=False)
        provider_id = str(self._model_provider.currentData() or next(iter(self._model_config.providers), ""))
        if not provider_id:
            QMessageBox.warning(self, "缺少服务", "请先新建服务连接。"); return
        model_id = f"model_{uuid.uuid4().hex[:12]}"
        profile = ModelProfile(model_id, provider_id, "新模型", "model-name")
        self._model_config.models[model_id] = profile
        self._editing_model_id = model_id
        self._refresh_model_list(model_id); self._refresh_route_model_choices()

    def _delete_model_profile(self) -> None:
        profile = self._model_config.models.get(self._editing_model_id)
        if not profile:
            return
        if QMessageBox.question(self, "删除模型", f"删除档案「{profile.name}」？") != QMessageBox.StandardButton.Yes:
            return
        self._model_config.models.pop(profile.model_id, None); self._editing_model_id = ""
        self._model_config.ensure_valid_routes(); self._refresh_model_list(); self._refresh_route_model_choices()

    def _test_selected_model(self) -> None:
        if not self._store_provider_editor(show_errors=True) or not self._store_model_editor(show_errors=True): return
        if not self._model_test_callback:
            QMessageBox.information(self, "能力测试", "请先保存配置后，再使用主窗口的连接测试。"); return
        if QMessageBox.question(
            self, "运行能力测试",
            "将实际发起普通、流式、推理和工具请求；若档案声明原生搜索，还会发起一次联网请求。"
            "这可能产生费用和外部数据传输。\n\n继续吗？",
        ) != QMessageBox.StandardButton.Yes:
            return
        ok, message = self._model_test_callback(self._model_config.to_dict(), self._editing_model_id)
        (QMessageBox.information if ok else QMessageBox.critical)(self, "能力测试", message)

    def _discover_models(self) -> None:
        if not self._store_provider_editor(show_errors=True) or not self._store_model_editor(show_errors=True): return
        if not self._model_discover_callback:
            QMessageBox.information(self, "模型发现", "当前运行环境未提供模型发现回调。"); return
        ok, result = self._model_discover_callback(self._model_config.to_dict(), self._editing_model_id)
        if not ok:
            QMessageBox.critical(self, "模型发现失败", str(result)); return
        names = list(result or [])
        if not names:
            QMessageBox.information(self, "模型发现", "服务未返回模型。"); return
        QMessageBox.information(self, "已发现模型", "\n".join(str(item) for item in names[:100]))

    def _refresh_route_model_choices(self) -> None:
        if not hasattr(self, "_route_controls"):
            return
        profiles = list(self._model_config.models.values())
        for controls in self._route_controls.values():
            selected = controls["primary"].currentData()
            controls["primary"].clear()
            for profile in profiles: controls["primary"].addItem(profile.name, profile.model_id)
            self._set_combo_data(controls["primary"], selected)
        self._load_routes_into_controls()

    def _load_routes_into_controls(self) -> None:
        if not hasattr(self, "_route_controls"):
            return
        source = (
            {key: value.to_dict() for key, value in self._model_config.routes.items()}
            if self._route_scope == "global" else self._book_routes
        )
        names = {profile.model_id: profile.name for profile in self._model_config.models.values()}
        for stage, controls in self._route_controls.items():
            route = RoutePolicy.from_dict(source.get(stage))
            controls["inherit"].setChecked(route.inherit if self._route_scope == "book" else False)
            controls["inherit"].setEnabled(self._route_scope == "book")
            self._set_combo_data(controls["primary"], route.primary_model_id)
            controls["fallback"].setText(", ".join(names.get(item, item) for item in route.fallback_model_ids))
            self._set_combo_data(controls["reasoning"], route.overrides.reasoning_level or "")
            self._set_combo_data(controls["web_search"], route.overrides.web_search or "")
            controls["context"].setValue(int(route.overrides.context_window or 0))
            controls["output"].setValue(int(route.overrides.max_output_tokens or 0))

    def _collect_routes(self) -> dict:
        by_label = {}
        for profile in self._model_config.models.values():
            by_label[profile.model_id] = profile.model_id; by_label[profile.name] = profile.model_id; by_label[profile.model] = profile.model_id
        result = {}
        for stage, controls in self._route_controls.items():
            fallback_ids = []
            for value in controls["fallback"].text().replace("，", ",").split(","):
                resolved = by_label.get(value.strip())
                if resolved and resolved not in fallback_ids: fallback_ids.append(resolved)
            primary = str(controls["primary"].currentData() or "")
            fallback_ids = [item for item in fallback_ids if item != primary]
            result[stage] = RoutePolicy.from_dict({
                "inherit": controls["inherit"].isChecked() if self._route_scope == "book" else False,
                "primary_model_id": primary,
                "fallback_model_ids": fallback_ids,
                "overrides": {
                    "reasoning_level": controls["reasoning"].currentData() or None,
                    "web_search": controls["web_search"].currentData() or None,
                    "context_window": controls["context"].value() or None,
                    "max_output_tokens": controls["output"].value() or None,
                },
            }).to_dict()
        return result

    def _on_route_scope_changed(self, *_args) -> None:
        if hasattr(self, "_route_controls"):
            collected = self._collect_routes()
            if self._route_scope == "global":
                self._model_config.routes = {key: RoutePolicy.from_dict(value) for key, value in collected.items()}
            else:
                self._book_routes = collected
        self._route_scope = str(self._route_scope_combo.currentData() or "global")
        if self._route_scope == "book":
            if not self._route_book_title:
                QMessageBox.warning(self, "未选择书籍", "请先在主界面选择一本书。")
                self._route_scope_combo.setCurrentIndex(0); return
            if self._book_routes_load_callback:
                self._book_routes = dict(self._book_routes_load_callback(self._route_book_title) or {})
        self._load_routes_into_controls()

    def _save_model_center(self) -> None:
        if not self._store_provider_editor(show_errors=True) or not self._store_model_editor(show_errors=True): return
        collected = self._collect_routes()
        if self._route_scope == "global":
            self._model_config.routes = {key: RoutePolicy.from_dict(value) for key, value in collected.items()}
        else:
            self._book_routes = collected
        errors = []
        for provider in self._model_config.providers.values(): errors.extend(f"{provider.name}：{item}" for item in validate_provider(provider))
        for stage in TaskStage:
            route = self._model_config.routes.get(stage.value)
            if not route or route.primary_model_id not in self._model_config.models:
                errors.append(f"{STAGE_LABELS[stage]}：请选择主模型"); continue
            profile = self._model_config.models[route.primary_model_id]
            reasoning = route.overrides.reasoning_level
            if reasoning and reasoning not in set(profile.capabilities.supported_reasoning_levels or ["off"]):
                errors.append(f"{STAGE_LABELS[stage]}：{profile.name} 不支持 {reasoning} 推理")
        if self._book_routes:
            for stage in TaskStage:
                route = RoutePolicy.from_dict(self._book_routes.get(stage.value))
                if route.inherit:
                    continue
                profile = self._model_config.models.get(route.primary_model_id)
                if profile is None:
                    errors.append(f"当前书籍 / {STAGE_LABELS[stage]}：主模型无效")
                    continue
                reasoning = route.overrides.reasoning_level
                if reasoning and reasoning not in set(profile.capabilities.supported_reasoning_levels or ["off"]):
                    errors.append(f"当前书籍 / {STAGE_LABELS[stage]}：{profile.name} 不支持 {reasoning} 推理")
        image_config = self._api_values("image")
        if any(image_config.values()) and not all(image_config.values()): errors.append("图片 API 需要同时填写地址、Key 和模型")
        if errors:
            QMessageBox.warning(self, "配置无效", "\n".join(errors)); return
        self._model_config.image = image_config
        try:
            if self._model_config_callback:
                self._model_config_callback(self._model_config.to_dict())
            else:
                self._api_config_callback({"text": self._model_config.legacy_text_config(), "image": image_config})
            if self._book_routes_save_callback and self._route_book_title and self._book_routes:
                self._book_routes_save_callback(self._route_book_title, self._book_routes)
        except Exception as exc:
            QMessageBox.critical(self, "保存失败", str(exc)); return
        self._api_config = {"text": self._model_config.legacy_text_config(), "image": image_config}
        QMessageBox.information(self, "已保存", "服务连接、模型档案和任务路由已加密保存并立即生效。")

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

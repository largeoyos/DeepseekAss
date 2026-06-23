import os
import shutil
import zipfile
from copy import deepcopy

from PyQt6.QtWidgets import (
    QButtonGroup,
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
    QPushButton,
    QRadioButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from core.auth_manager import AuthError, AuthManager
from core.settings_manager import DEFAULT_PRESETS, SettingsManager


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

        self.setWindowTitle("设置中心")
        self.resize(760, 620)
        self._init_ui()

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        tabs = QTabWidget()
        tabs.addTab(self._build_api_tab(), "API 与模型")
        tabs.addTab(self._build_models_tab(), "生成参数")
        tabs.addTab(self._build_account_tab(), "账号安全")
        tabs.addTab(self._build_data_tab(), "数据管理")
        tabs.addTab(self._build_appearance_tab(), "外观")
        tabs.addTab(self._build_agent_tab(), "Agent")
        layout.addWidget(tabs, stretch=1)

        row = QHBoxLayout()
        row.addStretch()
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.accept)
        row.addWidget(close_btn)
        layout.addLayout(row)

    def _build_models_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

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

        self._refresh_model_and_preset_lists()
        return page

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

    def _build_agent_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
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
        self._agent_web = QCheckBox("启用网页搜索工具（当前版本预留）")
        self._agent_web.setChecked(bool(self._settings.get("agent_web_enabled", False)))
        self._agent_web.setEnabled(False)
        layout.addWidget(self._agent_web)
        layout.addStretch()
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
        settings["agent_web_enabled"] = False
        self._settings_manager.save(settings)
        self._settings = settings
        self._settings_changed_callback()

    def _refresh_model_and_preset_lists(self) -> None:
        self._settings = self._settings_manager.load()
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

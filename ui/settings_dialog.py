import os
import shutil
import zipfile
from copy import deepcopy

from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
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
        api_key_callback,
        settings_changed_callback,
        password_changed_callback,
    ):
        super().__init__(parent)
        self._settings_manager = settings_manager
        self._settings = settings_manager.load()
        self._auth = auth
        self._username = username
        self._user_dir = user_dir
        self._encrypted = encrypted
        self._api_key_callback = api_key_callback
        self._settings_changed_callback = settings_changed_callback
        self._password_changed_callback = password_changed_callback

        self.setWindowTitle("设置中心")
        self.resize(680, 520)
        self._init_ui()

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        tabs = QTabWidget()
        tabs.addTab(self._build_models_tab(), "模型与参数")
        tabs.addTab(self._build_api_tab(), "API Key")
        tabs.addTab(self._build_account_tab(), "账号安全")
        tabs.addTab(self._build_data_tab(), "数据管理")
        tabs.addTab(self._build_appearance_tab(), "外观")
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

        model_group = QGroupBox("模型列表")
        model_layout = QVBoxLayout(model_group)
        self._model_list = QListWidget()
        model_layout.addWidget(self._model_list)
        row = QHBoxLayout()
        add_btn = QPushButton("添加模型")
        add_btn.clicked.connect(self._add_model)
        remove_btn = QPushButton("移除选中")
        remove_btn.clicked.connect(self._remove_model)
        row.addWidget(add_btn)
        row.addWidget(remove_btn)
        model_layout.addLayout(row)
        layout.addWidget(model_group)

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
        label = QLabel("API Key 会保存在当前用户的加密配置中。")
        label.setWordWrap(True)
        layout.addWidget(label)
        btn = QPushButton("修改 API Key")
        btn.clicked.connect(self._api_key_callback)
        layout.addWidget(btn)
        layout.addStretch()
        return page

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

    def _refresh_model_and_preset_lists(self) -> None:
        self._settings = self._settings_manager.load()
        self._model_list.clear()
        models = []
        for key in ("favorite_models", "custom_models"):
            for model in self._settings.get(key, []) or []:
                if model and model not in models:
                    models.append(model)
        self._model_list.addItems(models)

        self._preset_list.clear()
        self._preset_list.addItems(list((self._settings.get("presets") or {}).keys()))
        if self._preset_list.count():
            self._preset_list.setCurrentRow(0)

    def _add_model(self) -> None:
        model, ok = QInputDialog.getText(self, "添加模型", "模型名称：")
        if not ok or not model.strip():
            return
        settings = self._settings_manager.load()
        custom = settings.setdefault("custom_models", [])
        if model.strip() not in custom and model.strip() not in settings.get("favorite_models", []):
            custom.append(model.strip())
        settings["last_model"] = model.strip()
        self._settings_manager.save(settings)
        self._settings_changed_callback()
        self._refresh_model_and_preset_lists()

    def _remove_model(self) -> None:
        item = self._model_list.currentItem()
        if not item:
            return
        model = item.text()
        settings = self._settings_manager.load()
        for key in ("favorite_models", "custom_models"):
            settings[key] = [m for m in settings.get(key, []) if m != model]
        self._settings_manager.save(settings)
        self._settings_changed_callback()
        self._refresh_model_and_preset_lists()

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

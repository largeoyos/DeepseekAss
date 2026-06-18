from dataclasses import asdict

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from core.chat_domain import SenderProfile, new_id, now_text


class SenderProfileEditDialog(QDialog):
    def __init__(self, parent=None, profile: SenderProfile | None = None):
        super().__init__(parent)
        self.profile = profile or SenderProfile()
        self.setWindowTitle("玩家身份")
        self.resize(520, 600)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.fields = {}
        for key, label, multiline in (
            ("name", "名称", False),
            ("identity", "身份", False),
            ("personality", "性格", True),
            ("appearance", "外貌", True),
            ("background", "背景", True),
            ("relationships", "关系说明", True),
            ("knowledge_state", "已知信息", True),
            ("notes", "补充设定", True),
        ):
            widget = QTextEdit() if multiline else QLineEdit()
            value = getattr(self.profile, key)
            if multiline:
                widget.setPlainText(value)
                widget.setMaximumHeight(70)
            else:
                widget.setText(value)
            self.fields[key] = widget
            form.addRow(label, widget)
        layout.addLayout(form)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def result_profile(self) -> SenderProfile:
        for key, widget in self.fields.items():
            value = widget.toPlainText().strip() if isinstance(widget, QTextEdit) else widget.text().strip()
            setattr(self.profile, key, value)
        self.profile.sender_profile_id = self.profile.sender_profile_id or new_id("sender")
        self.profile.created_at = self.profile.created_at or now_text()
        self.profile.updated_at = now_text()
        return self.profile


class ChatControlDialog(QDialog):
    def __init__(
        self,
        parent,
        state,
        character_book,
        participant_ids,
        sender_profiles,
        apply_change_callback,
        modify_change_callback,
        reject_change_callback,
        revert_change_callback,
        switch_branch_callback,
        fork_branch_callback,
        message_operation_callback,
    ):
        super().__init__(parent)
        self.state = state
        self.character_book = character_book
        self.participant_ids = list(participant_ids)
        self.sender_profiles = sender_profiles
        self.apply_change_callback = apply_change_callback
        self.modify_change_callback = modify_change_callback
        self.reject_change_callback = reject_change_callback
        self.revert_change_callback = revert_change_callback
        self.switch_branch_callback = switch_branch_callback
        self.fork_branch_callback = fork_branch_callback
        self.message_operation_callback = message_operation_callback
        self.setWindowTitle("会话控制中心")
        self.resize(880, 680)

        layout = QVBoxLayout(self)
        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_identity_tab(), "玩家身份")
        self.tabs.addTab(self._build_scene_tab(), "场景")
        self.tabs.addTab(self._build_policy_tab(), "发言策略")
        self.tabs.addTab(self._build_branch_tab(), "分支")
        self.tabs.addTab(self._build_review_tab(), "记忆审核")
        self.tabs.addTab(self._build_messages_tab(), "消息操作")
        self.tabs.addTab(self._build_overview_tab(), "关系与知识")
        layout.addWidget(self.tabs)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Close
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).clicked.connect(self._save_controls)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _build_identity_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        self.sender_combo = QComboBox()
        layout.addWidget(QLabel("当前会话绑定的玩家身份"))
        layout.addWidget(self.sender_combo)
        row = QHBoxLayout()
        add_btn = QPushButton("新建身份")
        edit_btn = QPushButton("编辑身份")
        add_btn.clicked.connect(self._add_sender)
        edit_btn.clicked.connect(self._edit_sender)
        row.addWidget(add_btn)
        row.addWidget(edit_btn)
        row.addStretch()
        layout.addLayout(row)
        self.sender_detail = QTextEdit()
        self.sender_detail.setReadOnly(True)
        self.sender_combo.currentIndexChanged.connect(self._render_sender)
        layout.addWidget(self.sender_detail)
        self._refresh_senders()
        return tab

    def _refresh_senders(self):
        self.sender_combo.blockSignals(True)
        self.sender_combo.clear()
        self.sender_combo.addItem("临时身份", "")
        for profile in self.sender_profiles:
            self.sender_combo.addItem(profile.name or "未命名身份", profile.sender_profile_id)
        index = self.sender_combo.findData(self.state.sender_profile_id)
        self.sender_combo.setCurrentIndex(max(0, index))
        self.sender_combo.blockSignals(False)
        self._render_sender()

    def _current_sender(self):
        profile_id = self.sender_combo.currentData()
        return next((item for item in self.sender_profiles if item.sender_profile_id == profile_id), None)

    def _render_sender(self):
        profile = self._current_sender()
        if not profile:
            self.sender_detail.setPlainText("使用主界面中的临时发送者信息。")
            return
        lines = [f"{key}: {value}" for key, value in asdict(profile).items() if value and key not in ("sender_profile_id", "created_at", "updated_at")]
        self.sender_detail.setPlainText("\n".join(lines))

    def _add_sender(self):
        dialog = SenderProfileEditDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        profile = dialog.result_profile()
        if not profile.name:
            QMessageBox.warning(self, "缺少名称", "玩家身份名称不能为空。")
            return
        self.sender_profiles.append(profile)
        self._refresh_senders()
        self.sender_combo.setCurrentIndex(self.sender_combo.findData(profile.sender_profile_id))

    def _edit_sender(self):
        profile = self._current_sender()
        if not profile:
            return
        dialog = SenderProfileEditDialog(self, profile)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            dialog.result_profile()
            self._refresh_senders()
            self.sender_combo.setCurrentIndex(self.sender_combo.findData(profile.sender_profile_id))

    def _build_scene_tab(self):
        tab = QWidget()
        form = QFormLayout(tab)
        self.scene_time = QLineEdit(self.state.scene_state.time)
        self.scene_location = QLineEdit(self.state.scene_state.location)
        self.scene_weather = QLineEdit(self.state.scene_state.weather)
        self.scene_objective = QLineEdit(self.state.scene_state.objective)
        self.scene_description = QTextEdit(self.state.scene_state.description)
        self.scene_description.setMaximumHeight(100)
        self.scene_tags = QLineEdit("、".join(self.state.scene_state.tags))
        self.scene_present = QListWidget()
        self.scene_present.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
        for profile in self.character_book.profiles:
            if profile.character_id not in self.participant_ids:
                continue
            item = QListWidgetItem(profile.name)
            item.setData(Qt.ItemDataRole.UserRole, profile.character_id)
            item.setSelected(profile.character_id in self.state.scene_state.present_character_ids)
            self.scene_present.addItem(item)
        form.addRow("时间", self.scene_time)
        form.addRow("地点", self.scene_location)
        form.addRow("天气", self.scene_weather)
        form.addRow("场景目标", self.scene_objective)
        form.addRow("环境描述", self.scene_description)
        form.addRow("状态标签", self.scene_tags)
        form.addRow("在场角色", self.scene_present)
        return tab

    def _character_selector(self, selected_ids):
        widget = QListWidget()
        widget.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
        for profile in self.character_book.profiles:
            if profile.character_id not in self.participant_ids:
                continue
            item = QListWidgetItem(profile.name)
            item.setData(Qt.ItemDataRole.UserRole, profile.character_id)
            item.setSelected(profile.character_id in selected_ids)
            widget.addItem(item)
        return widget

    def _selected_ids(self, widget):
        return [item.data(Qt.ItemDataRole.UserRole) for item in widget.selectedItems()]

    def _build_policy_tab(self):
        tab = QWidget()
        form = QFormLayout(tab)
        policy = self.state.turn_policy
        self.policy_required = self._character_selector(policy.required_speaker_ids)
        self.policy_allowed = self._character_selector(policy.allowed_speaker_ids)
        self.policy_blocked = self._character_selector(policy.blocked_speaker_ids)
        self.policy_mention = self._character_selector(policy.mention_only_ids)
        name_by_id = {
            profile.character_id: profile.name for profile in self.character_book.profiles
        }
        self.policy_order = QLineEdit(
            "、".join(name_by_id.get(value, value) for value in policy.speaker_order)
        )
        self.policy_max = QSpinBox()
        self.policy_max.setRange(0, max(1, len(self.participant_ids)))
        self.policy_max.setValue(policy.max_speakers)
        self.narrator_check = QCheckBox("启用独立旁白主持")
        self.narrator_check.setChecked(self.state.narrator_enabled)
        form.addRow("必须回复", self.policy_required)
        form.addRow("允许回复（空=全部）", self.policy_allowed)
        form.addRow("禁止回复", self.policy_blocked)
        form.addRow("仅被点名时回复", self.policy_mention)
        form.addRow("发言顺序（角色名，顿号分隔）", self.policy_order)
        form.addRow("最大回复人数", self.policy_max)
        form.addRow("", self.narrator_check)
        return tab

    def _build_branch_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        self.branch_list = QListWidget()
        for branch in self.state.branches:
            item = QListWidgetItem(f"{branch.title} ({len(branch.messages)} 条消息)")
            item.setData(Qt.ItemDataRole.UserRole, branch.branch_id)
            self.branch_list.addItem(item)
            if branch.branch_id == self.state.active_branch_id:
                self.branch_list.setCurrentItem(item)
        layout.addWidget(self.branch_list)
        row = QHBoxLayout()
        switch_btn = QPushButton("切换分支")
        fork_btn = QPushButton("从当前末尾创建分支")
        switch_btn.clicked.connect(self._switch_branch)
        fork_btn.clicked.connect(self._fork_branch)
        row.addWidget(switch_btn)
        row.addWidget(fork_btn)
        layout.addLayout(row)
        return tab

    def _switch_branch(self):
        item = self.branch_list.currentItem()
        if item:
            self.switch_branch_callback(item.data(Qt.ItemDataRole.UserRole))
            self.accept()

    def _fork_branch(self):
        self.fork_branch_callback()
        self.accept()

    def _build_review_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        self.review_list = QListWidget()
        for change_set in self.state.memory_change_sets:
            high_count = sum(change.risk == "high" for change in change_set.changes)
            item = QListWidgetItem(
                f"{change_set.status} | {len(change_set.changes)} 项 | 高风险 {high_count} | {change_set.created_at}"
            )
            item.setData(Qt.ItemDataRole.UserRole, change_set.change_set_id)
            self.review_list.addItem(item)
        layout.addWidget(self.review_list)
        row = QHBoxLayout()
        for label, callback in (
            ("接受/应用", self.apply_change_callback),
            ("修改后应用", self.modify_change_callback),
            ("拒绝", self.reject_change_callback),
            ("撤销已应用", self.revert_change_callback),
        ):
            button = QPushButton(label)
            button.clicked.connect(lambda _checked=False, cb=callback: self._review_action(cb))
            row.addWidget(button)
        layout.addLayout(row)
        return tab

    def _build_messages_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        self.message_list = QListWidget()
        for message in self.state.active_branch().messages:
            preview = message.content.replace("\n", " ")[:70]
            item = QListWidgetItem(
                f"第{message.turn_index}轮 | {message.speaker_name or message.role}: {preview}"
            )
            item.setData(Qt.ItemDataRole.UserRole, message.message_id)
            self.message_list.addItem(item)
        layout.addWidget(self.message_list)
        row = QHBoxLayout()
        for label, operation in (
            ("单角色重生成", "regenerate"),
            ("从此处分支", "fork"),
            ("编辑", "edit"),
            ("删除", "delete"),
            ("查看来源", "source"),
            ("人物书变化", "changes"),
        ):
            button = QPushButton(label)
            button.clicked.connect(
                lambda _checked=False, op=operation: self._run_message_operation(op)
            )
            row.addWidget(button)
        layout.addLayout(row)
        return tab

    def _run_message_operation(self, operation):
        item = self.message_list.currentItem()
        if not item:
            return
        self.message_operation_callback(operation, item.data(Qt.ItemDataRole.UserRole))
        self.accept()

    def _review_action(self, callback):
        item = self.review_list.currentItem()
        if item:
            callback(item.data(Qt.ItemDataRole.UserRole))
            self.accept()

    def _build_overview_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        edit = QTextEdit()
        edit.setReadOnly(True)
        lines = []
        if self.state.consistency_warnings:
            lines.append("# 一致性警告")
            lines.extend(f"- {warning}" for warning in self.state.consistency_warnings[-20:])
            lines.append("")
        for memory in self.character_book.memories:
            if memory.character_id not in self.participant_ids:
                continue
            lines.append(f"# {memory.name}")
            if memory.relationship_states:
                lines.append("关系：")
                for rel in memory.relationship_states:
                    lines.append(
                        f"- {rel.get('target_id')}: 信任 {rel.get('trust', 0)}, "
                        f"好感 {rel.get('affection', 0)}, 敌意 {rel.get('hostility', 0)}, "
                        f"警惕 {rel.get('vigilance', 0)} | {rel.get('description', '')}"
                    )
            if memory.knowledge:
                lines.append("知识：")
                for item in memory.knowledge[-20:]:
                    lines.append(f"- [{item.get('awareness', '')}] {item.get('fact', '')}")
            lines.append("")
        edit.setPlainText("\n".join(lines) or "尚无关系和知识数据。")
        layout.addWidget(edit)
        return tab

    def _save_controls(self):
        self.state.sender_profile_id = self.sender_combo.currentData() or ""
        scene = self.state.scene_state
        scene.time = self.scene_time.text().strip()
        scene.location = self.scene_location.text().strip()
        scene.weather = self.scene_weather.text().strip()
        scene.objective = self.scene_objective.text().strip()
        scene.description = self.scene_description.toPlainText().strip()
        scene.tags = [
            value.strip()
            for value in self.scene_tags.text().replace(",", "、").split("、")
            if value.strip()
        ]
        scene.present_character_ids = self._selected_ids(self.scene_present)
        policy = self.state.turn_policy
        policy.required_speaker_ids = self._selected_ids(self.policy_required)
        policy.allowed_speaker_ids = self._selected_ids(self.policy_allowed)
        policy.blocked_speaker_ids = self._selected_ids(self.policy_blocked)
        policy.mention_only_ids = self._selected_ids(self.policy_mention)
        id_by_name = {
            profile.name: profile.character_id for profile in self.character_book.profiles
        }
        policy.speaker_order = [
            id_by_name.get(value.strip(), value.strip())
            for value in self.policy_order.text().replace(",", "、").split("、")
            if value.strip()
        ]
        policy.max_speakers = self.policy_max.value()
        self.state.narrator_enabled = self.narrator_check.isChecked()
        self.accept()

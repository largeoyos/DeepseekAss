from dataclasses import asdict

from PyQt6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QListWidget,
    QListWidgetItem,
    QTextEdit,
    QLabel,
    QLineEdit,
    QDialogButtonBox,
    QFormLayout,
    QMessageBox,
    QSplitter,
    QWidget,
)
from PyQt6.QtCore import Qt

from core.character_book import CharacterBook, CharacterProfile, find_memory


class CharacterProfileDialog(QDialog):
    def __init__(self, parent=None, profile: CharacterProfile | None = None):
        super().__init__(parent)
        self.setWindowTitle("角色档案")
        self.resize(560, 620)
        self.profile = profile or CharacterProfile()

        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.name_edit = QLineEdit(self.profile.name)
        self.aliases_edit = QLineEdit("、".join(self.profile.aliases))
        self.identity_edit = QLineEdit(self.profile.identity)
        self.status_edit = QLineEdit(self.profile.status)
        self.appearance_edit = QTextEdit(self.profile.appearance)
        self.personality_edit = QTextEdit(self.profile.personality)
        self.speech_edit = QTextEdit(self.profile.speech_style)
        self.background_edit = QTextEdit(self.profile.background)
        self.goals_edit = QTextEdit(self.profile.goals)
        self.boundaries_edit = QTextEdit(self.profile.boundaries)
        self.notes_edit = QTextEdit(self.profile.notes)
        for edit in (
            self.appearance_edit,
            self.personality_edit,
            self.speech_edit,
            self.background_edit,
            self.goals_edit,
            self.boundaries_edit,
            self.notes_edit,
        ):
            edit.setMaximumHeight(70)
        form.addRow("名称", self.name_edit)
        form.addRow("别名", self.aliases_edit)
        form.addRow("身份", self.identity_edit)
        form.addRow("状态", self.status_edit)
        form.addRow("外貌", self.appearance_edit)
        form.addRow("性格", self.personality_edit)
        form.addRow("说话风格", self.speech_edit)
        form.addRow("背景", self.background_edit)
        form.addRow("目标", self.goals_edit)
        form.addRow("禁忌/边界", self.boundaries_edit)
        form.addRow("补充设定", self.notes_edit)
        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            self,
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def get_profile(self) -> CharacterProfile:
        aliases = [a.strip() for a in self.aliases_edit.text().replace(",", "、").split("、") if a.strip()]
        self.profile.name = self.name_edit.text().strip()
        self.profile.aliases = aliases
        self.profile.identity = self.identity_edit.text().strip()
        self.profile.status = self.status_edit.text().strip() or "active"
        self.profile.appearance = self.appearance_edit.toPlainText().strip()
        self.profile.personality = self.personality_edit.toPlainText().strip()
        self.profile.speech_style = self.speech_edit.toPlainText().strip()
        self.profile.background = self.background_edit.toPlainText().strip()
        self.profile.goals = self.goals_edit.toPlainText().strip()
        self.profile.boundaries = self.boundaries_edit.toPlainText().strip()
        self.profile.notes = self.notes_edit.toPlainText().strip()
        return self.profile


class CharacterBookDialog(QDialog):
    def __init__(self, parent, book: CharacterBook, save_callback=None):
        super().__init__(parent)
        self.setWindowTitle("人物书")
        self.resize(900, 620)
        self._book = book
        self._save_callback = save_callback

        layout = QVBoxLayout(self)
        toolbar = QHBoxLayout()
        add_btn = QPushButton("新建角色")
        edit_btn = QPushButton("编辑")
        del_btn = QPushButton("删除")
        add_btn.clicked.connect(self._add_profile)
        edit_btn.clicked.connect(self._edit_profile)
        del_btn.clicked.connect(self._delete_profile)
        toolbar.addWidget(add_btn)
        toolbar.addWidget(edit_btn)
        toolbar.addWidget(del_btn)
        toolbar.addStretch()
        layout.addLayout(toolbar)

        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.addWidget(QLabel("角色档案"))
        self._list = QListWidget()
        self._list.currentRowChanged.connect(self._render_detail)
        left_layout.addWidget(self._list)
        splitter.addWidget(left)

        self._detail = QTextEdit()
        self._detail.setReadOnly(True)
        splitter.addWidget(self._detail)
        splitter.setSizes([260, 640])
        layout.addWidget(splitter, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._refresh()

    def _refresh(self) -> None:
        current_id = self._current_profile().character_id if self._current_profile() else ""
        self._list.clear()
        for profile in self._book.profiles:
            item = QListWidgetItem(profile.name or "未命名角色")
            item.setData(Qt.ItemDataRole.UserRole, profile.character_id)
            self._list.addItem(item)
        if self._list.count():
            target = 0
            for i in range(self._list.count()):
                if self._list.item(i).data(Qt.ItemDataRole.UserRole) == current_id:
                    target = i
                    break
            self._list.setCurrentRow(target)
        else:
            self._detail.setPlainText("尚未创建角色档案。")

    def _current_profile(self) -> CharacterProfile | None:
        item = self._list.currentItem()
        if not item:
            return None
        cid = item.data(Qt.ItemDataRole.UserRole)
        return next((p for p in self._book.profiles if p.character_id == cid), None)

    def _render_detail(self) -> None:
        profile = self._current_profile()
        if not profile:
            self._detail.setPlainText("请选择角色。")
            return
        memory = find_memory(self._book, profile.character_id)
        lines = [f"# {profile.name}", ""]
        data = asdict(profile)
        labels = {
            "aliases": "别名",
            "identity": "身份",
            "status": "状态",
            "appearance": "外貌",
            "personality": "性格",
            "speech_style": "说话风格",
            "background": "背景",
            "goals": "目标",
            "boundaries": "禁忌/边界",
            "notes": "补充设定",
        }
        for key, label in labels.items():
            value = data.get(key)
            if isinstance(value, list):
                value = "、".join(value)
            if value:
                lines.append(f"## {label}\n{value}\n")
        if memory:
            lines.append("## 自动累积记忆")
            if memory.current_state:
                lines.append(f"当前状态：{memory.current_state}")
            if memory.emotion_and_goals:
                lines.append(f"情绪/目标：{memory.emotion_and_goals}")
            if memory.knowledge_state:
                lines.append(f"已知信息：{memory.knowledge_state}")
            if memory.experiences:
                lines.append("经历：\n" + "\n".join(f"- {x}" for x in memory.experiences))
            if memory.recent_actions:
                lines.append("近期行动：\n" + "\n".join(f"- {x}" for x in memory.recent_actions))
            if memory.relationships:
                lines.append("关系：\n" + "\n".join(f"- {k}: {v}" for k, v in memory.relationships.items()))
            if memory.key_dialogues:
                lines.append("关键对话：\n" + "\n".join(f"- {x}" for x in memory.key_dialogues))
        self._detail.setPlainText("\n".join(lines))

    def _add_profile(self) -> None:
        dlg = CharacterProfileDialog(self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        profile = dlg.get_profile()
        if not profile.name:
            QMessageBox.warning(self, "缺少名称", "角色名称不能为空。")
            return
        self._book.profiles.append(profile)
        self._save()
        self._refresh()

    def _edit_profile(self) -> None:
        profile = self._current_profile()
        if not profile:
            return
        dlg = CharacterProfileDialog(self, profile)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        updated = dlg.get_profile()
        if not updated.name:
            QMessageBox.warning(self, "缺少名称", "角色名称不能为空。")
            return
        self._save()
        self._refresh()

    def _delete_profile(self) -> None:
        profile = self._current_profile()
        if not profile:
            return
        if QMessageBox.question(self, "删除角色", f"删除「{profile.name}」及其人物书记忆？") != QMessageBox.StandardButton.Yes:
            return
        self._book.profiles = [p for p in self._book.profiles if p.character_id != profile.character_id]
        self._book.memories = [m for m in self._book.memories if m.character_id != profile.character_id]
        self._save()
        self._refresh()

    def _save(self) -> None:
        if self._save_callback:
            self._save_callback(self._book)

    def get_book(self) -> CharacterBook:
        return self._book

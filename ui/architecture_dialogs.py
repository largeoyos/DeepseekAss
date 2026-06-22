"""Dialogs for context inspection, policies, and whole-book versions."""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)


class ContextPreviewDialog(QDialog):
    def __init__(self, parent, report) -> None:
        super().__init__(parent)
        self.setWindowTitle("生成上下文预览")
        self.resize(960, 720)
        layout = QVBoxLayout(self)
        summary = QLabel(report.preview())
        summary.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        summary.setWordWrap(True)
        layout.addWidget(summary)
        content = QPlainTextEdit()
        content.setReadOnly(True)
        content.setPlainText(report.render())
        layout.addWidget(content, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class WorldContextPolicyDialog(QDialog):
    COLUMNS = ("启用", "ID", "名称", "加载模式", "优先级", "简介", "关键词")

    def __init__(self, parent, novel_manager, title: str) -> None:
        super().__init__(parent)
        self.manager = novel_manager
        self.title = title
        self.workspace = novel_manager.get_workspace(title)
        self.setWindowTitle("世界书上下文策略")
        self.resize(1100, 620)
        layout = QVBoxLayout(self)
        help_label = QLabel(
            "常驻：每次完整注入；自动：名称/简介/关键词命中时展开；"
            "手动：仅由上下文预览或后续 Agent 明确引用。"
        )
        help_label.setWordWrap(True)
        layout.addWidget(help_label)
        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table, 1)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._load()

    def _load(self) -> None:
        from core.context_assembler import _world_entities

        policies = self.workspace.load_context_policies()
        bible = self.manager.load_world_bible(self.title)
        entities = _world_entities(bible)
        self.table.setRowCount(len(entities))
        for row, (entity_id, _kind, name, _item) in enumerate(entities):
            policy = {
                "enabled": True,
                "load_mode": "auto",
                "priority": 50,
                "brief_description": "",
                "keywords": [],
                **dict(policies.get(entity_id) or {}),
            }
            enabled = QCheckBox()
            enabled.setChecked(bool(policy["enabled"]))
            holder = QWidget()
            holder_layout = QHBoxLayout(holder)
            holder_layout.setContentsMargins(0, 0, 0, 0)
            holder_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
            holder_layout.addWidget(enabled)
            self.table.setCellWidget(row, 0, holder)
            id_item = QTableWidgetItem(entity_id)
            id_item.setFlags(id_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(row, 1, id_item)
            name_item = QTableWidgetItem(name)
            name_item.setFlags(name_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(row, 2, name_item)
            mode = QComboBox()
            mode.addItem("常驻", "resident")
            mode.addItem("自动", "auto")
            mode.addItem("手动", "manual")
            index = mode.findData(policy["load_mode"])
            mode.setCurrentIndex(max(0, index))
            self.table.setCellWidget(row, 3, mode)
            self.table.setItem(row, 4, QTableWidgetItem(str(policy["priority"])))
            self.table.setItem(row, 5, QTableWidgetItem(str(policy["brief_description"])))
            self.table.setItem(row, 6, QTableWidgetItem("、".join(policy["keywords"])))

    def _save(self) -> None:
        policies: dict[str, dict] = {}
        for row in range(self.table.rowCount()):
            entity_id = self.table.item(row, 1).text()
            enabled_holder = self.table.cellWidget(row, 0)
            enabled = enabled_holder.findChild(QCheckBox).isChecked()
            mode = self.table.cellWidget(row, 3).currentData()
            try:
                priority = int(self.table.item(row, 4).text())
            except (TypeError, ValueError):
                priority = 50
            brief = self.table.item(row, 5).text().strip()
            raw_keywords = self.table.item(row, 6).text()
            keywords = [
                item.strip()
                for item in raw_keywords.replace(",", "、").split("、")
                if item.strip()
            ]
            policies[entity_id] = {
                "enabled": enabled,
                "load_mode": mode,
                "priority": max(0, min(100, priority)),
                "brief_description": brief,
                "keywords": keywords,
            }
        self.workspace.save_context_policies(policies)
        self.accept()


class ProjectVersionsDialog(QDialog):
    def __init__(self, parent, novel_manager, title: str) -> None:
        super().__init__(parent)
        self.manager = novel_manager
        self.title = title
        self.service = novel_manager.snapshot_service(title)
        self.snapshots = []
        self.setWindowTitle(f"项目版本 - {title}")
        self.resize(1040, 680)

        layout = QVBoxLayout(self)
        toolbar = QHBoxLayout()
        create_button = QPushButton("保存当前版本")
        create_button.clicked.connect(self._create)
        restore_button = QPushButton("恢复所选版本")
        restore_button.clicked.connect(self._restore)
        refresh_button = QPushButton("刷新")
        refresh_button.clicked.connect(self._refresh)
        toolbar.addWidget(create_button)
        toolbar.addWidget(restore_button)
        toolbar.addWidget(refresh_button)
        toolbar.addStretch(1)
        layout.addLayout(toolbar)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(("时间", "来源", "说明", "文件数"))
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.itemSelectionChanged.connect(self._show_changes)
        splitter.addWidget(self.table)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.addWidget(QLabel("与当前书籍的变化"))
        self.changes = QTableWidget(0, 2)
        self.changes.setHorizontalHeaderLabels(("状态", "路径"))
        self.changes.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.changes.itemSelectionChanged.connect(self._show_diff)
        right_layout.addWidget(self.changes, 1)
        right_layout.addWidget(QLabel("文本 Diff"))
        self.diff = QPlainTextEdit()
        self.diff.setReadOnly(True)
        right_layout.addWidget(self.diff, 1)
        splitter.addWidget(right)
        splitter.setSizes([430, 610])
        layout.addWidget(splitter, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._refresh()

    def _selected_snapshot(self):
        row = self.table.currentRow()
        return self.snapshots[row] if 0 <= row < len(self.snapshots) else None

    def _refresh(self) -> None:
        self.snapshots = self.service.list()
        self.table.setRowCount(len(self.snapshots))
        labels = {
            "manual": "手动",
            "chapter": "章节",
            "timer": "定时",
            "rollback_backup": "恢复前备份",
        }
        for row, item in enumerate(self.snapshots):
            self.table.setItem(row, 0, QTableWidgetItem(item.created_at))
            self.table.setItem(row, 1, QTableWidgetItem(labels.get(item.source, item.source)))
            self.table.setItem(row, 2, QTableWidgetItem(item.message))
            self.table.setItem(row, 3, QTableWidgetItem(str(len(item.files))))
        if self.snapshots:
            self.table.selectRow(0)
        else:
            self.changes.setRowCount(0)
            self.diff.clear()

    def _create(self) -> None:
        message, ok = QInputDialog.getText(self, "保存项目版本", "版本说明：")
        if not ok:
            return
        try:
            self.service.create(message, source="manual")
            self._refresh()
        except Exception as exc:
            QMessageBox.critical(self, "保存失败", str(exc))

    def _show_changes(self) -> None:
        snapshot = self._selected_snapshot()
        if snapshot is None:
            return
        try:
            changes = self.service.status(snapshot.snapshot_id)
        except Exception as exc:
            QMessageBox.warning(self, "读取失败", str(exc))
            return
        self.changes.setRowCount(len(changes))
        for row, change in enumerate(changes):
            self.changes.setItem(row, 0, QTableWidgetItem(change["status"]))
            self.changes.setItem(row, 1, QTableWidgetItem(change["path"]))
        self.diff.clear()

    def _show_diff(self) -> None:
        snapshot = self._selected_snapshot()
        row = self.changes.currentRow()
        if snapshot is None or row < 0:
            return
        path = self.changes.item(row, 1).text()
        try:
            self.diff.setPlainText(self.service.diff(snapshot.snapshot_id, path))
        except Exception as exc:
            self.diff.setPlainText(str(exc))

    def _restore(self) -> None:
        snapshot = self._selected_snapshot()
        if snapshot is None:
            return
        answer = QMessageBox.question(
            self,
            "确认恢复",
            "将恢复整本书的章节、摘要、世界书和内部状态。\n"
            "系统会先自动保存当前版本。是否继续？",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self.service.restore(snapshot.snapshot_id)
            QMessageBox.information(self, "恢复完成", "项目已恢复，请关闭窗口后刷新书籍。")
            self._refresh()
        except Exception as exc:
            QMessageBox.critical(self, "恢复失败", str(exc))

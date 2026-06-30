"""Dialog for confirming world-bible diffs before saving."""
from __future__ import annotations

import json

from PyQt6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QPlainTextEdit, QTableWidget, QTableWidgetItem, QVBoxLayout

from core.world_bible_diff import summarize_world_bible_diff


class WorldBibleDiffDialog(QDialog):
    def __init__(self, parent, diff_items) -> None:
        super().__init__(parent)
        self._items = list(diff_items)
        self.setWindowTitle("世界书差异确认")
        self.resize(980, 640)
        layout = QVBoxLayout(self)
        summary = summarize_world_bible_diff(self._items)
        label = QLabel(
            f"共 {summary['total']} 项变更：新增 {summary['added']}，修改 {summary['modified']}，删除 {summary['removed']}；"
            f"高风险 {summary['high']}，中风险 {summary['medium']}，低风险 {summary['low']}。"
        )
        label.setWordWrap(True)
        layout.addWidget(label)
        self._table = QTableWidget(0, 5)
        self._table.setHorizontalHeaderLabels(("风险", "类型", "类别", "条目", "摘要"))
        self._table.itemSelectionChanged.connect(self._show_selected)
        layout.addWidget(self._table, 1)
        self._details = QPlainTextEdit()
        self._details.setReadOnly(True)
        layout.addWidget(self._details, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._load()

    def _load(self) -> None:
        self._table.setRowCount(len(self._items))
        for row, item in enumerate(self._items):
            for col, value in enumerate((item.risk, item.change_type, item.category, item.key, item.summary)):
                self._table.setItem(row, col, QTableWidgetItem(str(value)))
        self._table.resizeColumnsToContents()
        if self._items:
            self._table.selectRow(0)

    def _show_selected(self) -> None:
        row = self._table.currentRow()
        if row < 0 or row >= len(self._items):
            return
        item = self._items[row]
        self._details.setPlainText(json.dumps({"before": item.before, "after": item.after}, ensure_ascii=False, indent=2, default=str))

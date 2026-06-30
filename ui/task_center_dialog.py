"""Task center dialog for active and historical background work."""
from __future__ import annotations

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QDialog, QDialogButtonBox, QHBoxLayout, QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout


class TaskCenterDialog(QDialog):
    def __init__(self, parent, task_runner) -> None:
        super().__init__(parent)
        self._runner = task_runner
        self.setWindowTitle("任务中心")
        self.resize(980, 520)
        layout = QVBoxLayout(self)
        toolbar = QHBoxLayout()
        refresh = QPushButton("刷新")
        refresh.clicked.connect(self._refresh)
        cancel = QPushButton("取消选中任务")
        cancel.clicked.connect(self._cancel_selected)
        retry = QPushButton("重试选中任务")
        retry.clicked.connect(self._retry_selected)
        toolbar.addWidget(refresh)
        toolbar.addWidget(cancel)
        toolbar.addWidget(retry)
        toolbar.addStretch(1)
        layout.addLayout(toolbar)
        self._table = QTableWidget(0, 8)
        self._table.setHorizontalHeaderLabels(("状态", "进度", "阶段", "名称", "消息", "耗时(ms)", "创建时间", "ID"))
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        layout.addWidget(self._table, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh)
        self._timer.start(1500)
        self._refresh()

    def _records(self):
        return self._runner.history(limit=100)

    def _refresh(self) -> None:
        records = self._records()
        self._table.setRowCount(len(records))
        for row, record in enumerate(records):
            values = [
                record.status,
                f"{record.progress}%",
                record.stage,
                record.name,
                record.error or record.message or record.result_preview,
                str(record.duration_ms),
                record.created_at,
                record.task_id,
            ]
            for col, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                self._table.setItem(row, col, item)
        self._table.resizeColumnsToContents()

    def _selected_task_id(self) -> str:
        row = self._table.currentRow()
        if row < 0:
            return ""
        item = self._table.item(row, 7)
        return item.text() if item else ""

    def _cancel_selected(self) -> None:
        task_id = self._selected_task_id()
        if not task_id:
            return
        if not self._runner.cancel(task_id):
            QMessageBox.information(self, "任务中心", "该任务不在运行中。")
        self._refresh()

    def _retry_selected(self) -> None:
        task_id = self._selected_task_id()
        if not task_id:
            return
        try:
            self._runner.retry(task_id)
        except Exception as exc:
            QMessageBox.warning(self, "任务中心", str(exc))
        self._refresh()

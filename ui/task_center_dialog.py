"""Task center dialog for active and historical background work."""
from __future__ import annotations

from PyQt6.QtCore import QTimer, Qt
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
        cancel = self._cancel_button = QPushButton("取消选中任务")
        cancel.clicked.connect(self._cancel_selected)
        retry = self._retry_button = QPushButton("重试选中任务")
        retry.clicked.connect(self._retry_selected)
        toolbar.addWidget(refresh)
        toolbar.addWidget(cancel)
        toolbar.addWidget(retry)
        toolbar.addStretch(1)
        layout.addLayout(toolbar)
        self._table = QTableWidget(0, 8)
        self._table.setHorizontalHeaderLabels(("状态", "进度", "阶段", "名称", "消息", "耗时(ms)", "创建时间", "ID"))
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.itemSelectionChanged.connect(self._update_actions)
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
        selected_id = self._selected_task_id()
        scroll = self._table.verticalScrollBar().value()
        records = self._records()
        self._table.blockSignals(True)
        self._table.clearSelection()
        self._table.setCurrentItem(None)
        self._table.setRowCount(len(records))
        statuses = {"pending": "等待中", "running": "运行中", "cancelling": "正在取消",
                    "completed": "已完成", "failed": "失败", "cancelled": "已取消"}
        for row, record in enumerate(records):
            values = [
                statuses.get(record.status, record.status),
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
                item.setData(Qt.ItemDataRole.UserRole, record.task_id)
                self._table.setItem(row, col, item)
            if record.task_id == selected_id:
                self._table.setCurrentCell(row, 0)
                self._table.selectRow(row)
        self._table.resizeColumnsToContents()
        self._table.verticalScrollBar().setValue(scroll)
        self._table.blockSignals(False)
        self._update_actions()

    def _selected_task_id(self) -> str:
        row = self._table.currentRow()
        if row < 0:
            return ""
        item = self._table.item(row, 7)
        return item.data(Qt.ItemDataRole.UserRole) if item else ""

    def _update_actions(self) -> None:
        task_id = self._selected_task_id()
        record = self._runner.get_record(task_id) if task_id else None
        self._cancel_button.setEnabled(bool(
            record and record.cancellable and record.status in {"pending", "running"}
        ))
        self._retry_button.setEnabled(bool(
            record and record.retryable and record.status in {"failed", "cancelled"}
        ))

    def _cancel_selected(self) -> None:
        task_id = self._selected_task_id()
        if not task_id:
            return
        if not self._runner.cancel(task_id):
            QMessageBox.information(self, "任务中心", "该任务已结束、正在取消或不支持取消。")
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

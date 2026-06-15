from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from core.token_log_manager import TokenLogManager, TokenLogEntry


class TokenLogDialog(QDialog):
    """Token usage log viewer."""

    def __init__(self, parent, manager: TokenLogManager):
        super().__init__(parent)
        self._manager = manager
        self._entries: list[TokenLogEntry] = []
        self.setWindowTitle("Token 消耗日志")
        self.resize(760, 520)
        self._init_ui()
        self._load()

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)

        self._summary = QLabel("")
        self._summary.setStyleSheet("font-weight: bold; color: #9cdcfe;")
        layout.addWidget(self._summary)

        tools = QHBoxLayout()
        self._search = QLineEdit()
        self._search.setPlaceholderText("搜索日志内容、模式或模型...")
        self._search.textChanged.connect(self._refresh_table)
        tools.addWidget(self._search, stretch=1)

        clear_btn = QPushButton("清空")
        clear_btn.clicked.connect(self._clear)
        tools.addWidget(clear_btn)
        layout.addLayout(tools)

        self._table = QTableWidget(0, 11)
        self._table.setHorizontalHeaderLabels([
            "时间", "方向", "操作", "模式", "模型", "内容预览", "Prompt", "Completion / Total",
            "耗时", "字符", "汉字"
        ])
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        layout.addWidget(self._table, stretch=1)

        close_row = QHBoxLayout()
        close_row.addStretch()
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.accept)
        close_row.addWidget(close_btn)
        layout.addLayout(close_row)

    def _load(self) -> None:
        self._entries = self._manager.list_entries()
        totals = self._manager.totals()
        self._summary.setText(
            f"总计: {totals['prompt_tokens']:,} prompt / "
            f"{totals['completion_tokens']:,} completion = {totals['total_tokens']:,} tokens"
        )
        self._refresh_table()

    def _refresh_table(self) -> None:
        keyword = self._search.text().strip().lower()
        rows = []
        for entry in self._entries:
            haystack = " ".join([
                entry.timestamp, entry.operation, entry.direction, entry.strategy,
                entry.model, entry.content_preview,
            ]).lower()
            if keyword and keyword not in haystack:
                continue
            rows.append(entry)

        self._table.setRowCount(len(rows))
        for row, entry in enumerate(rows):
            prompt = "未返回" if entry.usage_status != "ok" else str(entry.prompt_tokens or 0)
            comp_total = (
                "未返回用量"
                if entry.usage_status != "ok"
                else f"{entry.completion_tokens or 0} / {entry.total_tokens or 0}"
            )
            duration = "" if entry.duration_ms is None else f"{entry.duration_ms / 1000:.1f}s"
            char_count = "" if entry.char_count is None else str(entry.char_count)
            hanzi_count = "" if entry.hanzi_count is None else str(entry.hanzi_count)
            values = [
                entry.timestamp,
                "发送" if entry.direction == "send" else "接收",
                entry.operation,
                entry.strategy,
                entry.model,
                entry.content_preview,
                prompt,
                comp_total,
                duration,
                char_count,
                hanzi_count,
            ]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                if col in (6, 7, 8, 9, 10):
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self._table.setItem(row, col, item)
        self._table.resizeColumnsToContents()

    def _clear(self) -> None:
        reply = QMessageBox.question(
            self,
            "确认清空",
            "确定清空所有 Token 日志？此操作不可恢复。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._manager.clear()
        self._load()

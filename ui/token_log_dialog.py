import json

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
)

from core.token_log_manager import TokenLogManager, TokenLogEntry


class ContentViewerDialog(QDialog):
    """Read-only viewer for a token log entry's full content."""

    def __init__(self, parent, entry: TokenLogEntry):
        super().__init__(parent)
        self.setWindowTitle("内容全文")
        self.resize(780, 560)

        layout = QVBoxLayout(self)
        direction = "发送" if entry.direction == "send" else "接收"
        meta = QLabel(
            f"{entry.timestamp} · {direction} · {entry.operation} · {entry.strategy} · {entry.model}"
        )
        meta.setWordWrap(True)
        meta.setStyleSheet("color: #888;")
        layout.addWidget(meta)

        meta_text = f"{entry.timestamp} · {direction} · {entry.operation} · {entry.strategy} · {entry.model}"
        if entry.reasoning_tokens is not None:
            meta_text += f" · 推理 {entry.reasoning_tokens} token"
        meta.setText(meta_text)

        tabs = QTabWidget()
        self._text = QPlainTextEdit()
        self._text.setReadOnly(True)
        self._text.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        full = (entry.content_full or "").strip()
        self._text.setPlainText(full if full else (entry.content_preview or "(无内容)"))
        tabs.addTab(self._text, "正文")
        self._reasoning = QPlainTextEdit()
        self._reasoning.setReadOnly(True)
        self._reasoning.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        rfull = (entry.reasoning_content_full or "").strip()
        self._reasoning.setPlainText(rfull if rfull else (entry.reasoning_content_preview or "(无推理内容)"))
        tabs.addTab(self._reasoning, "推理内容")
        self._routing = QPlainTextEdit()
        self._routing.setReadOnly(True)
        self._routing.setPlainText(json.dumps({
            "provider_id": entry.provider_id,
            "provider_name": entry.provider_name,
            "protocol": entry.protocol,
            "model_profile_id": entry.model_profile_id,
            "stage": entry.stage,
            "reasoning_level": entry.reasoning_level,
            "context_budget": entry.context_budget,
            "search_source": entry.search_source,
            "fallback_attempts": entry.fallback_attempts,
            "final_failure_reason": entry.final_failure_reason,
        }, ensure_ascii=False, indent=2))
        tabs.addTab(self._routing, "路由 / 预算 / 回退")
        layout.addWidget(tabs, stretch=1)

        notes = []
        if not full:
            notes.append("旧日志未保存正文全文，仅能显示保存时的预览。")
        if not rfull and (entry.reasoning_tokens or 0) > 0:
            notes.append("本次返回了推理 token，但旧日志未保存推理内容。")
        if notes:
            note = QLabel("；".join(notes))
            note.setStyleSheet("color: #b58900;")
            layout.addWidget(note)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)


class TokenLogDialog(QDialog):
    """Token usage log viewer."""

    def __init__(self, parent, manager: TokenLogManager):
        super().__init__(parent)
        self._manager = manager
        self._entries: list[TokenLogEntry] = []
        self._rows: list[TokenLogEntry] = []
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

        self._table = QTableWidget(0, 15)
        self._table.setHorizontalHeaderLabels([
            "时间", "方向", "操作", "阶段", "服务", "协议", "模型", "推理", "内容预览", "推理预览", "Prompt", "Completion / Total",
            "耗时", "字符", "汉字"
        ])
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.cellDoubleClicked.connect(self._on_cell_double_clicked)
        preview_header = self._table.horizontalHeaderItem(8)
        if preview_header is not None:
            preview_header.setToolTip("双击查看正文全文")
        reasoning_header = self._table.horizontalHeaderItem(9)
        if reasoning_header is not None:
            reasoning_header.setToolTip("模型思考/推理内容预览，双击正文或推理单元格查看全文")
        layout.addWidget(self._table, stretch=1)

        close_row = QHBoxLayout()
        hint = QLabel("双击「内容预览」可查看全文")
        hint.setStyleSheet("color: #888;")
        close_row.addWidget(hint)
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
                entry.model, entry.provider_name, entry.protocol, entry.stage,
                entry.reasoning_level, entry.content_preview, entry.reasoning_content_preview,
            ]).lower()
            if keyword and keyword not in haystack:
                continue
            rows.append(entry)

        self._table.setRowCount(len(rows))
        self._rows = rows
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
            content_preview = entry.content_preview
            if not (content_preview or "").strip() and (entry.completion_tokens or 0) > 0:
                if entry.reasoning_tokens is not None:
                    content_preview = f"（正文为空：本次返回 {entry.reasoning_tokens} 个推理 token、0 个正文字符）"
                else:
                    content_preview = "（正文为空：本次返回全为推理内容、0 个正文字符）"
            reasoning_preview = entry.reasoning_content_preview
            if not reasoning_preview and (entry.reasoning_tokens or 0) > 0:
                reasoning_preview = "（有推理 token，未保存推理内容）"
            if not reasoning_preview:
                reasoning_preview = "—"
            values = [
                entry.timestamp,
                "发送" if entry.direction == "send" else "接收",
                entry.operation,
                entry.stage,
                entry.provider_name,
                entry.protocol,
                entry.model,
                entry.reasoning_level,
                content_preview,
                reasoning_preview,
                prompt,
                comp_total,
                duration,
                char_count,
                hanzi_count,
            ]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                if col in (10, 11, 12, 13, 14):
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self._table.setItem(row, col, item)
        self._table.resizeColumnsToContents()

    def _on_cell_double_clicked(self, row: int, column: int) -> None:
        if column not in (8, 9) or row < 0 or row >= len(self._rows):
            return
        ContentViewerDialog(self, self._rows[row]).exec()

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

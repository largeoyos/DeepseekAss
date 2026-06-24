from __future__ import annotations

import difflib
import json
from dataclasses import asdict

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFormLayout, QGroupBox, QHBoxLayout,
    QLabel, QListWidget, QMessageBox, QPushButton, QSplitter, QTabWidget,
    QTextBrowser, QTextEdit, QVBoxLayout, QWidget,
)

from core.agent.changes import ChangeSetService
from core.agent.domain_tools import build_domain_tool_registry
from core.agent.profiles import AGENT_PROFILES
from core.agent.repository import AgentRepository
from core.agent.backends import build_agent_backend
from core.agent.queue import AgentTaskQueue
from core.agent.types import AgentRunRequest
from ui.dialog_utils import apply_responsive_dialog_size


class AgentSignalBridge(QObject):
    event = pyqtSignal(object)
    finished = pyqtSignal(object)


class ChangeApprovalDialog(QDialog):
    def __init__(self, parent, change_set, novel_manager, book_title: str) -> None:
        super().__init__(parent)
        self.change_set = change_set
        self.approved_ids: list[str] = []
        self.setWindowTitle("审批 Agent 变更")
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"原因：{change_set.reason or '未提供'}\n批准前会自动创建项目快照。"))
        self.operations = QListWidget()
        for operation in change_set.operations:
            from PyQt6.QtCore import Qt
            from PyQt6.QtWidgets import QListWidgetItem
            item = QListWidgetItem(f"{operation.operation_id} | {operation.operation} | {operation.target_id}")
            item.setData(Qt.ItemDataRole.UserRole, operation.operation_id)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            self.operations.addItem(item)
        self.operations.currentRowChanged.connect(lambda row: self._show_operation(row, novel_manager, book_title))
        layout.addWidget(self.operations)
        self.diff = QTextBrowser()
        layout.addWidget(self.diff, 1)
        buttons = QDialogButtonBox()
        approve = buttons.addButton("批准勾选项", QDialogButtonBox.ButtonRole.AcceptRole)
        reject = buttons.addButton("拒绝", QDialogButtonBox.ButtonRole.RejectRole)
        approve.clicked.connect(self._approve_all)
        reject.clicked.connect(self.reject)
        layout.addWidget(buttons)
        if change_set.operations:
            self.operations.setCurrentRow(0)
        apply_responsive_dialog_size(self, 760, 560, minimum_width=440, minimum_height=300)

    def _show_operation(self, row: int, manager, title: str) -> None:
        if row < 0:
            return
        operation = self.change_set.operations[row]
        if operation.operation == "chapter.save_version":
            before = manager.read_active_chapter(title, int(operation.target_id)) or ""
            after = operation.payload.get("content", "")
            text = "".join(difflib.unified_diff(before.splitlines(True), after.splitlines(True), fromfile="当前章节", tofile="Agent 提议"))
        else:
            text = json.dumps(operation.payload, ensure_ascii=False, indent=2)
        self.diff.setPlainText(text or "无文本差异")

    def _approve_all(self) -> None:
        from PyQt6.QtCore import Qt
        self.approved_ids = [self.operations.item(index).data(Qt.ItemDataRole.UserRole) for index in range(self.operations.count()) if self.operations.item(index).checkState() == Qt.CheckState.Checked]
        if not self.approved_ids:
            QMessageBox.warning(self, "未选择", "请至少勾选一项变更，或点击拒绝。")
            return
        self.accept()


class AgentWorkbenchDialog(QDialog):
    def __init__(self, parent, *, novel_manager, client, conversation_manager=None) -> None:
        super().__init__(parent)
        self.manager = novel_manager
        self.client = client
        self.conversation_manager = conversation_manager
        self.bridge = AgentSignalBridge()
        self.bridge.event.connect(self._on_event)
        self.bridge.finished.connect(self._on_finished)
        settings = getattr(parent, "_settings", {}) or {}
        self.runtime, self.backend_status = build_agent_backend(
            settings=settings,
            novel_manager=novel_manager,
            client=client,
            tool_registry=build_domain_tool_registry(novel_manager, conversation_manager),
            event_sink=self.bridge.event.emit,
            skills_enabled=bool(settings.get("agent_skills_enabled", True)),
        )
        self.task_queue = AgentTaskQueue(read_concurrency=2)
        self.current_session = None
        self.current_run_id = ""
        self.setWindowTitle("Agent 工作台")
        self._build_ui()
        apply_responsive_dialog_size(self, 1080, 700, minimum_width=640, minimum_height=420)
        self._refresh_books()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        toolbar = QHBoxLayout()
        self.book_combo = QComboBox()
        self.book_combo.currentTextChanged.connect(self._book_changed)
        self.agent_combo = QComboBox()
        for kind, profile in AGENT_PROFILES.items():
            self.agent_combo.addItem(profile.display_name, kind)
        self.agent_combo.currentIndexChanged.connect(self._new_session)
        new_btn = QPushButton("新建会话")
        new_btn.clicked.connect(self._new_session)
        toolbar.addWidget(QLabel("书籍")); toolbar.addWidget(self.book_combo, 1)
        toolbar.addWidget(QLabel("Agent")); toolbar.addWidget(self.agent_combo)
        toolbar.addWidget(new_btn)
        root.addLayout(toolbar)

        splitter = QSplitter()
        left = QWidget(); left_layout = QVBoxLayout(left)
        left_layout.addWidget(QLabel("Agent 会话"))
        self.session_list = QListWidget()
        self.session_list.currentRowChanged.connect(self._load_selected_session)
        left_layout.addWidget(self.session_list)
        splitter.addWidget(left)

        center = QWidget(); center_layout = QVBoxLayout(center)
        self.transcript = QTextBrowser(); center_layout.addWidget(self.transcript, 1)
        self.input = QTextEdit(); self.input.setPlaceholderText("描述分析、规划或写作任务……"); self.input.setMaximumHeight(120)
        center_layout.addWidget(self.input)
        buttons = QHBoxLayout()
        self.send_btn = QPushButton("发送")
        self.stop_btn = QPushButton("停止"); self.pause_btn = QPushButton("暂停"); self.resume_btn = QPushButton("恢复")
        self.send_btn.clicked.connect(self._send); self.stop_btn.clicked.connect(self._stop); self.pause_btn.clicked.connect(self._pause); self.resume_btn.clicked.connect(self._resume)
        for button in (self.send_btn, self.pause_btn, self.resume_btn, self.stop_btn): buttons.addWidget(button)
        center_layout.addLayout(buttons)
        splitter.addWidget(center)

        tabs = QTabWidget()
        self.timeline = QTextBrowser(); tabs.addTab(self.timeline, "运行时间线")
        self.context_view = QTextBrowser(); tabs.addTab(self.context_view, "上下文/详情")
        pending_page = QWidget(); pending_layout = QVBoxLayout(pending_page)
        self.pending = QListWidget(); pending_layout.addWidget(self.pending)
        approve_btn = QPushButton("查看并审批"); approve_btn.clicked.connect(self._approve_selected)
        reject_btn = QPushButton("拒绝选中变更"); reject_btn.clicked.connect(self._reject_selected)
        pending_layout.addWidget(approve_btn); pending_layout.addWidget(reject_btn)
        tabs.addTab(pending_page, "待审批")
        splitter.addWidget(tabs)
        splitter.setSizes([210, 600, 340])
        root.addWidget(splitter, 1)

    def _refresh_books(self) -> None:
        self.book_combo.clear(); self.book_combo.addItems(self.manager.list_books())
        self._book_changed(self.book_combo.currentText())

    def _repository(self):
        title = self.book_combo.currentText()
        return AgentRepository(self.manager.get_workspace(title)) if title else None

    def _book_changed(self, _title: str) -> None:
        self._refresh_sessions(); self._refresh_pending()

    def _refresh_sessions(self) -> None:
        self.session_list.clear(); repo = self._repository()
        self._sessions = repo.list_sessions() if repo else []
        for session in self._sessions:
            self.session_list.addItem(f"{AGENT_PROFILES[session.agent_kind].display_name} | {session.title}")

    def _new_session(self) -> None:
        title = self.book_combo.currentText()
        if not title:
            return
        kind = self.agent_combo.currentData()
        self.current_session = self.runtime.create_session(title, kind)
        self._refresh_sessions()
        self.session_list.setCurrentRow(0)

    def _load_selected_session(self, row: int) -> None:
        if row < 0 or row >= len(getattr(self, "_sessions", [])):
            return
        self.current_session = self._sessions[row]
        self.transcript.clear()
        for message in self.current_session.messages:
            self.transcript.append(f"<b>{message.get('role', '')}</b><br>{message.get('content', '')}<br>")

    def _send(self) -> None:
        message, title = self.input.toPlainText().strip(), self.book_combo.currentText()
        if not message or not title:
            return
        kind = self.agent_combo.currentData()
        if self.current_session is None or self.current_session.agent_kind != kind:
            self.current_session = self.runtime.create_session(title, kind)
        manifest = self.manager.ensure_workspace(title)
        request = AgentRunRequest(manifest.book_id, self.current_session.session_id, kind, message, model=getattr(self.client, "model", ""), book_title=title)
        self.transcript.append(f"<b>user</b><br>{message}<br>")
        self.input.clear(); self.send_btn.setEnabled(False)
        read_only = kind in {"continuity_editor", "project_maintainer"} and "修改" not in message and "写入" not in message
        self.task_queue.submit(lambda: self.bridge.finished.emit(self.runtime.run(request)), read_only=read_only)

    def _on_event(self, event) -> None:
        self.current_run_id = event.run_id
        self.timeline.append(f"{event.sequence}. {event.event_type}: {json.dumps(event.payload, ensure_ascii=False, default=str)}")
        if event.event_type == "model_stream":
            self.transcript.append(f"<b>assistant</b><br>{event.payload.get('text', '')}<br>")
        if event.event_type == "approval_required":
            self._refresh_pending()

    def _on_finished(self, run) -> None:
        self.send_btn.setEnabled(True)
        self.context_view.setPlainText(json.dumps(asdict(run), ensure_ascii=False, indent=2, default=str))
        self._refresh_sessions(); self._refresh_pending()

    def _stop(self) -> None:
        if self.current_run_id: self.runtime.cancel(self.current_run_id)

    def _pause(self) -> None:
        if self.current_run_id: self.runtime.pause(self.current_run_id)

    def _resume(self) -> None:
        if not self.current_run_id:
            return
        repository = self._repository()
        run = repository.load_run(self.current_run_id) if repository else None
        if run and run.status == "waiting_approval":
            QMessageBox.information(self, "等待审批", "请在待审批列表中批准或拒绝变更后继续。")
            return
        self.runtime.resume(self.current_run_id, {"resume": True})

    def _refresh_pending(self) -> None:
        self.pending.clear(); repo = self._repository()
        self._pending_sets = repo.list_pending_change_sets() if repo else []
        for change in self._pending_sets:
            self.pending.addItem(f"{change.change_set_id} | {change.reason or 'Agent 变更'} | {len(change.operations)} 项")

    def _selected_change(self):
        row = self.pending.currentRow()
        return self._pending_sets[row] if 0 <= row < len(getattr(self, "_pending_sets", [])) else None

    def _approve_selected(self) -> None:
        change = self._selected_change()
        if not change:
            return
        dialog = ChangeApprovalDialog(self, change, self.manager, self.book_combo.currentText())
        if dialog.exec() == QDialog.DialogCode.Accepted:
            try:
                ChangeSetService(self.manager, self.book_combo.currentText(), self._repository()).approve(change.change_set_id, dialog.approved_ids)
                if self.current_run_id:
                    self.runtime.resume(self.current_run_id, {"approved": True, "change_set_id": change.change_set_id})
                QMessageBox.information(self, "完成", "变更已应用并创建项目快照。")
            except Exception as exc:
                QMessageBox.critical(self, "应用失败", str(exc))
        self._refresh_pending()

    def _reject_selected(self) -> None:
        change = self._selected_change()
        if change:
            ChangeSetService(self.manager, self.book_combo.currentText(), self._repository()).reject(change.change_set_id)
            if self.current_run_id:
                self.runtime.resume(self.current_run_id, {"approved": False, "change_set_id": change.change_set_id})
            self._refresh_pending()

    def closeEvent(self, event) -> None:
        if self.current_run_id:
            self.runtime.cancel(self.current_run_id)
        self.task_queue.close()
        super().closeEvent(event)

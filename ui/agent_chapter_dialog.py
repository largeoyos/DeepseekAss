from __future__ import annotations

from PyQt6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QTabWidget, QTextBrowser, QVBoxLayout, QWidget


class AgentChapterPlanDialog(QDialog):
    """Read-only approval dialog for a prepared Agent chapter plan."""

    def __init__(self, parent, request, plan) -> None:
        super().__init__(parent)
        self.request = request
        self.plan = plan
        self.setWindowTitle(f"确认 Agent 章节计划 · 第{request.chapter_num}章")
        self.resize(900, 720)
        layout = QVBoxLayout(self)
        notice = QLabel("确认后将自动生成正文、执行监督修复、写入章节树并维护世界书。取消不会修改正式数据。")
        notice.setWordWrap(True)
        layout.addWidget(notice)
        tabs = QTabWidget()
        plan_view = QTextBrowser()
        plan_view.setPlainText(plan.render())
        tabs.addTab(plan_view, "章节计划")
        context_view = QTextBrowser()
        context_view.setPlainText(plan.context_report.get("content", ""))
        tabs.addTab(context_view, "实际注入上下文")
        source_view = QTextBrowser()
        lines = [
            f"候选上下文：{plan.context_report.get('candidate_chars', 0)} 字",
            f"实际注入：{plan.context_report.get('injected_chars', 0)} 字",
            f"省略：{plan.context_report.get('omitted_chars', 0)} 字",
            "",
        ]
        for item in plan.context_report.get("sources", []):
            lines.append(f"- {item.get('title', '')}: {len(item.get('content', ''))} 字；来源={item.get('source', '')}；原因={item.get('reason', '')}；省略={item.get('omitted_chars', 0)}")
        source_view.setPlainText("\n".join(lines))
        tabs.addTab(source_view, "来源与预算")
        layout.addWidget(tabs, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("确认并生成正文")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

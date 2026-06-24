from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
)

from ui.dialog_utils import apply_responsive_dialog_size


class AgentPolishPlanDialog(QDialog):
    """Read-only approval dialog for a prepared Agent polish plan."""

    def __init__(self, parent, request, plan) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"确认 Agent 润色方案 · 第{request.chapter_num}章")
        layout = QVBoxLayout(self)
        notice = QLabel(
            "确认后将润色完整原文并执行保真审查。润色版会保存为新版本，"
            "不会自动切换为活跃版本。"
        )
        notice.setWordWrap(True)
        layout.addWidget(notice)

        tabs = QTabWidget()
        plan_view = QTextBrowser()
        plan_view.setPlainText(plan.render())
        tabs.addTab(plan_view, "润色方案")

        context_view = QTextBrowser()
        context_view.setPlainText(plan.context_report.get("content", ""))
        tabs.addTab(context_view, "连续性上下文")

        source_view = QTextBrowser()
        lines = [
            f"候选上下文：{plan.context_report.get('candidate_chars', 0)} 字",
            f"实际注入：{plan.context_report.get('injected_chars', 0)} 字",
            f"省略：{plan.context_report.get('omitted_chars', 0)} 字",
            "",
        ]
        for skill in plan.context_report.get("skills", []):
            lines.append(f"- Skill {skill.get('name', skill.get('id', ''))} v{skill.get('version', '1')}；来源={skill.get('scope', '')}；原因={skill.get('reason', '')}")
        if plan.context_report.get("skills"):
            lines.append("")
        for item in plan.context_report.get("sources", []):
            lines.append(
                f"- {item.get('title', '')}: {len(item.get('content', ''))} 字；"
                f"来源={item.get('source', '')}；原因={item.get('reason', '')}；"
                f"省略={item.get('omitted_chars', 0)}"
            )
        source_view.setPlainText("\n".join(lines))
        tabs.addTab(source_view, "来源与预算")
        layout.addWidget(tabs, 1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("确认并润色全文")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        apply_responsive_dialog_size(self, 840, 620, minimum_width=460, minimum_height=320)

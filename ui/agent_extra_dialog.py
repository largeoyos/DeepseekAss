from __future__ import annotations

from PyQt6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit,
    QSpinBox, QTabWidget, QTextBrowser, QTextEdit, QVBoxLayout, QWidget,
)


class AgentExtraRequestDialog(QDialog):
    def __init__(self, parent, *, start_node=None, end_node=None, reference_node=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Agent 插入番外")
        self.resize(680, 580)
        layout = QVBoxLayout(self)
        anchor = QLabel(
            f"起点：{(start_node or {}).get('display_label') or (start_node or {}).get('title', '(无)')}\n"
            f"终点：{(end_node or {}).get('display_label') or (end_node or {}).get('title', '(无)')}\n"
            f"参考：{(reference_node or {}).get('display_label') or (reference_node or {}).get('title', '(无)')}"
        )
        anchor.setWordWrap(True)
        layout.addWidget(anchor)
        form = QFormLayout()
        self.type_combo = QComboBox()
        self.type_combo.addItem("丰富内容", "enrichment")
        self.type_combo.addItem("IF 线", "if_line")
        self.type_combo.addItem("前传", "prequel")
        self.type_combo.addItem("后传", "sequel")
        self.title_edit = QLineEdit()
        self.title_edit.setPlaceholderText("番外标题")
        self.plot_edit = QTextEdit()
        self.plot_edit.setPlaceholderText("输入大致剧情、关键场景或希望发生的事件")
        self.requirement_edit = QTextEdit()
        self.requirement_edit.setPlaceholderText("额外写作要求，可留空")
        self.requirement_edit.setMaximumHeight(100)
        self.words = QSpinBox()
        self.words.setRange(500, 50000)
        self.words.setValue(5000)
        self.words.setSingleStep(500)
        self.manual_entities = QLineEdit()
        self.manual_entities.setPlaceholderText("可选：世界书实体 ID，使用逗号分隔")
        form.addRow("类型", self.type_combo)
        form.addRow("标题", self.title_edit)
        form.addRow("大致剧情", self.plot_edit)
        form.addRow("写作要求", self.requirement_edit)
        form.addRow("目标字数", self.words)
        form.addRow("手动世界书", self.manual_entities)
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("让 Agent 规划")
        buttons.accepted.connect(self._accept_if_valid)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _accept_if_valid(self) -> None:
        if not self.title_edit.text().strip() or not self.plot_edit.toPlainText().strip():
            return
        self.accept()

    def values(self) -> dict:
        return {
            "extra_type": str(self.type_combo.currentData()),
            "title": self.title_edit.text().strip(),
            "plot": self.plot_edit.toPlainText().strip(),
            "requirement": self.requirement_edit.toPlainText().strip(),
            "target_words": self.words.value(),
            "manual_entity_ids": [item.strip() for item in self.manual_entities.text().replace("，", ",").split(",") if item.strip()],
        }


class AgentExtraPlanDialog(QDialog):
    def __init__(self, parent, request, plan) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"确认 Agent 番外计划 · {request.title}")
        self.resize(900, 720)
        layout = QVBoxLayout(self)
        notice = QLabel("确认后将生成正文、执行 Agent Supervision、保存番外节点、摘要、世界书快照和项目快照。")
        notice.setWordWrap(True)
        layout.addWidget(notice)
        tabs = QTabWidget()
        plan_view = QTextBrowser()
        plan_view.setPlainText(plan.render())
        tabs.addTab(plan_view, "番外计划")
        context_view = QTextBrowser()
        context_view.setPlainText(plan.context_report.get("content", ""))
        tabs.addTab(context_view, "实际注入上下文")
        source_view = QTextBrowser()
        lines = [
            f"候选：{plan.context_report.get('candidate_chars', 0)} 字",
            f"注入：{plan.context_report.get('injected_chars', 0)} 字",
            f"省略：{plan.context_report.get('omitted_chars', 0)} 字",
            "",
        ]
        for item in plan.context_report.get("sources", []):
            lines.append(f"- {item.get('source', '')}: {item.get('id') or item.get('node_id', '')}；{item.get('reason', '')}")
        source_view.setPlainText("\n".join(lines))
        tabs.addTab(source_view, "来源与预算")
        layout.addWidget(tabs, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("确认并生成")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

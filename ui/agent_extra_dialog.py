from __future__ import annotations

from PyQt6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit,
    QSpinBox, QTabWidget, QTextBrowser, QTextEdit, QVBoxLayout, QWidget,
)

from ui.dialog_utils import apply_responsive_dialog_size


class AgentExtraRequestDialog(QDialog):
    def __init__(
        self,
        parent,
        *,
        nodes: list[dict] | None = None,
        initial_node_id: str = "",
    ) -> None:
        super().__init__(parent)
        self._nodes = {
            str(node.get("id", "")): node
            for node in (nodes or [])
            if node.get("id") and not node.get("virtual")
        }
        self.setWindowTitle("Agent 插入番外")
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.type_combo = QComboBox()
        self.type_combo.addItem("丰富内容", "enrichment")
        self.type_combo.addItem("IF 线", "if_line")
        self.type_combo.addItem("前传", "prequel")
        self.type_combo.addItem("后传", "sequel")
        self.start_combo = QComboBox()
        self.end_combo = QComboBox()
        self.reference_combo = QComboBox()
        for node in self._ordered_nodes():
            label = self._node_label(node)
            node_id = str(node["id"])
            self.start_combo.addItem(label, node_id)
            self.reference_combo.addItem(label, node_id)
        self._select_combo_data(self.start_combo, initial_node_id)
        self._select_combo_data(self.reference_combo, initial_node_id)
        self.start_combo.currentIndexChanged.connect(self._refresh_end_nodes)
        self.type_combo.currentIndexChanged.connect(self._apply_type_state)
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
        form.addRow("起点", self.start_combo)
        form.addRow("终点", self.end_combo)
        form.addRow("前/后传参考点", self.reference_combo)
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
        self._refresh_end_nodes()
        self._apply_type_state()
        apply_responsive_dialog_size(self, 680, 580, minimum_width=460, minimum_height=360)

    def _accept_if_valid(self) -> None:
        if not self.title_edit.text().strip() or not self.plot_edit.toPlainText().strip():
            return
        extra_type = str(self.type_combo.currentData())
        if extra_type in {"enrichment", "if_line"} and (
            not self.start_combo.currentData() or not self.end_combo.currentData()
        ):
            return
        if extra_type in {"prequel", "sequel"} and not self.reference_combo.currentData():
            return
        self.accept()

    def values(self) -> dict:
        extra_type = str(self.type_combo.currentData())
        return {
            "extra_type": extra_type,
            "start_node_id": str(self.start_combo.currentData() or "") if extra_type in {"enrichment", "if_line"} else "",
            "end_node_id": str(self.end_combo.currentData() or "") if extra_type in {"enrichment", "if_line"} else "",
            "reference_node_id": str(self.reference_combo.currentData() or "") if extra_type in {"prequel", "sequel"} else "",
            "title": self.title_edit.text().strip(),
            "plot": self.plot_edit.toPlainText().strip(),
            "requirement": self.requirement_edit.toPlainText().strip(),
            "target_words": self.words.value(),
            "manual_entity_ids": [item.strip() for item in self.manual_entities.text().replace("，", ",").split(",") if item.strip()],
        }

    def _ordered_nodes(self) -> list[dict]:
        return sorted(
            self._nodes.values(),
            key=lambda item: (
                float(item.get("display_order", item.get("chapter_num", 0)) or 0),
                str(item.get("title", "")),
            ),
        )

    @staticmethod
    def _node_label(node: dict) -> str:
        display = node.get("display_label") or f"第{node.get('chapter_num', 0)}章"
        return f"{display} · {node.get('title', '')}"

    @staticmethod
    def _select_combo_data(combo: QComboBox, value: str) -> None:
        index = combo.findData(value)
        if index >= 0:
            combo.setCurrentIndex(index)

    def _refresh_end_nodes(self) -> None:
        start_id = str(self.start_combo.currentData() or "")
        start = self._nodes.get(start_id, {})
        previous = str(self.end_combo.currentData() or "")
        self.end_combo.clear()
        for child_id in start.get("children_ids", []) or []:
            child = self._nodes.get(str(child_id))
            if child and child.get("tree_id") == start.get("tree_id"):
                self.end_combo.addItem(self._node_label(child), str(child["id"]))
        self._select_combo_data(self.end_combo, previous)

    def _apply_type_state(self) -> None:
        pair_mode = str(self.type_combo.currentData()) in {"enrichment", "if_line"}
        self.start_combo.setEnabled(pair_mode)
        self.end_combo.setEnabled(pair_mode)
        self.reference_combo.setEnabled(not pair_mode)

class AgentExtraPlanDialog(QDialog):
    def __init__(self, parent, request, plan) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"确认 Agent 番外计划 · {request.title}")
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
        apply_responsive_dialog_size(self, 840, 620, minimum_width=460, minimum_height=320)

from __future__ import annotations

import threading

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QMessageBox,
    QPushButton, QTabWidget, QTextBrowser, QTextEdit, QVBoxLayout,
)

from core.agent.chapter_generation import AgentChapterPlan
from ui.dialog_utils import apply_responsive_dialog_size


class AgentChapterPlanDialog(QDialog):
    """Approve, select, and revise an Agent chapter plan before prose generation."""

    revision_ready = pyqtSignal(object)
    revision_failed = pyqtSignal(str)

    def __init__(self, parent, request, plan, *, revise_callback=None) -> None:
        super().__init__(parent)
        self.request = request
        self.plan = plan
        self._revise_callback = revise_callback
        self._candidates = list(plan.candidate_plans or [plan.to_dict(include_candidates=False)])
        self._has_multiple = len(self._candidates) > 1
        self.selected_plan = plan
        self.revision_ready.connect(self._on_revision_ready)
        self.revision_failed.connect(self._on_revision_failed)
        self.setWindowTitle(f"确认 Agent 章节计划 · 第{request.chapter_num}章")
        layout = QVBoxLayout(self)
        notice = QLabel(
            "这是正文生成前的章节规划。你可以先提出局部修改要求，让 AI 修改当前方案；"
            "只有点击确认后才会开始生成正文。"
        )
        notice.setWordWrap(True)
        layout.addWidget(notice)

        self._selector = QComboBox()
        self._rebuild_selector()
        if self._has_multiple:
            selector_row = QHBoxLayout()
            selector_row.addWidget(QLabel("章节方案："))
            self._selector.currentIndexChanged.connect(self._select_candidate)
            selector_row.addWidget(self._selector, 1)
            layout.addLayout(selector_row)

        self._recommendation = QLabel()
        self._recommendation.setWordWrap(True)
        if self._has_multiple:
            layout.addWidget(self._recommendation)

        tabs = QTabWidget()
        self._plan_view = QTextBrowser()
        tabs.addTab(self._plan_view, "章节计划")
        self._context_view = QTextBrowser()
        tabs.addTab(self._context_view, "实际注入上下文")
        self._source_view = QTextBrowser()
        tabs.addTab(self._source_view, "来源与预算")
        layout.addWidget(tabs, 1)

        revision_label = QLabel("计划修改要求（只修改当前显示的方案）：")
        layout.addWidget(revision_label)
        revision_row = QHBoxLayout()
        self._revision_input = QTextEdit()
        self._revision_input.setMaximumHeight(92)
        self._revision_input.setPlaceholderText(
            "例如：保留前两个场景，只把第三场的选择改成主角主动隐瞒线索，并补上对应代价。"
        )
        revision_row.addWidget(self._revision_input, 1)
        self._revision_button = QPushButton("让 AI 修改当前计划")
        self._revision_button.clicked.connect(self._request_revision)
        revision_row.addWidget(self._revision_button)
        layout.addLayout(revision_row)
        self._revision_status = QLabel("修改会更新当前计划，不会重新生成其他方案或正文。")
        self._revision_status.setWordWrap(True)
        layout.addWidget(self._revision_status)

        recommended_index = self._recommended_index()
        self._selector.setCurrentIndex(recommended_index)
        self._select_candidate(recommended_index)

        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self._buttons.button(QDialogButtonBox.StandardButton.Ok).setText(
            "确认所选方案并生成正文" if self._has_multiple else "确认并生成正文"
        )
        self._buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)
        layout.addWidget(self._buttons)
        apply_responsive_dialog_size(self, 900, 720, minimum_width=540, minimum_height=430)

    def _recommended_index(self) -> int:
        recommended = self.plan.recommended_candidate_id or self.plan.candidate_id
        return next(
            (index for index, item in enumerate(self._candidates)
             if item.get("candidate_id") == recommended),
            0,
        )

    def _rebuild_selector(self) -> None:
        self._selector.blockSignals(True)
        self._selector.clear()
        for index, candidate in enumerate(self._candidates, 1):
            strategy = str(candidate.get("strategy", "") or f"方案 {index}")
            critic = candidate.get("critic", {}) if isinstance(candidate.get("critic"), dict) else {}
            score_keys = ("causality", "character_agency", "surprise", "main_plot_value")
            scores = [int(critic[key]) for key in score_keys if str(critic.get(key, "")).isdigit()]
            score_text = f" · Critic {sum(scores)}/{len(scores) * 10}" if scores else ""
            self._selector.addItem(f"{index}. {strategy}{score_text}", candidate.get("candidate_id", ""))
        self._selector.blockSignals(False)

    def _select_candidate(self, index: int) -> None:
        if index < 0 or index >= len(self._candidates):
            return
        selected = AgentChapterPlan.from_dict(self._candidates[index])
        selected.candidate_plans = self._candidates
        selected.recommended_candidate_id = self.plan.recommended_candidate_id
        self.selected_plan = selected
        critic = selected.critic if isinstance(selected.critic, dict) else {}
        score_text = "；".join(
            f"{label} {critic[key]}/10"
            for key, label in (
                ("causality", "因果"), ("character_agency", "主动性"),
                ("surprise", "意外性"), ("main_plot_value", "主线价值"),
            )
            if key in critic
        )
        reason = selected.selection_reason or critic.get("reason", "") or "当前方案可继续修改后确认。"
        risk = critic.get("risk", "")
        self._recommendation.setText(
            f"Critic 评估：{score_text or '当前方案已人工修改或未评分'}\n说明：{reason}"
            + (f"\n风险提示：{risk}" if risk else "")
        )
        self._plan_view.setPlainText(selected.render())
        self._context_view.setPlainText(selected.context_report.get("content", ""))
        lines = [
            f"候选上下文：{selected.context_report.get('candidate_chars', 0)} 字",
            f"实际注入：{selected.context_report.get('injected_chars', 0)} 字",
            f"省略：{selected.context_report.get('omitted_chars', 0)} 字",
            "",
        ]
        for skill in selected.context_report.get("skills", []):
            lines.append(
                f"- Skill {skill.get('name', skill.get('id', ''))} v{skill.get('version', '1')}；"
                f"来源={skill.get('scope', '')}；原因={skill.get('reason', '')}"
            )
        if selected.context_report.get("skills"):
            lines.append("")
        for item in selected.context_report.get("sources", []):
            lines.append(
                f"- {item.get('title', '')}: {len(item.get('content', ''))} 字；"
                f"来源={item.get('source', '')}；原因={item.get('reason', '')}；"
                f"省略={item.get('omitted_chars', 0)}"
            )
        self._source_view.setPlainText("\n".join(lines))

    def _request_revision(self) -> None:
        instruction = self._revision_input.toPlainText().strip()
        if not instruction:
            QMessageBox.warning(self, "缺少修改要求", "请填写需要修改章节计划的具体要求。")
            return
        if self._revise_callback is None:
            QMessageBox.warning(self, "无法修改", "当前没有可用的章节计划修改服务。")
            return
        current = self.selected_plan
        self._set_revision_busy(True)
        self._revision_status.setText("AI 正在基于当前计划做局部修改，请稍候……")

        def run() -> None:
            try:
                revised = self._revise_callback(current, instruction)
                self.revision_ready.emit(revised)
            except Exception as exc:
                self.revision_failed.emit(str(exc))

        threading.Thread(target=run, daemon=True).start()

    def _set_revision_busy(self, busy: bool) -> None:
        self._revision_button.setEnabled(not busy)
        self._selector.setEnabled(not busy)
        self._buttons.setEnabled(not busy)

    def _on_revision_ready(self, revised) -> None:
        self.plan = revised
        self._candidates = list(
            revised.candidate_plans or [revised.to_dict(include_candidates=False)]
        )
        self._has_multiple = len(self._candidates) > 1
        self._rebuild_selector()
        index = next(
            (position for position, item in enumerate(self._candidates)
             if item.get("candidate_id") == revised.candidate_id),
            0,
        )
        self._selector.setCurrentIndex(index)
        self._select_candidate(index)
        self._revision_input.clear()
        self._revision_status.setText("当前章节计划已按要求修改，可继续修改或确认生成正文。")
        self._set_revision_busy(False)

    def _on_revision_failed(self, error: str) -> None:
        self._revision_status.setText(f"计划修改失败：{error}")
        self._set_revision_busy(False)
        QMessageBox.warning(self, "章节计划修改失败", error)

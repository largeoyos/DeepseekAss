"""
续写小说对话框模块
提供分析结果展示、方向选择和续写参数设置等对话框
"""

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QTabWidget,
    QTextEdit, QPushButton, QLabel, QMessageBox,
    QRadioButton, QButtonGroup, QSpinBox, QGroupBox,
)
from PyQt6.QtCore import Qt


CONTINUATION_SETTING_PROMPT = """请深度分析以下文本，提取其中的世界观设定、角色信息。请用中文输出，格式如下：

【核心设定】
核心世界观规则、力量体系、时代背景等

【角色列表】
- 角色名：性格/身份/能力简述
- 角色名：性格/身份/能力简述

【角色关系】
- A 和 B：关系类型（如师徒/仇敌/恋人）

【未解线索】
文中埋下的伏笔、未解决的冲突等

原文：
{text}"""

CONTINUATION_PLOT_PROMPT = """请总结以下文本的完整剧情大纲，按时间顺序列出主要情节节点：

【剧情概要】
1. 事件一
2. 事件二
...

【当前状态】
故事进行到哪里，角色们处于什么状态

原文：
{text}"""

SUGGESTION_PROMPT = """你是一位资深小说编辑。请根据以下故事设定和剧情概要，给出 3-5 个下一章的发展方向建议。

每个建议需要：
1. 方向标题（10字以内）
2. 核心冲突或看点
3. 大致情节走向（50字以内）

要求：建议有创意且符合已有设定。

核心设定：
{setting}

剧情概要：
{plot}

请按以下格式输出（每行一个方向）：
方向1：标题 | 核心冲突 | 情节走向
方向2：标题 | 核心冲突 | 情节走向"""


def analyze_source_text(client, source_text: str, model: str) -> tuple[str, str]:
    """
    分析源文档，返回 (setting_summary, plot_outline)

    Args:
        client: OpenAI 客户端
        source_text: 源文档全文
        model: 模型名称

    Returns:
        (核心设定摘要, 剧情大纲)
    """
    # 截取前 6000 字分析
    sample = source_text[:6000]

    setting = ""
    plot = ""

    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": CONTINUATION_SETTING_PROMPT.format(text=sample)}],
            max_tokens=2000,
            temperature=0.3,
        )
        setting = resp.choices[0].message.content or ""
    except Exception:
        setting = "[分析失败]"

    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": CONTINUATION_PLOT_PROMPT.format(text=sample)}],
            max_tokens=2000,
            temperature=0.3,
        )
        plot = resp.choices[0].message.content or ""
    except Exception:
        plot = "[分析失败]"

    return setting, plot


def suggest_directions(client, setting: str, plot: str, model: str) -> list[str]:
    """
    AI 建议 3-5 个发展方向

    Returns:
        方向描述列表
    """
    prompt = SUGGESTION_PROMPT.format(setting=setting[:1500], plot=plot[:1500])
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=1500,
            temperature=0.8,
        )
        text = resp.choices[0].message.content or ""
        directions = [line.strip() for line in text.split("\n") if line.strip() and ("方向" in line or "：" in line)]
        return directions[:5] if directions else [text[:200]]
    except Exception:
        return ["建议生成失败，请手动指定剧情"]


class ContinuationAnalysisDialog(QDialog):
    """
    续写分析结果对话框
    展示核心设定和剧情大纲，用户可编辑，然后选择续写方式
    """

    def __init__(self, parent, setting: str, plot_outline: str,
                 on_suggest, on_specify):
        """
        Args:
            parent: 父窗口
            setting: 核心设定文本
            plot_outline: 剧情大纲文本
            on_suggest: 回调 (setting, plot) -> 自由续写模式启动
            on_specify: 回调 (setting, plot) -> 指定续写模式启动
        """
        super().__init__(parent)
        self._setting = setting
        self._plot = plot_outline
        self._on_suggest = on_suggest
        self._on_specify = on_specify
        self.setWindowTitle("续写前 - 设定与剧情概要")
        self.resize(650, 500)
        self.setModal(True)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        hint = QLabel("以下是从源文档中提取的设定与剧情概要，可编辑修改后选择续写方式。")
        hint.setStyleSheet("color: #888; font-size: 12px; padding: 4px 0;")
        layout.addWidget(hint)

        tabs = QTabWidget()
        self._setting_edit = QTextEdit()
        self._setting_edit.setPlainText(self._setting)
        self._setting_edit.setStyleSheet("background: #2d2d2d; color: #e0e0e0; border: 1px solid #444;")
        tabs.addTab(self._setting_edit, "核心设定")

        self._plot_edit = QTextEdit()
        self._plot_edit.setPlainText(self._plot)
        self._plot_edit.setStyleSheet("background: #2d2d2d; color: #e0e0e0; border: 1px solid #444;")
        tabs.addTab(self._plot_edit, "剧情概要")
        layout.addWidget(tabs)

        # 字数设置
        word_row = QHBoxLayout()
        word_row.addWidget(QLabel("目标字数："))
        self._word_count = QSpinBox()
        self._word_count.setRange(100, 100000)
        self._word_count.setValue(2000)
        self._word_count.setSingleStep(500)
        self._word_count.setSuffix(" 字")
        word_row.addWidget(self._word_count)
        word_row.addStretch()
        layout.addLayout(word_row)

        # 按钮
        btn_row = QHBoxLayout()
        suggest_btn = QPushButton("🎲 AI 建议发展方向")
        suggest_btn.setStyleSheet("""
            QPushButton { background: #2d6b2d; color: white; border: none;
                          border-radius: 6px; padding: 8px 16px; font-weight: bold; }
            QPushButton:hover { background: #3d8b3d; }
        """)
        suggest_btn.clicked.connect(self._on_suggest)
        btn_row.addWidget(suggest_btn)

        specify_btn = QPushButton("📝 我指定剧情")
        specify_btn.setStyleSheet("""
            QPushButton { background: #6b4d2d; color: white; border: none;
                          border-radius: 6px; padding: 8px 16px; font-weight: bold; }
            QPushButton:hover { background: #8b6d3d; }
        """)
        specify_btn.clicked.connect(self._on_specify)
        btn_row.addWidget(specify_btn)

        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.close)
        btn_row.addWidget(cancel_btn)

        layout.addLayout(btn_row)

    def _on_suggest(self):
        setting = self._setting_edit.toPlainText().strip()
        plot = self._plot_edit.toPlainText().strip()
        word_count = self._word_count.value()
        self._on_suggest(setting, plot, word_count)

    def _on_specify(self):
        setting = self._setting_edit.toPlainText().strip()
        plot = self._plot_edit.toPlainText().strip()
        word_count = self._word_count.value()
        self._on_specify(setting, plot, word_count)


class DirectionSelectionDialog(QDialog):
    """AI 建议发展方向的选择对话框"""

    def __init__(self, parent, directions: list[str]):
        super().__init__(parent)
        self.selected_direction: str | None = None
        self.setWindowTitle("选择发展方向")
        self.resize(500, 350)
        self.setModal(True)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("请选择下一章的发展方向："))

        self._group = QButtonGroup(self)
        for i, d in enumerate(directions):
            btn = QRadioButton(d)
            self._group.addButton(btn, i)
            btn.setStyleSheet("padding: 6px 0; font-size: 13px;")
            layout.addWidget(btn)
            if i == 0:
                btn.setChecked(True)

        btn_row = QHBoxLayout()
        confirm_btn = QPushButton("确定")
        confirm_btn.clicked.connect(self._on_confirm)
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.close)
        btn_row.addStretch()
        btn_row.addWidget(confirm_btn)
        btn_row.addWidget(cancel_btn)
        layout.addLayout(btn_row)

    def _on_confirm(self):
        checked_id = self._group.checkedId()
        if checked_id >= 0:
            btn = self._group.button(checked_id)
            self.selected_direction = btn.text() if btn else None
        self.accept()

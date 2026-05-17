"""
续写小说对话框模块
提供分析结果展示、方向选择和续写参数设置等对话框
"""

import os
import re

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QTabWidget,
    QTextEdit, QPushButton, QLabel, QMessageBox,
    QRadioButton, QButtonGroup, QSpinBox, QGroupBox,
    QListWidget, QSplitter, QFrame, QAbstractItemView,
    QListWidgetItem,
)
from PyQt6.QtCore import Qt

SUGGESTION_PROMPT = """你是一位资深小说编辑。请根据以下完整世界观设定、待回收伏笔和剧情进展，为下一章提供 3-5 个发展方向建议。

每个建议包含：
1. 方向标题（10字以内）
2. 核心看点或情绪基调
3. 大致情节走向（50字以内）

【核心规则——必须遵守】
- 优先处理【待回收伏笔】中列出的未收束伏笔，每个方向至少回收或推进 1-2 条伏笔
- 如果【待回收伏笔】列表非空，禁止提出完全无关的新方向；所有方向必须与已有伏笔产生关联
- 如果【待回收伏笔】为空，则可以自由展开新剧情或深化已有的剧情线
- 每个方向必须严格服务于全文的整体基调，延续已有风格和氛围
- 情节推进方式与前文的叙事节奏一致——温馨则延续温暖，悬疑则保持张力，而非强行制造冲突
- 聚焦于角色成长、人物关系深化、伏笔回收或世界观展开
- 如果故事本身有冲突线，合理推进即可；如果故事是日常/氛围向，不要凭空制造戏剧冲突
- 避免涉及现实政治、社会批判、历史影射等严肃议题
- 充分利用世界观中的角色、地点、规则和关键设定来构思

【完整世界观设定】
{world}

【核心背景】
{setting}

【剧情进展】
{plot}

请按以下格式输出（每行一个方向）：
方向1：标题 | 核心看点 | 情节走向
方向2：标题 | 核心看点 | 情节走向"""


def _safe_format(template: str, **kwargs) -> str:
    """安全的模板替换，值中含 { 或 } 不会导致崩溃。"""
    result = template
    for key, value in kwargs.items():
        result = result.replace("{" + key + "}", value)
    result = result.replace("{{", "{").replace("}}", "}")
    return result


def analyze_source_text(client, source_text: str, model: str, global_user_prompt: str = "") -> dict:
    """
    新版分析：使用 AI 语义分段 + 结构化提取，返回可加载的小说设定。

    替换旧版双 API 调用（只读前 6000 字 + 纯文本输出）。

    Args:
        client: OpenAI 客户端
        source_text: 源文档全文
        model: 模型名称
        global_user_prompt: 用户全局提示词（偏好参考）

    Returns:
        {
            "world_data": {      # extract_world_bible_from_segments 的输出
                "characters": [...], "locations": [...],
                "rules": [...], "timeline": [...], "plot_threads": [...]
            },
            "settings": {        # generate_novel_settings_from_world_bible 的输出
                "background_story": str,
                "protagonist_bio": str,
                "writing_demand": str,
            },
            "segments": [(title, content), ...],  # AI 识别的段落
        }
    """
    from utils.summarize import segment_by_ai, extract_world_bible_from_segments, generate_novel_settings_from_world_bible

    # 1. AI 语义分段
    segments = segment_by_ai(client, source_text, model, global_user_prompt=global_user_prompt)

    # 2. 逐段提取世界观
    world_data = extract_world_bible_from_segments(client, segments, model, global_user_prompt=global_user_prompt)

    # 3. 生成小说设定
    settings = generate_novel_settings_from_world_bible(client, world_data, model, global_user_prompt=global_user_prompt)

    return {
        "world_data": world_data,
        "settings": settings,
        "segments": segments,
    }


def _build_world_summary(world_data: dict | None) -> str:
    """从世界书数据构建完整的世界观文本摘要"""
    if not world_data:
        return "（无）"

    parts = []

    chars = world_data.get("characters", [])
    if chars:
        lines = []
        for c in chars[:8]:
            info = c.get("name", "?")
            traits = c.get("traits", "")
            if traits:
                info += f" | {traits[:150]}"
            motivation = c.get("motivation", "")
            if motivation:
                info += f" | 动机：{motivation[:80]}"
            arc = c.get("arc", "")
            if arc:
                info += f" | 弧光：{arc[:60]}"
            status = c.get("status", "alive")
            if status != "alive":
                info += f" | 状态：{status}"
            rels = c.get("relationships", [])
            for r in rels[:2]:
                info += f" | {r.get('type', '')}→{r.get('target', '')}"
            lines.append(f"- {info}")
        parts.append("【角色】\n" + "\n".join(lines))

    locs = world_data.get("locations", [])
    if locs:
        lines = []
        for l in locs[:6]:
            info = l.get("name", "?")
            desc = l.get("description", "")
            if desc:
                info += f"：{desc[:120]}"
            atmos = l.get("atmosphere", "")
            if atmos:
                info += f"（{atmos[:40]}）"
            lines.append(f"- {info}")
        parts.append("【地点】\n" + "\n".join(lines))

    rules = world_data.get("rules", [])
    if rules:
        lines = [f"- {r[:150]}" for r in rules[:5]]
        parts.append("【世界规则】\n" + "\n".join(lines))

    threads_any = world_data.get("plot_threads", [])
    if not threads_any:
        threads_any = world_data.get("active_plot_threads", [])
    if threads_any:
        lines = []
        for p in threads_any[:6]:
            status_tag = f"[{p.get('status', 'active')}]"
            chars_in = p.get("involved_characters", [])
            char_str = f" 涉及：{', '.join(chars_in[:4])}" if chars_in else ""
            lines.append(f"- {p.get('name', '?')} {status_tag}: {p.get('description', '')[:150]}{char_str}")
        if lines:
            parts.append("【剧情线】\n" + "\n".join(lines))

    timeline = world_data.get("timeline", [])
    if timeline:
        recent = timeline[-5:]
        lines = []
        for t in recent:
            event = t.get("event", "")[:100]
            sig = t.get("significance", "")
            hint = ""
            fh = t.get("foreshadowing_hints", [])
            if fh:
                hint = f" [伏笔：{fh[0][:40]}]"
            lines.append(f"- {event}{' - ' + sig[:60] if sig else ''}{hint}")
        parts.append("【近期事件】\n" + "\n".join(lines))

    # 全局伏笔（待回收）
    foreshadowing = world_data.get("global_foreshadowing", [])
    if foreshadowing:
        lines = []
        for f in foreshadowing[:8]:
            hint = f.get("hint", "")[:80]
            relates = f.get("relates_to", "")
            if relates:
                lines.append(f"- 🔮 {hint} → 关联：{relates[:40]}")
            else:
                lines.append(f"- 🔮 {hint}")
        parts.append("【待回收伏笔】\n" + "\n".join(lines))

    # 关键世界观设定段落
    wb_passages = world_data.get("key_worldbuilding", [])
    if wb_passages:
        lines = []
        for item in wb_passages[:4]:
            topic = item.get("topic", "")[:30]
            passage = item.get("passage", "")[:100]
            if topic and passage:
                lines.append(f"- {topic}：{passage}")
        if lines:
            parts.append("【关键设定】\n" + "\n".join(lines))

    return "\n\n".join(parts)


def suggest_directions(client, setting: str, plot: str, model: str,
                       world_data: dict | None = None,
                       global_user_prompt: str = "") -> list[str]:
    """
    AI 建议 3-5 个发展方向

    Args:
        world_data: 世界书数据（含角色/地点/规则/剧情线等）
        global_user_prompt: 用户全局提示词（偏好参考）

    Returns:
        方向描述列表
    """
    world_summary = _build_world_summary(world_data)
    prompt = _safe_format(
        SUGGESTION_PROMPT,
        world=world_summary[:2000],
        setting=setting[:1500],
        plot=plot[:1500],
    )
    if global_user_prompt.strip():
        prompt += f"\n\n用户偏好参考: {global_user_prompt}"
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
    续写分析结果对话框（新版）
    展示结构化提取结果的摘要统计，用户可查看各标签页的数据
    """

    def __init__(self, parent, world_data: dict, settings: dict,
                 on_suggest, on_specify):
        """
        Args:
            parent: 父窗口
            world_data: extract_world_bible_from_segments 的输出
            settings: generate_novel_settings_from_world_bible 的输出
            on_suggest: 回调 (setting, plot) -> AI 建议方向
            on_specify: 回调 (setting, plot) -> 自行指定剧情
        """
        super().__init__(parent)
        self._world_data = world_data
        self._settings = settings
        self._suggest_callback = on_suggest
        self._specify_callback = on_specify
        self.setWindowTitle("分析完成 - 提取结果总览")
        self.resize(700, 550)
        self.setModal(True)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        # 统计摘要
        chars = self._world_data.get("characters", [])
        locs = self._world_data.get("locations", [])
        rules = self._world_data.get("rules", [])
        timeline = self._world_data.get("timeline", [])
        threads = self._world_data.get("plot_threads", [])

        summary = (
            f"✅ 分析完成！\n"
            f"  👥 角色 {len(chars)} 个  |  🏙️ 地点 {len(locs)} 个  |  📜 规则 {len(rules)} 条\n"
            f"  ⏱️ 事件 {len(timeline)} 个  |  🔗 剧情线 {len(threads)} 条\n\n"
            f"以下内容已保存到书架并加载到编辑面板，可在面板上直接修改。"
        )
        hint = QLabel(summary)
        hint.setStyleSheet("color: #ccc; font-size: 13px; padding: 8px; background: #333; border-radius: 4px;")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        # 标签页展示详细数据
        tabs = QTabWidget()

        # 设定总览标签页
        bg = self._settings.get("background_story", "") or "(未生成)"
        bio = self._settings.get("protagonist_bio", "") or "(未生成)"
        demand = self._settings.get("writing_demand", "") or "(未生成)"
        overview_text = (
            f"【核心设定】\n{bg}\n\n"
            f"【人物背景】\n{bio}\n\n"
            f"【写作要求】\n{demand}\n"
        )
        overview_edit = QTextEdit()
        overview_edit.setPlainText(overview_text)
        overview_edit.setStyleSheet("background: #2d2d2d; color: #e0e0e0; border: 1px solid #444;")
        tabs.addTab(overview_edit, "生成的小说设定")

        # 角色标签页
        char_text = "\n".join(
            f"- {c['name']}：{c.get('traits', '')[:200]}"
            for c in chars
        ) or "(未提取到角色)"
        char_edit = QTextEdit()
        char_edit.setPlainText(char_text)
        char_edit.setStyleSheet("background: #2d2d2d; color: #e0e0e0; border: 1px solid #444;")
        tabs.addTab(char_edit, f"角色 ({len(chars)})")

        # 地点标签页
        loc_text = "\n".join(
            f"- {l['name']}：{l.get('description', '')[:150]}"
            for l in locs
        ) or "(未提取到地点)"
        loc_edit = QTextEdit()
        loc_edit.setPlainText(loc_text)
        loc_edit.setStyleSheet("background: #2d2d2d; color: #e0e0e0; border: 1px solid #444;")
        tabs.addTab(loc_edit, f"地点 ({len(locs)})")

        # 规则标签页
        rule_text = "\n".join(f"- {r[:200]}" for r in rules) or "(未提取到规则)"
        rule_edit = QTextEdit()
        rule_edit.setPlainText(rule_text)
        rule_edit.setStyleSheet("background: #2d2d2d; color: #e0e0e0; border: 1px solid #444;")
        tabs.addTab(rule_edit, f"规则 ({len(rules)})")

        # 剧情线标签页
        thread_text = "\n".join(
            f"- {p['name']} [{p.get('status', 'active')}]: {p.get('description', '')[:150]}"
            for p in threads
        ) or "(未提取到剧情线)"
        thread_edit = QTextEdit()
        thread_edit.setPlainText(thread_text)
        thread_edit.setStyleSheet("background: #2d2d2d; color: #e0e0e0; border: 1px solid #444;")
        tabs.addTab(thread_edit, f"剧情线 ({len(threads)})")

        # 时间线标签页
        tl_text = "\n".join(
            f"- {t.get('event', '')[:150]} ({t.get('significance', '')[:100]})"
            for t in timeline
        ) or "(未提取到时间线)"
        tl_edit = QTextEdit()
        tl_edit.setPlainText(tl_text)
        tl_edit.setStyleSheet("background: #2d2d2d; color: #e0e0e0; border: 1px solid #444;")
        tabs.addTab(tl_edit, f"事件 ({len(timeline)})")

        layout.addWidget(tabs)

        # 操作按钮
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

        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.close)
        btn_row.addWidget(close_btn)

        layout.addLayout(btn_row)

    def _build_plot_context(self) -> str:
        """从 world_data 构建当前剧情摘要，供发展方向建议使用"""
        parts = []
        threads = self._world_data.get("plot_threads", [])
        if threads:
            active = [p for p in threads if p.get("status") == "active"]
            if active:
                parts.append("当前活跃剧情线：")
                for p in active[:3]:
                    parts.append(f"- {p['name']}: {p.get('description', '')[:60]}")
        timeline = self._world_data.get("timeline", [])
        if timeline:
            recent = timeline[-3:]
            parts.append("最近事件：")
            for t in recent:
                parts.append(f"- {t.get('event', '')[:60]}")
        return "\n".join(parts)

    def _on_suggest(self):
        wc = self.parent()._continue_word_count.value() if hasattr(self.parent(), '_continue_word_count') else 10000
        plot = self._build_plot_context()
        self._suggest_callback(
            self._settings.get("background_story", ""),
            plot,
            wc,
            self._world_data,
        )

    def _on_specify(self):
        wc = self.parent()._continue_word_count.value() if hasattr(self.parent(), '_continue_word_count') else 10000
        plot = self._build_plot_context()
        self._specify_callback(
            self._settings.get("background_story", ""),
            plot,
            wc,
        )


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


class SectionPreviewDialog(QDialog):
    """
    段落划分预览弹窗。

    在分析/续写前展示文本的段落划分（基于 # 一级标题或 AI 语义分段），
    用户确认后可选择重新分段，满意后再进入正式处理。

    Args:
        parent: 父窗口
        source_text: 文件模式的文本
        folder_path: 文件夹模式的路径（与 source_text 二选一）
        client: OpenAI 客户端（用于 AI 重新分段）
        model: 模型名称
        global_user_prompt: 用户偏好
        mode: "analyze" 或 "continue"
    """

    def __init__(self, parent,
                 source_text: str | None = None,
                 folder_path: str | None = None,
                 client=None, model: str = "",
                 global_user_prompt: str = "",
                 mode: str = "analyze"):
        super().__init__(parent)
        self._source_text = source_text
        self._folder_path = folder_path
        self._client = client
        self._model = model
        self._global_prompt = global_user_prompt
        self._mode = mode

        # 存储结果
        self.result: dict | None = None

        self._folder_files: list[dict] = []     # 文件夹模式下缓存每个文件的信息
        self._current_file_idx: int = -1        # 当前选中文件的索引

        self.setWindowTitle("📑 段落划分预览")
        self.resize(750, 520)
        self.setModal(True)
        self._build_ui()

        # 初始化数据
        if source_text:
            self._init_file_mode(source_text)
        elif folder_path:
            self._init_folder_mode(folder_path)

    def _build_ui(self):
        layout = QVBoxLayout(self)

        # 状态标签
        self._status_label = QLabel("准备中…")
        self._status_label.setStyleSheet(
            "color: #ccc; font-size: 13px; padding: 6px 10px; "
            "background: #333; border-radius: 4px;"
        )
        self._status_label.setWordWrap(True)
        layout.addWidget(self._status_label)

        # 主体：左右分栏
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # 左侧：段落列表
        left_frame = QFrame()
        left_layout = QVBoxLayout(left_frame)
        left_layout.setContentsMargins(0, 0, 0, 0)
        self._section_list = QListWidget()
        self._section_list.setStyleSheet("""
            QListWidget { background: #2d2d2d; color: #e0e0e0;
                          border: 1px solid #444; font-size: 13px; }
            QListWidget::item:selected { background: #3d7abb; color: white; }
        """)
        self._section_list.currentRowChanged.connect(self._on_section_selected)
        left_layout.addWidget(QLabel("段落列表"))
        left_layout.addWidget(self._section_list)
        splitter.addWidget(left_frame)

        # 右侧：内容预览
        right_frame = QFrame()
        right_layout = QVBoxLayout(right_frame)
        right_layout.setContentsMargins(0, 0, 0, 0)
        self._preview_edit = QTextEdit()
        self._preview_edit.setReadOnly(True)
        self._preview_edit.setStyleSheet("""
            QTextEdit { background: #2d2d2d; color: #e0e0e0;
                        border: 1px solid #444; font-size: 13px; }
        """)
        right_layout.addWidget(QLabel("内容预览"))
        right_layout.addWidget(self._preview_edit)
        splitter.addWidget(right_frame)

        splitter.setSizes([250, 500])
        layout.addWidget(splitter, stretch=1)

        # 底部按钮
        btn_row = QHBoxLayout()
        btn_row.addStretch()

        self._resegment_btn = QPushButton("🔄 AI 重新分段")
        self._resegment_btn.setStyleSheet("""
            QPushButton { background: #6b5a2d; color: white; border: none;
                          border-radius: 6px; padding: 8px 16px; font-weight: bold; }
            QPushButton:hover { background: #8b7a3d; }
        """)
        self._resegment_btn.clicked.connect(self._on_resegment)
        btn_row.addWidget(self._resegment_btn)

        confirm_text = "✅ 确认分析" if self._mode == "analyze" else "✅ 确认续写"
        self._confirm_btn = QPushButton(confirm_text)
        self._confirm_btn.setStyleSheet("""
            QPushButton { background: #2d6b2d; color: white; border: none;
                          border-radius: 6px; padding: 8px 16px; font-weight: bold; }
            QPushButton:hover { background: #3d8b3d; }
        """)
        self._confirm_btn.clicked.connect(self._on_confirm_analysis)
        btn_row.addWidget(self._confirm_btn)

        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)

        layout.addLayout(btn_row)

    # ── 文件模式 ──

    def _init_file_mode(self, text: str):
        """初始化文件模式：检测或 AI 分段"""
        from utils.summarize import detect_sections, segment_by_ai

        sections = detect_sections(text)
        if sections:
            self._sections_data = sections
            self._status_label.setText("✅ 文本已有正确的 # 段落划分")
            self._status_label.setStyleSheet(
                "color: #8f8; font-size: 13px; padding: 6px 10px; "
                "background: #2d3d2d; border-radius: 4px;"
            )
        else:
            self._status_label.setText("⏳ 未检测到段落划分，正在由 AI 自动分段…")
            self._status_label.setStyleSheet(
                "color: #ff8; font-size: 13px; padding: 6px 10px; "
                "background: #3d3d2d; border-radius: 4px;"
            )
            from PyQt6.QtWidgets import QApplication
            QApplication.processEvents()
            try:
                sections = segment_by_ai(self._client, text, self._model,
                                         global_user_prompt=self._global_prompt)
                self._sections_data = sections
                self._status_label.setText("⚠️ 已由 AI 自动分段，请确认是否合理")
                self._status_label.setStyleSheet(
                    "color: #ff8; font-size: 13px; padding: 6px 10px; "
                    "background: #3d3d2d; border-radius: 4px;"
                )
            except Exception as e:
                self._sections_data = [("全文", text)]
                self._status_label.setText(f"⚠️ AI 分段失败，将使用全文: {e}")
                self._status_label.setStyleSheet(
                    "color: #f88; font-size: 13px; padding: 6px 10px; "
                    "background: #3d2d2d; border-radius: 4px;"
                )

        self._populate_sections()

    def _populate_sections(self):
        """用 self._sections_data 刷新列表"""
        self._section_list.blockSignals(True)
        self._section_list.clear()
        for title, _ in self._sections_data:
            self._section_list.addItem(f"# {title}")
        self._section_list.blockSignals(False)
        if self._sections_data:
            self._section_list.setCurrentRow(0)

    def _on_section_selected(self, row: int):
        """段落选中 → 预览内容"""
        if 0 <= row < len(self._sections_data):
            _, content = self._sections_data[row]
            self._preview_edit.setPlainText(content[:2000])

    def _on_resegment(self):
        """AI 重新分段"""
        text = self._get_full_text()
        if not text:
            return

        from utils.summarize import segment_by_ai
        from PyQt6.QtWidgets import QApplication

        self._status_label.setText("⏳ AI 正在重新分段…")
        self._status_label.setStyleSheet(
            "color: #ff8; font-size: 13px; padding: 6px 10px; "
            "background: #3d3d2d; border-radius: 4px;"
        )
        self._resegment_btn.setEnabled(False)
        QApplication.processEvents()

        try:
            sections = segment_by_ai(self._client, text, self._model,
                                     global_user_prompt=self._global_prompt)
            self._sections_data = sections
            self._populate_sections()
            self._status_label.setText(f"⚠️ AI 分段完成，共 {len(sections)} 个段落")
        except Exception as e:
            self._status_label.setText(f"❌ 重新分段失败: {e}")
            self._status_label.setStyleSheet(
                "color: #f88; font-size: 13px; padding: 6px 10px; "
                "background: #3d2d2d; border-radius: 4px;"
            )
        finally:
            self._resegment_btn.setEnabled(True)

    def _get_full_text(self) -> str:
        """获取当前正在处理的完整文本"""
        if self._source_text is not None:
            return self._source_text
        if self._current_file_idx >= 0 and self._current_file_idx < len(self._folder_files):
            return self._folder_files[self._current_file_idx]["full_content"]
        return ""

    # ── 文件夹模式 ──

    def _init_folder_mode(self, folder_path: str):
        """初始化文件夹模式：扫描文件，逐个检测分段"""
        from utils.summarize import detect_sections, segment_by_ai
        from PyQt6.QtWidgets import QApplication

        ext_map = {".txt", ".md"}
        raw_files = []
        for fname in os.listdir(folder_path):
            if os.path.splitext(fname)[1].lower() not in ext_map:
                continue
            content = ""
            fpath = os.path.join(folder_path, fname)
            for enc in ("utf-8", "gbk"):
                try:
                    with open(fpath, "r", encoding=enc) as f:
                        content = f.read()
                    break
                except UnicodeDecodeError:
                    continue
            if not content:
                continue
            # 提取数字序号
            stem = os.path.splitext(fname)[0]
            nums = re.findall(r'\d+', stem)
            chapter_num = int(nums[0]) if nums else 0
            raw_files.append((chapter_num, fname, content))

        raw_files.sort(key=lambda x: x[0])

        self._folder_files = []
        for chapter_num, fname, content in raw_files:
            sections = detect_sections(content)
            needs_ai = not sections
            self._folder_files.append({
                "filename": fname,
                "chapter_num": chapter_num,
                "full_content": content,
                "sections": sections,
                "needs_ai": needs_ai,
                "checked": True,
            })

        # 对需要 AI 分段的文件自动处理
        auto_count = sum(1 for f in self._folder_files if f["needs_ai"])
        if auto_count > 0:
            self._status_label.setText(
                f"⏳ 正在由 AI 分段（{auto_count} 个文件需要处理）…"
            )
            QApplication.processEvents()
            for f in self._folder_files:
                if f["needs_ai"]:
                    try:
                        f["sections"] = segment_by_ai(
                            self._client, f["full_content"], self._model,
                            global_user_prompt=self._global_prompt,
                        )
                        f["needs_ai"] = False
                    except Exception:
                        f["sections"] = [("全文", f["full_content"])]

        self._status_label.setText(
            f"📂 共 {len(self._folder_files)} 个文件，勾选后确认分析"
        )
        self._status_label.setStyleSheet(
            "color: #ccc; font-size: 13px; padding: 6px 10px; "
            "background: #333; border-radius: 4px;"
        )
        self._populate_folder_list()

    def _populate_folder_list(self):
        """填充文件夹文件列表（带复选框）"""
        self._section_list.blockSignals(True)
        self._section_list.clear()
        self._section_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)

        for f in self._folder_files:
            item = QListWidgetItem(f"{f['filename']} ({len(f['sections'])}段)")
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if f["checked"] else Qt.CheckState.Unchecked)
            item.setData(Qt.ItemDataRole.UserRole, self._folder_files.index(f))
            self._section_list.addItem(item)

        self._section_list.blockSignals(False)
        self._section_list.currentRowChanged.connect(self._on_folder_file_selected)
        self._section_list.itemChanged.connect(self._on_folder_item_changed)

        # 清空预览
        self._preview_edit.clear()

    def _on_folder_item_changed(self, item):
        """复选框状态改变 → 同步到数据"""
        idx = item.data(Qt.ItemDataRole.UserRole)
        if idx is not None and 0 <= idx < len(self._folder_files):
            self._folder_files[idx]["checked"] = (item.checkState() == Qt.CheckState.Checked)

    def _on_folder_file_selected(self, row: int):
        """文件夹模式：选中文件 → 显示其段落"""
        if row < 0 or row >= len(self._folder_files):
            return
        self._current_file_idx = row
        f = self._folder_files[row]

        # 更新预览为文件内容（非段落）
        self._preview_edit.setPlainText(f["full_content"][:2000])

    def _on_confirm_analysis(self):
        """确认分析/续写 → 构建结果并关闭弹窗"""
        if self._source_text is not None:
            # 文件模式
            self.result = {
                "mode": "file",
                "sections": self._sections_data,
            }
            self.accept()
        elif self._folder_files:
            # 文件夹模式：只保留勾选的文件
            checked = [f for f in self._folder_files if f["checked"]]
            if not checked:
                QMessageBox.warning(self, "提示", "请至少勾选一个文件。")
                return
            self.result = {
                "mode": "folder",
                "files": checked,
            }
            self.accept()

    def get_result(self) -> dict | None:
        """获取确认后的结果"""
        return self.result

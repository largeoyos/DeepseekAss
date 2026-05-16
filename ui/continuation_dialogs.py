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

SUGGESTION_PROMPT = """你是一位资深小说编辑。请根据以下完整世界观设定和剧情进展，为下一章提供 3-5 个发展方向建议。

每个建议包含：
1. 方向标题（10字以内）
2. 核心冲突或看点
3. 大致情节走向（50字以内）

要求：
- 建议必须有创意，且严格符合已有设定，不引入矛盾
- 发展方向要与前文情节自然衔接，保持风格和基调一致
- 聚焦于角色成长、人物关系变化和剧情推进
- 避免涉及现实政治、社会批判、历史影射等严肃议题
- 充分利用世界观中的角色、地点和规则来构思

【完整世界观设定】
{world}

【核心背景】
{setting}

【剧情进展】
{plot}

请按以下格式输出（每行一个方向）：
方向1：标题 | 核心冲突 | 情节走向
方向2：标题 | 核心冲突 | 情节走向"""


def _safe_format(template: str, **kwargs) -> str:
    """安全的模板替换，值中含 { 或 } 不会导致崩溃。"""
    result = template
    for key, value in kwargs.items():
        result = result.replace("{" + key + "}", value)
    result = result.replace("{{", "{").replace("}}", "}")
    return result


def analyze_source_text(client, source_text: str, model: str) -> dict:
    """
    新版分析：使用 AI 语义分段 + 结构化提取，返回可加载的小说设定。

    替换旧版双 API 调用（只读前 6000 字 + 纯文本输出）。

    Args:
        client: OpenAI 客户端
        source_text: 源文档全文
        model: 模型名称

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
    segments = segment_by_ai(client, source_text, model)

    # 2. 逐段提取世界观
    world_data = extract_world_bible_from_segments(client, segments, model)

    # 3. 生成小说设定
    settings = generate_novel_settings_from_world_bible(client, world_data, model)

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

    return "\n\n".join(parts)


def suggest_directions(client, setting: str, plot: str, model: str,
                       world_data: dict | None = None) -> list[str]:
    """
    AI 建议 3-5 个发展方向

    Args:
        world_data: 世界书数据（含角色/地点/规则/剧情线等）

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

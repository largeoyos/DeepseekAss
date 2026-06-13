"""
世界书查看/编辑对话框
提供标签页结构展示 WorldBible 的各部分内容，用户可直接编辑保存
"""

import json
import os
import re

from PyQt6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QFormLayout,
    QTabWidget,
    QTextEdit,
    QPushButton,
    QMessageBox,
    QLabel,
    QInputDialog,
    QLineEdit,
    QComboBox,
    QDialogButtonBox,
)


class WorldBibleDialog(QDialog):
    """世界书查看/编辑对话框"""

    def __init__(self, parent, world_bible, save_callback=None):
        """
        Args:
            parent: 父窗口
            world_bible: WorldBible 对象 (from core.world_bible)
            save_callback: 可选，保存回调，参数为 WorldBible
        """
        super().__init__(parent)
        self._bible = world_bible
        self._save_callback = save_callback
        self._saved = False
        self.setWindowTitle("📖 世界书 - 已建立的设定与世界观")
        self.resize(800, 600)
        self.setModal(True)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        # 说明
        hint = QLabel(
            "以下是从已生成章节中自动提取的世界观设定。修改后点击保存生效。"
        )
        hint.setStyleSheet("color: #888; font-size: 12px; padding: 4px 0;")
        layout.addWidget(hint)

        # 标签页
        self._tabs = QTabWidget()
        self._tabs.setStyleSheet("""
            QTabWidget::pane { background-color: #2d2d2d; border: 1px solid #444; }
            QTabBar::tab { background-color: #3c3c3c; color: #e0e0e0; padding: 6px 16px; border: 1px solid #444; border-bottom: none; border-top-left-radius: 4px; border-top-right-radius: 4px; }
            QTabBar::tab:selected { background-color: #2d2d2d; color: #ffffff; }
            QTabBar::tab:hover:!selected { background-color: #4a4a4a; }
        """)

        self._char_edit = self._make_tab("角色", self._format_characters())
        self._loc_edit = self._make_tab("地点", self._format_locations())
        self._rule_edit = self._make_tab("规则", "\n".join(self._bible.rules))
        self._timeline_edit = self._make_tab("时间线", self._format_timeline())
        self._plot_edit = self._make_tab("剧情线", self._format_plot_threads())
        self._worldbuilding_edit = self._make_tab("设定与伏笔", self._format_worldbuilding())
        self._warning_edit = self._make_tab("冲突提醒", self._format_consistency_warnings())

        layout.addWidget(self._tabs)

        tool_row = QHBoxLayout()
        merge_btn = QPushButton("合并角色")
        merge_btn.clicked.connect(self._on_merge_characters)
        resolve_btn = QPushButton("标记已解决")
        resolve_btn.clicked.connect(self._on_mark_resolved)
        lock_btn = QPushButton("锁定核心设定")
        lock_btn.clicked.connect(self._on_lock_core_setting)
        hide_btn = QPushButton("隐藏低优先级")
        hide_btn.clicked.connect(self._on_hide_low_priority)
        source_btn = QPushButton("按来源章节查看")
        source_btn.clicked.connect(self._on_view_source_chapter)
        add_fs_btn = QPushButton("添加伏笔")
        add_fs_btn.clicked.connect(self._on_add_foreshadowing)
        tool_row.addWidget(merge_btn)
        tool_row.addWidget(resolve_btn)
        tool_row.addWidget(lock_btn)
        tool_row.addWidget(hide_btn)
        tool_row.addWidget(source_btn)
        tool_row.addWidget(add_fs_btn)
        layout.addLayout(tool_row)

        # 按钮
        btn_row = QHBoxLayout()
        save_btn = QPushButton("💾 保存")
        save_btn.clicked.connect(self._on_save)
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.close)
        btn_row.addStretch()
        btn_row.addWidget(save_btn)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

    def _make_tab(self, title: str, content: str) -> QTextEdit:
        edit = QTextEdit()
        edit.setPlainText(content)
        edit.setStyleSheet("""
            QTextEdit {
                background-color: #2d2d2d; color: #e0e0e0;
                border: 1px solid #444; font-size: 13px;
            }
        """)
        self._tabs.addTab(edit, title)
        return edit

    def _format_characters(self) -> str:
        lines = ["# 角色列表", "格式：角色名 | 重要性 | 性格/外貌/能力 | 状态 | 首登场章\n"]
        for c in self._bible.characters:
            rels = "; ".join(f"{r.type}({r.target})" for r in c.relationships)
            lines.append(f"【{c.name}】")
            if c.aliases:
                lines.append(f"  别名：{'、'.join(c.aliases)}")
            imp_map = {"major": "重要", "normal": "普通", "minor": "次要"}
            lines.append(f"  重要性：{imp_map.get(c.importance, c.importance)}")
            if getattr(c, "hidden", False):
                lines.append("  隐藏：是")
            lines.append(f"  描述：{c.traits}")
            lines.append(f"  状态：{c.status}")
            if rels:
                lines.append(f"  关系：{rels}")
            if c.motivation:
                lines.append(f"  动机：{c.motivation}")
            if c.arc:
                lines.append(f"  成长弧线：{c.arc}")
            if c.current_location:
                lines.append(f"  当前位置：{c.current_location}")
            if c.current_goal:
                lines.append(f"  当前目标：{c.current_goal}")
            if c.current_emotion:
                lines.append(f"  当前状态：{c.current_emotion}")
            if c.recent_action:
                lines.append(f"  最近行动：{c.recent_action}")
            if c.knowledge_state:
                lines.append(f"  已知信息：{c.knowledge_state}")
            if c.unresolved_conflicts:
                for conflict in c.unresolved_conflicts:
                    lines.append(f"  ⚠️ 未解冲突：{conflict}")
            if c.notes:
                lines.append(f"  备注：{c.notes}")
            if c.key_details:
                for kd in c.key_details:
                    lines.append(f"  📌 关键细节：{kd}")
            if c.key_dialogues:
                for kd in c.key_dialogues:
                    lines.append(f"  💬 关键台词：{kd}")
            lines.append(f"  首登场：第{c.first_appearance}章\n")
            if c.source_chapter:
                src = f"  来源：第{c.source_chapter}章"
                if c.source_version:
                    src += f" v{c.source_version}"
                if c.last_updated_chapter:
                    src += f"；最近更新：第{c.last_updated_chapter}章"
                    if c.last_updated_version:
                        src += f" v{c.last_updated_version}"
                lines.append(src)
            lines.append("")
        return "\n".join(lines) if self._bible.characters else "(尚未提取到角色信息)"

    def _format_locations(self) -> str:
        lines = ["# 地点列表\n"]
        for l in self._bible.locations:
            lines.append(f"【{l.name}】")
            if getattr(l, "hidden", False):
                lines.append("  隐藏：是")
            lines.append(f"  描述：{l.description}")
            lines.append(f"  重要度：{l.significance}")
            if l.atmosphere:
                lines.append(f"  氛围：{l.atmosphere}")
            if l.key_details:
                for kd in l.key_details:
                    lines.append(f"  📌 关键描写：{kd}")
            lines.append(f"  首登场：第{l.first_appearance}章\n")
            if l.source_chapter:
                src = f"  来源：第{l.source_chapter}章"
                if l.source_version:
                    src += f" v{l.source_version}"
                if l.last_updated_chapter:
                    src += f"；最近更新：第{l.last_updated_chapter}章"
                    if l.last_updated_version:
                        src += f" v{l.last_updated_version}"
                lines.append(src)
            lines.append("")
        return "\n".join(lines) if self._bible.locations else "(尚未提取到地点信息)"

    def _format_timeline(self) -> str:
        lines = ["# 时间线（按章节）\n"]
        for t in self._bible.timeline:
            lines.append(f"- 第{t.chapter}章：{t.event} ({t.significance})")
            if t.key_passages:
                for kp in t.key_passages[:2]:
                    lines.append(f"  📄 原文段落：{kp}")
            if t.foreshadowing_hints:
                for fh in t.foreshadowing_hints:
                    lines.append(f"  🔮 伏笔：{fh}")
        return "\n".join(lines) if self._bible.timeline else "(尚未提取到时间线)"

    def _format_plot_threads(self) -> str:
        lines = ["# 剧情线\n"]
        for p in self._bible.active_plot_threads:
            chars = "、".join(p.involved_characters) if p.involved_characters else "无"
            imp_map = {"major": "重要", "normal": "普通", "minor": "次要"}
            lines.append(f"【{p.name}】（{p.status}）")
            lines.append(f"  重要性：{imp_map.get(p.importance, p.importance)}")
            if getattr(p, "hidden", False):
                lines.append("  隐藏：是")
            lines.append(f"  描述：{p.description}")
            lines.append(f"  涉及角色：{chars}")
            if p.opened_chapter:
                lines.append(f"  开启章节：第{p.opened_chapter}章")
            if p.last_touched_chapter:
                lines.append(f"  最近触达：第{p.last_touched_chapter}章")
            if p.expected_payoff:
                lines.append(f"  预期回收：{p.expected_payoff}")
            if p.payoff_hint:
                lines.append(f"  回收提示：{p.payoff_hint}")
            if p.source_chapter:
                src = f"  来源：第{p.source_chapter}章"
                if p.source_version:
                    src += f" v{p.source_version}"
                if p.last_updated_version:
                    src += f"；最近更新版本：v{p.last_updated_version}"
                lines.append(src)
            if p.key_details:
                for kd in p.key_details:
                    lines.append(f"  📌 关键细节：{kd}")
            if p.foreshadowing_related:
                for fr in p.foreshadowing_related:
                    lines.append(f"  🔮 关联伏笔：{fr}")
            lines.append("")
        return "\n".join(lines) if self._bible.active_plot_threads else "(尚未提取到剧情线)"

    def _format_worldbuilding(self) -> str:
        lines = ["# 关键设定与伏笔\n"]
        if self._bible.key_worldbuilding_passages:
            lines.append("## 世界观设定段落\n")
            for item in self._bible.key_worldbuilding_passages:
                lines.append(f"【{item.get('topic', '')}】")
                meta = []
                if item.get("locked"):
                    meta.append("锁定：是")
                if item.get("hidden"):
                    meta.append("隐藏：是")
                if meta:
                    lines.append("  " + " | ".join(meta))
                lines.append(f"  {item.get('passage', '')}")
                src = f"  （第{item.get('chapter', '?')}章"
                if item.get("version"):
                    src += f" v{item.get('version')}"
                lines.append(src + "）\n")
        if self._bible.global_foreshadowing:
            lines.append("## 全局伏笔\n")
            for item in self._bible.global_foreshadowing:
                hint = item.get('hint', '')
                relates = item.get('relates_to', '')
                meta_parts = []
                status = item.get("status", "open")
                if status:
                    meta_parts.append(f"状态：{status}")
                if relates:
                    meta_parts.append(f"关联：{relates}")
                if item.get("introduced_chapter"):
                    meta_parts.append(f"埋设：第{item.get('introduced_chapter')}章")
                if item.get("last_touched_chapter"):
                    meta_parts.append(f"最近：第{item.get('last_touched_chapter')}章")
                if item.get("hidden"):
                    meta_parts.append("隐藏：是")
                if item.get("next_step"):
                    meta_parts.append(f"推进：{item.get('next_step')}")
                if item.get("reveal_rule"):
                    meta_parts.append(f"限制：{item.get('reveal_rule')}")
                suffix = " | " + " | ".join(meta_parts) if meta_parts else ""
                lines.append(f"  🔮 {hint}{suffix}")
        if self._bible.global_key_dialogues:
            lines.append("\n## 关键对话\n")
            for item in self._bible.global_key_dialogues:
                speaker = item.get('speaker', '')
                dialogue = item.get('dialogue', '')
                ctx = item.get('context', '')
                parts = f"  💬 {speaker}：{dialogue}"
                if ctx:
                    parts += f"（{ctx}）"
                lines.append(parts)
        result = "\n".join(lines)
        if result.strip() == "# 关键设定与伏笔\n":
            return "(尚未提取到关键设定与伏笔)"
        return result

    def _format_consistency_warnings(self) -> str:
        lines = ["# 世界书冲突提醒\n"]
        warnings = getattr(self._bible, "consistency_warnings", []) or []
        if not warnings:
            return "(暂无冲突提醒)"
        for item in warnings:
            severity = item.get("severity", "minor")
            issue_type = item.get("type", "冲突")
            message = item.get("message", "")
            related = "、".join(str(x) for x in item.get("related", []) if x)
            line = f"- [{severity}] {issue_type}：{message}"
            if related:
                line += f" | 相关：{related}"
            lines.append(line)
        return "\n".join(lines)

    def _parse_characters_from_text(self, text: str) -> list:
        """从编辑后的文本重新解析角色列表"""
        from core.world_bible import CharacterEntry, Relationship
        characters = []
        current = {}
        for line in text.split("\n"):
            line = line.strip()
            if line.startswith("【") and line.endswith("】"):
                if current and current.get("name"):
                    characters.append(CharacterEntry(**current))
                name = line.strip("【】").strip()
                current = {
                    "name": name,
                    "aliases": [],
                    "traits": "",
                    "relationships": [],
                    "status": "alive",
                    "importance": "normal",
                    "first_appearance": 0,
                    "notes": "",
                    "key_details": [],
                    "key_dialogues": [],
                    "motivation": "",
                    "arc": "",
                    "current_location": "",
                    "current_goal": "",
                    "current_emotion": "",
                    "recent_action": "",
                    "knowledge_state": "",
                    "unresolved_conflicts": [],
                    "hidden": False,
                }
            elif line.startswith("重要性：") and current:
                imp_raw = line[4:].strip()
                imp_map = {"重要": "major", "普通": "normal", "次要": "minor"}
                current["importance"] = imp_map.get(imp_raw, imp_raw)
            elif line.startswith("隐藏：") and current:
                current["hidden"] = line[3:].strip() in {"是", "true", "True", "1", "yes"}
            elif line.startswith("描述：") and current:
                current["traits"] = line[3:].strip()
            elif line.startswith("状态：") and current:
                current["status"] = line[3:].strip()
            elif line.startswith("别名：") and current:
                current["aliases"] = [a.strip() for a in line[3:].strip().split("、") if a.strip()]
            elif line.startswith("动机：") and current:
                current["motivation"] = line[3:].strip()
            elif line.startswith("成长弧线：") and current:
                current["arc"] = line[5:].strip()
            elif line.startswith("当前位置：") and current:
                current["current_location"] = line[5:].strip()
            elif line.startswith("当前目标：") and current:
                current["current_goal"] = line[5:].strip()
            elif line.startswith("当前状态：") and current:
                current["current_emotion"] = line[5:].strip()
            elif line.startswith("最近行动：") and current:
                current["recent_action"] = line[5:].strip()
            elif line.startswith("已知信息：") and current:
                current["knowledge_state"] = line[5:].strip()
            elif line.startswith("⚠️ 未解冲突：") and current:
                current.setdefault("unresolved_conflicts", []).append(line.split("：", 1)[1].strip())
            elif line.startswith("备注：") and current:
                current["notes"] = line[3:].strip()
            elif line.startswith("📌 关键细节：") and current:
                current.setdefault("key_details", []).append(line[7:].strip())
            elif line.startswith("💬 关键台词：") and current:
                current.setdefault("key_dialogues", []).append(line[7:].strip())
            elif line.startswith("首登场：") and current:
                try:
                    current["first_appearance"] = int(''.join(filter(str.isdigit, line[4:])))
                except ValueError:
                    pass
            elif line.startswith("来源：") and current:
                nums = [int(x) for x in re.findall(r"\d+", line)]
                if nums:
                    current["source_chapter"] = nums[0]
                if len(nums) >= 2:
                    current["source_version"] = nums[1]
                if len(nums) >= 3:
                    current["last_updated_chapter"] = nums[2]
                if len(nums) >= 4:
                    current["last_updated_version"] = nums[3]
        if current and current.get("name"):
            characters.append(CharacterEntry(**current))
        return characters

    def _parse_locations_from_text(self, text: str) -> list:
        """从编辑后的文本重新解析地点列表"""
        from core.world_bible import LocationEntry
        locations = []
        current = {}
        for line in text.split("\n"):
            line = line.strip()
            if line.startswith("【") and line.endswith("】"):
                if current and current.get("name"):
                    locations.append(LocationEntry(**current))
                current = {
                    "name": line.strip("【】").strip(),
                    "description": "",
                    "significance": "",
                    "first_appearance": 0,
                    "key_details": [],
                    "atmosphere": "",
                    "hidden": False,
                }
            elif line.startswith("隐藏：") and current:
                current["hidden"] = line[3:].strip() in {"是", "true", "True", "1", "yes"}
            elif line.startswith("描述：") and current:
                current["description"] = line[3:].strip()
            elif line.startswith("重要度：") and current:
                current["significance"] = line[4:].strip()
            elif line.startswith("氛围：") and current:
                current["atmosphere"] = line[3:].strip()
            elif line.startswith("📌 关键描写：") and current:
                current.setdefault("key_details", []).append(line[7:].strip())
            elif line.startswith("首登场：") and current:
                try:
                    current["first_appearance"] = int(''.join(filter(str.isdigit, line[4:])))
                except ValueError:
                    pass
            elif line.startswith("来源：") and current:
                nums = [int(x) for x in re.findall(r"\d+", line)]
                if nums:
                    current["source_chapter"] = nums[0]
                if len(nums) >= 2:
                    current["source_version"] = nums[1]
                if len(nums) >= 3:
                    current["last_updated_chapter"] = nums[2]
                if len(nums) >= 4:
                    current["last_updated_version"] = nums[3]
        if current and current.get("name"):
            locations.append(LocationEntry(**current))
        return locations

    def _parse_timeline_from_text(self, text: str) -> list:
        """从编辑后的文本重新解析时间线"""
        from core.world_bible import TimelineEntry
        timeline = []
        current = {}
        for line in text.split("\n"):
            line = line.strip()
            if line.startswith("- 第") and "章：" in line:
                if current and current.get("event"):
                    timeline.append(TimelineEntry(**current))
                rest = line[1:].strip()
                try:
                    ch_part = rest.split("章")[0].replace("第", "").strip()
                    chapter = int(ch_part)
                except ValueError:
                    chapter = 0
                after_ch = "章：".join(rest.split("章：")[1:]) if "章：" in rest else ""
                if "(" in after_ch and after_ch.endswith(")"):
                    event = after_ch[:after_ch.rindex("(")].strip()
                    significance = after_ch[after_ch.rindex("(")+1:-1].strip()
                else:
                    event = after_ch.strip()
                    significance = ""
                current = {"chapter": chapter, "event": event, "significance": significance, "key_passages": [], "foreshadowing_hints": []}
            elif line.startswith("📄 原文段落：") and current:
                current.setdefault("key_passages", []).append(line[7:].strip())
            elif line.startswith("🔮 伏笔：") and current:
                current.setdefault("foreshadowing_hints", []).append(line[5:].strip())
        if current and current.get("event"):
            timeline.append(TimelineEntry(**current))
        return timeline

    def _parse_plot_threads_from_text(self, text: str) -> list:
        """从编辑后的文本重新解析剧情线"""
        from core.world_bible import PlotThread
        threads = []
        current = {}
        for line in text.split("\n"):
            line = line.strip()
            if line.startswith("【") and "）" in line and "】" in line:
                if current and current.get("name"):
                    threads.append(PlotThread(**current))
                name_end = line.index("】")
                name = line[1:name_end].strip()
                status = line[name_end+1:].strip("（）() ").strip()
                current = {
                    "name": name,
                    "status": status or "active",
                    "importance": "normal",
                    "description": "",
                    "involved_characters": [],
                    "key_details": [],
                    "foreshadowing_related": [],
                    "opened_chapter": 0,
                    "last_touched_chapter": 0,
                    "expected_payoff": "",
                    "payoff_hint": "",
                    "hidden": False,
                }
            elif line.startswith("重要性：") and current:
                imp_raw = line[4:].strip()
                imp_map = {"重要": "major", "普通": "normal", "次要": "minor"}
                current["importance"] = imp_map.get(imp_raw, imp_raw)
            elif line.startswith("隐藏：") and current:
                current["hidden"] = line[3:].strip() in {"是", "true", "True", "1", "yes"}
            elif line.startswith("描述：") and current:
                current["description"] = line[3:].strip()
            elif line.startswith("涉及角色：") and current:
                raw = line[5:].strip()
                current["involved_characters"] = [c.strip() for c in raw.split("、") if c.strip() and c.strip() != "无"]
            elif line.startswith("开启章节：") and current:
                try:
                    current["opened_chapter"] = int(''.join(filter(str.isdigit, line[5:])))
                except ValueError:
                    current["opened_chapter"] = 0
            elif line.startswith("最近触达：") and current:
                try:
                    current["last_touched_chapter"] = int(''.join(filter(str.isdigit, line[5:])))
                except ValueError:
                    current["last_touched_chapter"] = 0
            elif line.startswith("预期回收：") and current:
                current["expected_payoff"] = line[5:].strip()
            elif line.startswith("回收提示：") and current:
                current["payoff_hint"] = line[5:].strip()
            elif line.startswith("来源：") and current:
                nums = [int(x) for x in re.findall(r"\d+", line)]
                if nums:
                    current["source_chapter"] = nums[0]
                if len(nums) >= 2:
                    current["source_version"] = nums[1]
                if len(nums) >= 3:
                    current["last_updated_version"] = nums[2]
            elif line.startswith("📌 关键细节：") and current:
                current.setdefault("key_details", []).append(line[7:].strip())
            elif line.startswith("🔮 关联伏笔：") and current:
                current.setdefault("foreshadowing_related", []).append(line[7:].strip())
        if current and current.get("name"):
            threads.append(PlotThread(**current))
        return threads

    def _parse_worldbuilding_from_text(self, text: str) -> tuple:
        """从编辑后的文本重新解析设定与伏笔"""
        from core.world_bible import WorldBible
        passages = []
        foreshadowing = []
        dialogues = []
        section = None
        current_topic = None
        current_passage = ""
        current_meta = {"locked": False, "hidden": False}
        for line in text.split("\n"):
            line = line.strip()
            if line.startswith("## 世界观设定段落"):
                section = "passages"
            elif line.startswith("## 全局伏笔"):
                section = "foreshadowing"
            elif line.startswith("## 关键对话"):
                section = "dialogues"
            elif line.startswith("【") and line.endswith("】") and section == "passages":
                current_topic = line.strip("【】").strip()
                current_passage = ""
                current_meta = {"locked": False, "hidden": False}
            elif section == "passages" and current_topic and line.startswith(("锁定：", "隐藏：")):
                for meta in [p.strip() for p in line.split("|") if p.strip()]:
                    if meta.startswith("锁定："):
                        current_meta["locked"] = meta[3:].strip() in {"是", "true", "True", "1", "yes"}
                    elif meta.startswith("隐藏："):
                        current_meta["hidden"] = meta[3:].strip() in {"是", "true", "True", "1", "yes"}
            elif section == "passages" and current_topic and line and not line.startswith("（第") and not line.startswith("#"):
                current_passage = line
            elif section == "passages" and current_topic and line.startswith("（第"):
                nums = [int(x) for x in re.findall(r"\d+", line)]
                ch = nums[0] if nums else 0
                version = nums[1] if len(nums) >= 2 else 0
                item = {
                    "topic": current_topic,
                    "passage": current_passage,
                    "chapter": ch,
                    "locked": current_meta.get("locked", False),
                    "hidden": current_meta.get("hidden", False),
                }
                if version:
                    item["version"] = version
                passages.append(item)
                current_topic = None
            elif section == "foreshadowing" and line.startswith("🔮"):
                hint_text = line[2:].strip()
                if "→ 关联：" in hint_text:
                    parts = hint_text.split("→ 关联：", 1)
                    foreshadowing.append({
                        "hint": parts[0].strip(),
                        "relates_to": parts[1].strip(),
                        "status": "open",
                        "next_step": "",
                        "reveal_rule": "",
                        "hidden": False,
                    })
                else:
                    pieces = [p.strip() for p in hint_text.split(" | ") if p.strip()]
                    item = {
                        "hint": pieces[0] if pieces else "",
                        "relates_to": "",
                        "status": "open",
                        "introduced_chapter": 0,
                        "last_touched_chapter": 0,
                        "next_step": "",
                        "reveal_rule": "",
                        "hidden": False,
                    }
                    for meta in pieces[1:]:
                        if meta.startswith("状态："):
                            item["status"] = meta[3:].strip() or "open"
                        elif meta.startswith("关联："):
                            item["relates_to"] = meta[3:].strip()
                        elif meta.startswith("埋设："):
                            try:
                                item["introduced_chapter"] = int(''.join(filter(str.isdigit, meta)))
                            except ValueError:
                                item["introduced_chapter"] = 0
                        elif meta.startswith("最近："):
                            try:
                                item["last_touched_chapter"] = int(''.join(filter(str.isdigit, meta)))
                            except ValueError:
                                item["last_touched_chapter"] = 0
                        elif meta.startswith("隐藏："):
                            item["hidden"] = meta[3:].strip() in {"是", "true", "True", "1", "yes"}
                        elif meta.startswith("推进："):
                            item["next_step"] = meta[3:].strip()
                        elif meta.startswith("限制："):
                            item["reveal_rule"] = meta[3:].strip()
                    if item["hint"]:
                        foreshadowing.append(item)
            elif section == "dialogues" and line.startswith("💬"):
                dia_text = line[2:].strip()
                if "：" in dia_text:
                    speaker, rest = dia_text.split("：", 1)
                    if "（" in rest and rest.endswith("）"):
                        dialogue = rest[:rest.rindex("（")].strip()
                        context = rest[rest.rindex("（")+1:-1].strip()
                    else:
                        dialogue = rest.strip()
                        context = ""
                    dialogues.append({"speaker": speaker.strip(), "dialogue": dialogue, "context": context})
        return passages, foreshadowing, dialogues

    def _split_names(self, text: str) -> list[str]:
        return [p.strip() for p in re.split(r"[、,，;；\s]+", text or "") if p.strip()]

    def _find_character(self, name: str):
        key = name.strip().lower()
        if not key:
            return None
        for character in self._bible.characters:
            names = [character.name, *getattr(character, "aliases", [])]
            if any((item or "").strip().lower() == key for item in names):
                return character
        return None

    def _append_unique_text(self, current: str, incoming: str, limit: int = 1200) -> str:
        current = (current or "").strip()
        incoming = (incoming or "").strip()
        if not incoming:
            return current
        if not current:
            return incoming[:limit]
        if incoming in current:
            return current[:limit]
        return f"{current}\n{incoming}"[:limit]

    def _extend_unique(self, target: list, values: list) -> None:
        seen = {str(item).strip() for item in target if str(item).strip()}
        for value in values:
            text = str(value).strip()
            if text and text not in seen:
                target.append(value)
                seen.add(text)

    def _sync_from_editors(self) -> None:
        char_text = self._char_edit.toPlainText().strip()
        if char_text and "尚未提取到" not in char_text:
            self._bible.characters = self._parse_characters_from_text(char_text)

        loc_text = self._loc_edit.toPlainText().strip()
        if loc_text and "尚未提取到" not in loc_text:
            self._bible.locations = self._parse_locations_from_text(loc_text)

        rule_text = self._rule_edit.toPlainText().strip()
        if rule_text and "尚未提取到" not in rule_text:
            self._bible.rules = [r.strip() for r in rule_text.split("\n") if r.strip() and not r.startswith("#")]

        timeline_text = self._timeline_edit.toPlainText().strip()
        if timeline_text and "尚未提取到" not in timeline_text:
            self._bible.timeline = self._parse_timeline_from_text(timeline_text)

        plot_text = self._plot_edit.toPlainText().strip()
        if plot_text and "尚未提取到" not in plot_text:
            self._bible.active_plot_threads = self._parse_plot_threads_from_text(plot_text)

        wb_text = self._worldbuilding_edit.toPlainText().strip()
        if wb_text and "尚未提取到" not in wb_text:
            passages, foreshadowing, dialogues = self._parse_worldbuilding_from_text(wb_text)
            self._bible.key_worldbuilding_passages = passages
            self._bible.global_foreshadowing = foreshadowing
            self._bible.global_key_dialogues = dialogues

    def _audit_and_refresh(self) -> None:
        try:
            from core.world_bible import audit_world_bible_consistency
            self._bible.consistency_warnings = audit_world_bible_consistency(self._bible)
        except Exception:
            pass
        self._char_edit.setPlainText(self._format_characters())
        self._loc_edit.setPlainText(self._format_locations())
        self._rule_edit.setPlainText("\n".join(self._bible.rules) if self._bible.rules else "(尚未提取到世界规则)")
        self._timeline_edit.setPlainText(self._format_timeline())
        self._plot_edit.setPlainText(self._format_plot_threads())
        self._worldbuilding_edit.setPlainText(self._format_worldbuilding())
        self._warning_edit.setPlainText(self._format_consistency_warnings())

    def _on_merge_characters(self) -> None:
        try:
            self._sync_from_editors()
        except Exception as exc:
            QMessageBox.warning(self, "解析失败", f"请先修正当前编辑内容：{exc}")
            return

        target_name, ok = QInputDialog.getText(self, "合并角色", "保留哪个角色作为主条目？")
        if not ok or not target_name.strip():
            return
        base = self._find_character(target_name)
        if not base:
            QMessageBox.warning(self, "未找到角色", f"找不到主条目：{target_name}")
            return

        merge_text, ok = QInputDialog.getText(self, "合并角色", "要合并进来的角色名/别名（可用顿号或逗号分隔）：")
        if not ok:
            return
        merge_names = [name for name in self._split_names(merge_text) if name != base.name]
        targets = []
        for name in merge_names:
            character = self._find_character(name)
            if character and character is not base and character not in targets:
                targets.append(character)
        if not targets:
            QMessageBox.warning(self, "未找到角色", "没有找到可合并的异名角色。")
            return

        from core.world_bible import Relationship

        imp_rank = {"minor": 0, "normal": 1, "major": 2}
        removed_names = []
        for other in targets:
            removed_names.append(other.name)
            self._extend_unique(base.aliases, [other.name, *getattr(other, "aliases", [])])
            base.traits = self._append_unique_text(base.traits, other.traits)
            base.notes = self._append_unique_text(base.notes, other.notes)
            base.motivation = base.motivation or other.motivation
            base.arc = self._append_unique_text(base.arc, other.arc, 800)
            base.current_location = base.current_location or other.current_location
            base.current_goal = base.current_goal or other.current_goal
            base.current_emotion = base.current_emotion or other.current_emotion
            base.recent_action = base.recent_action or other.recent_action
            base.knowledge_state = base.knowledge_state or other.knowledge_state
            self._extend_unique(base.key_details, getattr(other, "key_details", []))
            self._extend_unique(base.key_dialogues, getattr(other, "key_dialogues", []))
            self._extend_unique(base.unresolved_conflicts, getattr(other, "unresolved_conflicts", []))
            if imp_rank.get(other.importance, 1) > imp_rank.get(base.importance, 1):
                base.importance = other.importance
            if base.status == "alive" and other.status != "alive":
                base.status = other.status
            if other.first_appearance and (not base.first_appearance or other.first_appearance < base.first_appearance):
                base.first_appearance = other.first_appearance
            if other.source_chapter and (not base.source_chapter or other.source_chapter < base.source_chapter):
                base.source_chapter = other.source_chapter
                base.source_version = other.source_version
            if other.last_updated_chapter > base.last_updated_chapter:
                base.last_updated_chapter = other.last_updated_chapter
                base.last_updated_version = other.last_updated_version
            for rel in getattr(other, "relationships", []):
                target = base.name if rel.target in removed_names else rel.target
                if target != base.name and not any(r.target == target and r.type == rel.type for r in base.relationships):
                    base.relationships.append(Relationship(target=target, type=rel.type, description=rel.description))

        for character in self._bible.characters:
            for rel in getattr(character, "relationships", []):
                if rel.target in removed_names:
                    rel.target = base.name
        self._bible.characters = [c for c in self._bible.characters if c not in targets]
        self._audit_and_refresh()
        QMessageBox.information(self, "已合并", f"已将 {len(targets)} 个角色合并到「{base.name}」。")

    def _on_mark_resolved(self) -> None:
        try:
            self._sync_from_editors()
        except Exception as exc:
            QMessageBox.warning(self, "解析失败", f"请先修正当前编辑内容：{exc}")
            return
        query, ok = QInputDialog.getText(self, "标记已解决", "输入剧情线名或伏笔关键词：")
        if not ok or not query.strip():
            return
        key = query.strip().lower()
        changed = 0
        for thread in self._bible.active_plot_threads:
            if key in thread.name.lower() or key in thread.description.lower():
                thread.status = "resolved"
                changed += 1
        for item in self._bible.global_foreshadowing:
            haystack = " ".join(str(item.get(k, "")) for k in ("hint", "relates_to", "next_step")).lower()
            if key in haystack:
                item["status"] = "resolved"
                changed += 1
        if not changed:
            QMessageBox.warning(self, "未找到条目", "没有匹配到剧情线或伏笔。")
            return
        self._audit_and_refresh()
        QMessageBox.information(self, "已更新", f"已标记 {changed} 个条目为 resolved。")

    def _on_lock_core_setting(self) -> None:
        try:
            self._sync_from_editors()
        except Exception as exc:
            QMessageBox.warning(self, "解析失败", f"请先修正当前编辑内容：{exc}")
            return
        topic, ok = QInputDialog.getText(self, "锁定核心设定", "输入要锁定的设定主题：")
        if not ok or not topic.strip():
            return
        topic = topic.strip()
        for item in self._bible.key_worldbuilding_passages:
            if item.get("topic", "").strip() == topic:
                item["locked"] = True
                item["hidden"] = False
                self._audit_and_refresh()
                QMessageBox.information(self, "已锁定", f"已锁定核心设定：{topic}")
                return
        passage, ok = QInputDialog.getMultiLineText(self, "新增核心设定", "未找到该主题，请输入设定内容：")
        if not ok or not passage.strip():
            return
        self._bible.key_worldbuilding_passages.append({
            "topic": topic,
            "passage": passage.strip(),
            "chapter": getattr(self._bible, "last_updated_chapter", 0),
            "locked": True,
            "hidden": False,
        })
        self._audit_and_refresh()
        QMessageBox.information(self, "已新增", f"已新增并锁定核心设定：{topic}")

    def _on_hide_low_priority(self) -> None:
        try:
            self._sync_from_editors()
        except Exception as exc:
            QMessageBox.warning(self, "解析失败", f"请先修正当前编辑内容：{exc}")
            return
        changed = 0
        for character in self._bible.characters:
            if character.importance == "minor" and not character.hidden:
                character.hidden = True
                changed += 1
        for thread in self._bible.active_plot_threads:
            if thread.importance == "minor" and thread.status != "active" and not thread.hidden:
                thread.hidden = True
                changed += 1
        for location in self._bible.locations:
            low_signal = not location.key_details and not location.atmosphere and location.significance in {"", "minor", "次要"}
            if low_signal and not location.hidden:
                location.hidden = True
                changed += 1
        self._audit_and_refresh()
        QMessageBox.information(self, "已隐藏", f"已隐藏 {changed} 个低优先级条目。")

    def _on_view_source_chapter(self) -> None:
        try:
            self._sync_from_editors()
        except Exception as exc:
            QMessageBox.warning(self, "解析失败", f"请先修正当前编辑内容：{exc}")
            return
        chapter, ok = QInputDialog.getInt(self, "按来源章节查看", "章节号：", value=max(1, self._bible.last_updated_chapter or 1), min=0)
        if not ok:
            return
        lines = [f"# 第{chapter}章来源条目\n"]
        chars = [
            c.name for c in self._bible.characters
            if c.source_chapter == chapter or c.last_updated_chapter == chapter or c.first_appearance == chapter
        ]
        locs = [
            l.name for l in self._bible.locations
            if l.source_chapter == chapter or l.last_updated_chapter == chapter or l.first_appearance == chapter
        ]
        events = [t.event for t in self._bible.timeline if t.chapter == chapter]
        threads = [
            p.name for p in self._bible.active_plot_threads
            if p.source_chapter == chapter or p.opened_chapter == chapter or p.last_touched_chapter == chapter
        ]
        passages = [p.get("topic", "") for p in self._bible.key_worldbuilding_passages if p.get("chapter") == chapter]
        foreshadowing = [
            f.get("hint", "") for f in self._bible.global_foreshadowing
            if f.get("introduced_chapter") == chapter or f.get("last_touched_chapter") == chapter
        ]
        groups = [
            ("角色", chars),
            ("地点", locs),
            ("事件", events),
            ("剧情线", threads),
            ("设定", passages),
            ("伏笔", foreshadowing),
        ]
        for title, values in groups:
            lines.append(f"## {title}")
            if values:
                lines.extend(f"- {value}" for value in values if value)
            else:
                lines.append("- 无")
            lines.append("")

        dialog = QDialog(self)
        dialog.setWindowTitle(f"第{chapter}章来源条目")
        dialog.resize(620, 520)
        layout = QVBoxLayout(dialog)
        edit = QTextEdit(dialog)
        edit.setReadOnly(True)
        edit.setPlainText("\n".join(lines))
        layout.addWidget(edit)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok, dialog)
        buttons.accepted.connect(dialog.accept)
        layout.addWidget(buttons)
        dialog.exec()

    def _on_add_foreshadowing(self) -> None:
        try:
            self._sync_from_editors()
        except Exception as exc:
            QMessageBox.warning(self, "解析失败", f"请先修正当前编辑内容：{exc}")
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("添加伏笔")
        layout = QVBoxLayout(dialog)
        form = QFormLayout()
        hint_edit = QLineEdit(dialog)
        relates_edit = QLineEdit(dialog)
        next_step_edit = QLineEdit(dialog)
        reveal_rule_edit = QLineEdit(dialog)
        status_combo = QComboBox(dialog)
        status_combo.addItems(["open", "noticed", "advanced", "dormant", "resolved"])
        form.addRow("伏笔内容", hint_edit)
        form.addRow("关联对象", relates_edit)
        form.addRow("状态", status_combo)
        form.addRow("下一步推进", next_step_edit)
        form.addRow("回收限制", reveal_rule_edit)
        layout.addLayout(form)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            dialog,
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        hint = hint_edit.text().strip()
        if not hint:
            QMessageBox.warning(self, "内容为空", "伏笔内容不能为空。")
            return
        chapter = getattr(self._bible, "last_updated_chapter", 0)
        self._bible.global_foreshadowing.append({
            "hint": hint,
            "relates_to": relates_edit.text().strip(),
            "status": status_combo.currentText(),
            "introduced_chapter": chapter,
            "last_touched_chapter": chapter,
            "next_step": next_step_edit.text().strip(),
            "reveal_rule": reveal_rule_edit.text().strip(),
            "hidden": False,
        })
        self._audit_and_refresh()
        QMessageBox.information(self, "已添加", "伏笔已添加到世界书。")

    def get_bible(self):
        """返回修改后的 WorldBible 对象（在 exec 返回 Accepted 后调用）"""
        return self._bible

    def _on_save(self):
        """保存所有标签页的修改回 WorldBible"""
        try:
            # 角色
            char_text = self._char_edit.toPlainText().strip()
            if char_text and "尚未提取到" not in char_text:
                self._bible.characters = self._parse_characters_from_text(char_text)

            # 地点
            loc_text = self._loc_edit.toPlainText().strip()
            if loc_text and "尚未提取到" not in loc_text:
                self._bible.locations = self._parse_locations_from_text(loc_text)

            # 规则
            rule_text = self._rule_edit.toPlainText().strip()
            if rule_text and "尚未提取到" not in rule_text:
                self._bible.rules = [r.strip() for r in rule_text.split("\n") if r.strip() and not r.startswith("#")]

            # 时间线
            timeline_text = self._timeline_edit.toPlainText().strip()
            if timeline_text and "尚未提取到" not in timeline_text:
                self._bible.timeline = self._parse_timeline_from_text(timeline_text)

            # 剧情线
            plot_text = self._plot_edit.toPlainText().strip()
            if plot_text and "尚未提取到" not in plot_text:
                self._bible.active_plot_threads = self._parse_plot_threads_from_text(plot_text)

            # 设定与伏笔
            wb_text = self._worldbuilding_edit.toPlainText().strip()
            if wb_text and "尚未提取到" not in wb_text:
                passages, foreshadowing, dialogues = self._parse_worldbuilding_from_text(wb_text)
                self._bible.key_worldbuilding_passages = passages
                self._bible.global_foreshadowing = foreshadowing
                self._bible.global_key_dialogues = dialogues

            try:
                from core.world_bible import audit_world_bible_consistency
                self._bible.consistency_warnings = audit_world_bible_consistency(self._bible)
                self._warning_edit.setPlainText(self._format_consistency_warnings())
            except Exception:
                pass

            if self._save_callback:
                self._save_callback(self._bible)
                QMessageBox.information(self, "成功", "世界书已保存。")
            else:
                self._saved = True
                self.accept()
        except Exception as e:
            QMessageBox.critical(self, "错误", f"保存失败: {e}")

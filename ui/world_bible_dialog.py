"""
世界书查看/编辑对话框
提供标签页结构展示 WorldBible 的各部分内容，用户可直接编辑保存
"""

import json
import os

from PyQt6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QTabWidget,
    QTextEdit,
    QPushButton,
    QMessageBox,
    QLabel,
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

        layout.addWidget(self._tabs)

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
            lines.append(f"  描述：{c.traits}")
            lines.append(f"  状态：{c.status}")
            if rels:
                lines.append(f"  关系：{rels}")
            if c.motivation:
                lines.append(f"  动机：{c.motivation}")
            if c.arc:
                lines.append(f"  成长弧线：{c.arc}")
            if c.notes:
                lines.append(f"  备注：{c.notes}")
            if c.key_details:
                for kd in c.key_details:
                    lines.append(f"  📌 关键细节：{kd}")
            if c.key_dialogues:
                for kd in c.key_dialogues:
                    lines.append(f"  💬 关键台词：{kd}")
            lines.append(f"  首登场：第{c.first_appearance}章\n")
        return "\n".join(lines) if self._bible.characters else "(尚未提取到角色信息)"

    def _format_locations(self) -> str:
        lines = ["# 地点列表\n"]
        for l in self._bible.locations:
            lines.append(f"【{l.name}】")
            lines.append(f"  描述：{l.description}")
            lines.append(f"  重要度：{l.significance}")
            if l.atmosphere:
                lines.append(f"  氛围：{l.atmosphere}")
            if l.key_details:
                for kd in l.key_details:
                    lines.append(f"  📌 关键描写：{kd}")
            lines.append(f"  首登场：第{l.first_appearance}章\n")
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
            lines.append(f"  描述：{p.description}")
            lines.append(f"  涉及角色：{chars}")
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
                lines.append(f"  {item.get('passage', '')}")
                lines.append(f"  （第{item.get('chapter', '?')}章）\n")
        if self._bible.global_foreshadowing:
            lines.append("## 全局伏笔\n")
            for item in self._bible.global_foreshadowing:
                hint = item.get('hint', '')
                relates = item.get('relates_to', '')
                if relates:
                    lines.append(f"  🔮 {hint} → 关联：{relates}")
                else:
                    lines.append(f"  🔮 {hint}")
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
                current = {"name": name, "aliases": [], "traits": "", "relationships": [], "status": "alive", "importance": "normal", "first_appearance": 0, "notes": "", "key_details": [], "key_dialogues": [], "motivation": "", "arc": ""}
            elif line.startswith("重要性：") and current:
                imp_raw = line[4:].strip()
                imp_map = {"重要": "major", "普通": "normal", "次要": "minor"}
                current["importance"] = imp_map.get(imp_raw, imp_raw)
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
                current = {"name": line.strip("【】").strip(), "description": "", "significance": "", "first_appearance": 0, "key_details": [], "atmosphere": ""}
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
                current = {"name": name, "status": status or "active", "importance": "normal", "description": "", "involved_characters": [], "key_details": [], "foreshadowing_related": []}
            elif line.startswith("重要性：") and current:
                imp_raw = line[4:].strip()
                imp_map = {"重要": "major", "普通": "normal", "次要": "minor"}
                current["importance"] = imp_map.get(imp_raw, imp_raw)
            elif line.startswith("描述：") and current:
                current["description"] = line[3:].strip()
            elif line.startswith("涉及角色：") and current:
                raw = line[5:].strip()
                current["involved_characters"] = [c.strip() for c in raw.split("、") if c.strip() and c.strip() != "无"]
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
            elif section == "passages" and current_topic and line and not line.startswith("（第") and not line.startswith("#"):
                current_passage = line
            elif section == "passages" and current_topic and line.startswith("（第") and line.endswith("章）"):
                try:
                    ch = int(''.join(filter(str.isdigit, line[2:])))
                except ValueError:
                    ch = 0
                passages.append({"topic": current_topic, "passage": current_passage, "chapter": ch})
                current_topic = None
            elif section == "foreshadowing" and line.startswith("🔮"):
                hint_text = line[2:].strip()
                if "→ 关联：" in hint_text:
                    parts = hint_text.split("→ 关联：", 1)
                    foreshadowing.append({"hint": parts[0].strip(), "relates_to": parts[1].strip()})
                else:
                    foreshadowing.append({"hint": hint_text, "relates_to": ""})
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

            if self._save_callback:
                self._save_callback(self._bible)
                QMessageBox.information(self, "成功", "世界书已保存。")
            else:
                self._saved = True
                self.accept()
        except Exception as e:
            QMessageBox.critical(self, "错误", f"保存失败: {e}")

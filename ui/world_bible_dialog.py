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
        self.resize(700, 500)
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

        self._char_edit = self._make_tab("角色", self._format_characters())
        self._loc_edit = self._make_tab("地点", self._format_locations())
        self._rule_edit = self._make_tab("规则", "\n".join(self._bible.rules))
        self._timeline_edit = self._make_tab("时间线", self._format_timeline())
        self._plot_edit = self._make_tab("剧情线", self._format_plot_threads())

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
        lines = ["# 角色列表", "格式：角色名 | 性格/外貌/能力 | 状态 | 首登场章\n"]
        for c in self._bible.characters:
            rels = "; ".join(f"{r.type}({r.target})" for r in c.relationships)
            lines.append(f"【{c.name}】")
            if c.aliases:
                lines.append(f"  别名：{'、'.join(c.aliases)}")
            lines.append(f"  描述：{c.traits}")
            lines.append(f"  状态：{c.status}")
            if rels:
                lines.append(f"  关系：{rels}")
            if c.notes:
                lines.append(f"  备注：{c.notes}")
            lines.append(f"  首登场：第{c.first_appearance}章\n")
        return "\n".join(lines) if self._bible.characters else "(尚未提取到角色信息)"

    def _format_locations(self) -> str:
        lines = ["# 地点列表\n"]
        for l in self._bible.locations:
            lines.append(f"【{l.name}】")
            lines.append(f"  描述：{l.description}")
            lines.append(f"  重要度：{l.significance}")
            lines.append(f"  首登场：第{l.first_appearance}章\n")
        return "\n".join(lines) if self._bible.locations else "(尚未提取到地点信息)"

    def _format_timeline(self) -> str:
        lines = ["# 时间线（按章节）\n"]
        for t in self._bible.timeline:
            lines.append(f"- 第{t.chapter}章：{t.event} ({t.significance})")
        return "\n".join(lines) if self._bible.timeline else "(尚未提取到时间线)"

    def _format_plot_threads(self) -> str:
        lines = ["# 剧情线\n"]
        for p in self._bible.active_plot_threads:
            chars = "、".join(p.involved_characters) if p.involved_characters else "无"
            lines.append(f"【{p.name}】（{p.status}）")
            lines.append(f"  描述：{p.description}")
            lines.append(f"  涉及角色：{chars}\n")
        return "\n".join(lines) if self._bible.active_plot_threads else "(尚未提取到剧情线)"

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
                current = {"name": name, "aliases": [], "traits": "", "relationships": [], "status": "alive", "first_appearance": 0, "notes": ""}
            elif line.startswith("描述：") and current:
                current["traits"] = line[3:].strip()
            elif line.startswith("状态：") and current:
                current["status"] = line[3:].strip()
            elif line.startswith("别名：") and current:
                current["aliases"] = [a.strip() for a in line[3:].strip().split("、") if a.strip()]
            elif line.startswith("备注：") and current:
                current["notes"] = line[3:].strip()
            elif line.startswith("首登场：") and current:
                try:
                    current["first_appearance"] = int(''.join(filter(str.isdigit, line[4:])))
                except ValueError:
                    pass
        if current and current.get("name"):
            characters.append(CharacterEntry(**current))
        return characters

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

            # 规则
            rule_text = self._rule_edit.toPlainText().strip()
            if rule_text and "尚未提取到" not in rule_text:
                self._bible.rules = [r.strip() for r in rule_text.split("\n") if r.strip() and not r.startswith("#")]

            if self._save_callback:
                self._save_callback(self._bible)
                QMessageBox.information(self, "成功", "世界书已保存。")
            else:
                self._saved = True
                self.accept()
        except Exception as e:
            QMessageBox.critical(self, "错误", f"保存失败: {e}")

"""
世界书查看/编辑对话框
提供标签页结构展示 WorldBible 的各部分内容，用户可直接编辑保存
"""

import copy
import json
import os
import re
from dataclasses import asdict

from PyQt6.QtCore import Qt
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
    QListWidget,
    QListWidgetItem,
    QSplitter,
    QWidget,
    QScrollArea,
    QFrame,
)


class WorldBibleDialog(QDialog):
    """世界书查看/编辑对话框"""

    def __init__(self, parent, world_bible, save_callback=None, active_chapters: set[int] | None = None):
        """
        Args:
            parent: 父窗口
            world_bible: WorldBible 对象 (from core.world_bible)
            save_callback: 可选，保存回调，参数为 WorldBible
        """
        super().__init__(parent)
        self._bible = world_bible
        from core.world_bible import _flat_view_dict
        self._original_view = copy.deepcopy(_flat_view_dict(world_bible))
        self._save_callback = save_callback
        self._active_chapters = active_chapters or set()
        self._saved = False
        self.setWindowTitle("📖 世界书 - 已建立的设定与世界观")
        self.resize(800, 600)
        self.setModal(True)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        # 说明
        load_state = getattr(self._bible, "diagnostics", {}).get("load_state", "loaded")
        state_text = {"missing": "尚无世界书文件", "error": "世界书加载失败（原文件已保护）", "loaded": "已加载"}.get(load_state, load_state)
        hint = QLabel(
            f"世界书 schema v{getattr(self._bible, 'schema_version', 1)} · {state_text}。人工修改会保存为可重放修订，不会在分支重建时丢失。"
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

        self._card_tabs: dict[str, QVBoxLayout] = {}
        for kind in ("角色", "时间状态", "地点", "规则", "时间线", "剧情线", "设定", "伏笔", "关键对话", "冲突提醒"):
            self._tabs.addTab(self._build_kind_cards_tab(kind), kind)

        self._advanced_edit = self._make_tab("高级 JSON", json.dumps(asdict(self._bible), ensure_ascii=False, indent=2))
        self._snapshot_view = self._make_tab("章节快照", "")
        self._snapshot_view.setReadOnly(True)
        self._override_view = self._make_tab("人工修订", "")
        self._override_view.setReadOnly(True)
        self._diagnostic_view = self._make_tab("运行诊断", "")
        self._diagnostic_view.setReadOnly(True)
        self._refresh_kind_cards()
        self._refresh_v2_tabs()

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
        preview_btn = QPushButton("注入预览")
        preview_btn.clicked.connect(self._on_preview_retrieval)
        facts_btn = QPushButton("事实历史")
        facts_btn.clicked.connect(self._on_view_fact_history)
        duplicate_btn = QPushButton("重复候选")
        duplicate_btn.clicked.connect(self._on_review_duplicate)
        undo_merge_btn = QPushButton("撤销合并")
        undo_merge_btn.clicked.connect(self._on_undo_merge)
        tool_row.addWidget(merge_btn)
        tool_row.addWidget(resolve_btn)
        tool_row.addWidget(lock_btn)
        tool_row.addWidget(hide_btn)
        tool_row.addWidget(source_btn)
        tool_row.addWidget(add_fs_btn)
        tool_row.addWidget(preview_btn)
        tool_row.addWidget(facts_btn)
        tool_row.addWidget(duplicate_btn)
        tool_row.addWidget(undo_merge_btn)
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

    def _build_kind_cards_tab(self, kind: str) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)
        scroll.setWidget(container)
        self._card_tabs[kind] = layout
        return scroll

    def _kind_card_entries(self, kind: str) -> list[dict]:
        entries = [entry for entry in self._card_entries() if entry["kind"] == kind]
        if kind == "时间状态":
            current = dict(self._bible.story_clock or {})
            entries = []
            if current:
                entries.append({
                    "kind": kind, "index": 0,
                    "title": current.get("current_date") or current.get("story_phase") or "当前故事时间",
                    "subtitle": "；".join(str(current.get(key, "")) for key in ("time_of_day", "elapsed_time", "story_phase") if current.get(key)),
                    "source": self._card_source_label(current),
                    "data": {**current, "history_state": "current"},
                    "hidden": False, "resolved": False, "editable": True,
                })
            for history_index, history in enumerate(reversed(self._bible.story_clock_history or []), 1):
                if not isinstance(history, dict):
                    continue
                chapter = int(history.get("source_chapter", 0) or 0)
                version = int(history.get("source_version", 0) or 0)
                entries.append({
                    "kind": kind, "index": -history_index,
                    "title": history.get("current_date") or history.get("story_phase") or f"历史状态 {history_index}",
                    "subtitle": "；".join(str(history.get(key, "")) for key in ("time_of_day", "elapsed_time", "story_phase") if history.get(key)),
                    "source": self._card_snapshot_label(chapter, version),
                    "data": {**history, "history_state": "history"},
                    "hidden": False, "resolved": False, "editable": False,
                })
        elif kind == "规则":
            entries = []
            for idx, rule in enumerate(self._bible.rules):
                entries.append({
                    "kind": kind,
                    "index": idx,
                    "title": f"规则 {idx + 1}",
                    "subtitle": rule,
                    "source": "全局规则",
                    "data": {"rule": rule},
                    "hidden": False,
                    "resolved": False,
                })
        elif kind == "时间线":
            entries = []
            for idx, item in enumerate(self._bible.timeline):
                data = asdict(item)
                entries.append({
                    "kind": kind,
                    "index": idx,
                    "title": f"第{item.chapter}章",
                    "subtitle": item.event,
                    "source": self._card_source_label(data),
                    "data": data,
                    "hidden": False,
                    "resolved": False,
                })
        elif kind == "关键对话":
            entries = []
            for idx, item in enumerate(self._bible.global_key_dialogues):
                entries.append({
                    "kind": kind,
                    "index": idx,
                    "title": item.get("speaker") or f"对话 {idx + 1}",
                    "subtitle": item.get("dialogue", "")[:80],
                    "source": self._card_source_label(item),
                    "data": item,
                    "hidden": bool(item.get("hidden")),
                    "resolved": False,
                })
        elif kind == "冲突提醒":
            entries = []
            for idx, item in enumerate(self._bible.consistency_warnings):
                severity_text = {"error": "阻断", "major": "严重", "minor": "一般", "info": "提示"}.get(
                    item.get("severity", ""), item.get("severity", "")
                )
                entries.append({
                    "kind": kind,
                    "index": idx,
                    "title": item.get("type") or item.get("severity") or f"提醒 {idx + 1}",
                    "subtitle": item.get("message", "")[:100],
                    "source": severity_text,
                    "data": item,
                    "hidden": False,
                    "resolved": False,
                })
        return entries

    def _refresh_kind_cards(self) -> None:
        for kind, layout in self._card_tabs.items():
            while layout.count():
                item = layout.takeAt(0)
                widget = item.widget()
                if widget:
                    widget.deleteLater()
            entries = self._kind_card_entries(kind)
            if not entries:
                empty_text = "未发现明显冲突/提醒" if kind == "冲突提醒" else "暂无条目"
                empty = QLabel(empty_text)
                empty.setStyleSheet("color: #888; padding: 12px;")
                layout.addWidget(empty)
                layout.addStretch()
                continue
            for entry in entries:
                layout.addWidget(self._make_entry_card(entry))
            layout.addStretch()

    def _make_entry_card(self, entry: dict) -> QFrame:
        card = QFrame()
        card.setFrameShape(QFrame.Shape.StyledPanel)
        card.setStyleSheet("""
            QFrame { background-color: #252526; border: 1px solid #444; border-radius: 6px; }
            QLabel { color: #dcdcdc; border: none; }
        """)
        layout = QVBoxLayout(card)
        layout.setSpacing(8)
        head = QHBoxLayout()
        title = QLabel(f"{entry['title']}")
        title.setStyleSheet("font-weight: bold; color: #ffffff;")
        kind_badge = self._make_badge(entry["kind"], "#3a4f68", "#cfe8ff")
        source = QLabel(entry.get("source", ""))
        source.setStyleSheet("color: #9cdcfe;")
        source.setWordWrap(True)
        status_badges = []
        if entry.get("hidden"):
            status_badges.append(self._make_badge("隐藏", "#4f3a3a", "#ffd6d6"))
        if entry.get("resolved"):
            status_badges.append(self._make_badge("已解决", "#34513a", "#d8f5dc"))
        editable = entry.get("editable", True)
        edit_btn = QPushButton("编辑") if editable else None
        delete_btn = QPushButton("×") if editable else None
        if edit_btn is not None:
            edit_btn.clicked.connect(lambda _=False, k=entry["kind"], i=entry["index"]: self._edit_card(k, i))
        if delete_btn is not None:
            delete_btn.setToolTip("删除条目")
            delete_btn.clicked.connect(lambda _=False, k=entry["kind"], i=entry["index"]: self._delete_card(k, i))
        head.addWidget(title, stretch=1)
        head.addWidget(kind_badge)
        for badge in status_badges:
            head.addWidget(badge)
        head.addWidget(source)
        if edit_btn is not None:
            head.addWidget(edit_btn)
        if delete_btn is not None:
            head.addWidget(delete_btn)
        layout.addLayout(head)
        self._render_entry_body(layout, entry)
        return card

    def _make_badge(self, text: str, bg: str = "#3c3c3c", fg: str = "#e0e0e0") -> QLabel:
        badge = QLabel(text)
        badge.setStyleSheet(
            f"background-color: {bg}; color: {fg}; border: 1px solid rgba(255,255,255,0.08); "
            "border-radius: 4px; padding: 2px 7px; font-size: 12px;"
        )
        return badge

    def _clean_display_text(self, value) -> str:
        text = str(value or "").strip()
        return re.sub(r"^【原文引用】\s*", "", text)

    def _is_quote_text(self, value) -> bool:
        return str(value or "").strip().startswith("【原文引用】")

    def _truthy_value(self, value) -> bool:
        if value is None:
            return False
        if isinstance(value, bool):
            return True
        if isinstance(value, (list, tuple, set, dict)):
            return bool(value)
        return bool(str(value).strip())

    def _add_section_label(self, layout: QVBoxLayout, text: str) -> None:
        label = QLabel(text)
        label.setStyleSheet("color: #c8c8c8; font-weight: bold; padding-top: 4px;")
        layout.addWidget(label)

    def _add_text_block(self, layout: QVBoxLayout, label: str, value, *, quote: bool = False) -> None:
        if not self._truthy_value(value):
            return
        if label:
            self._add_section_label(layout, label)
        text = self._clean_display_text(value)
        block = QLabel(text)
        block.setWordWrap(True)
        if quote or self._is_quote_text(value):
            block.setStyleSheet(
                "background-color: #1f2a30; color: #d7ecf7; border-left: 3px solid #4fa3d1; "
                "border-radius: 4px; padding: 7px 9px; line-height: 1.45;"
            )
        else:
            block.setStyleSheet(
                "background-color: #1f1f1f; color: #dddddd; border: 1px solid #383838; "
                "border-radius: 4px; padding: 7px 9px; line-height: 1.45;"
            )
        layout.addWidget(block)

    def _add_meta_row(self, layout: QVBoxLayout, data: dict, fields: list[tuple[str, str]]) -> None:
        row = QHBoxLayout()
        added = False
        for key, label in fields:
            value = data.get(key)
            if not self._truthy_value(value):
                continue
            row.addWidget(self._make_badge(f"{label}：{value}", "#303030", "#d0d0d0"))
            added = True
        if added:
            row.addStretch()
            layout.addLayout(row)

    def _add_list_block(self, layout: QVBoxLayout, label: str, values, *, quote: bool = False) -> None:
        values = [v for v in (values or []) if self._truthy_value(v)]
        if not values:
            return
        self._add_section_label(layout, label)
        for value in values:
            self._add_text_block(layout, "", value, quote=quote or self._is_quote_text(value))

    def _add_tags_block(self, layout: QVBoxLayout, label: str, values) -> None:
        values = [str(v).strip() for v in (values or []) if str(v or "").strip()]
        if not values:
            return
        self._add_section_label(layout, label)
        row = QHBoxLayout()
        for value in values[:12]:
            row.addWidget(self._make_badge(value, "#2f3b2f", "#dff0df"))
        row.addStretch()
        layout.addLayout(row)

    def _add_collapsible_details(self, layout: QVBoxLayout, render_callback) -> None:
        details = QWidget()
        details_layout = QVBoxLayout(details)
        details_layout.setContentsMargins(8, 2, 0, 0)
        details_layout.setSpacing(7)
        render_callback(details_layout)
        details.setVisible(False)
        toggle = QPushButton("展开更多详情 ▾")
        toggle.setCheckable(True)
        toggle.setStyleSheet(
            "QPushButton { text-align:left; color:#9cdcfe; background:transparent; border:none; padding:5px 2px; }"
            "QPushButton:hover { color:#ffffff; }"
        )

        def on_toggled(checked: bool) -> None:
            details.setVisible(checked)
            toggle.setText("收起详情 ▴" if checked else "展开更多详情 ▾")

        toggle.toggled.connect(on_toggled)
        layout.addWidget(toggle)
        layout.addWidget(details)
    def _field_label(self, key: str) -> str:
        labels = {
            "name": "名称", "aliases": "别名", "traits": "角色特征", "relationships": "关系",
            "status": "状态", "importance": "重要性", "first_appearance": "首次出现",
            "key_details": "关键细节", "key_dialogues": "关键台词", "motivation": "动机",
            "arc": "成长弧线", "current_location": "当前位置", "current_goal": "当前目标",
            "current_emotion": "当前情绪", "recent_action": "近期行动", "knowledge_state": "已知信息",
            "unresolved_conflicts": "未解决冲突", "description": "描述", "significance": "作用",
            "atmosphere": "氛围", "involved_characters": "相关角色", "foreshadowing_related": "关联伏笔",
            "opened_chapter": "开启章", "last_touched_chapter": "最近触达章",
            "expected_payoff": "预期回收", "payoff_hint": "回收提示",
        }
        return labels.get(key, key)

    def _add_fact_sources_block(self, layout: QVBoxLayout, data: dict) -> None:
        sources = data.get("fact_sources")
        if not isinstance(sources, dict) or not sources:
            return
        rows = []
        for field_name, records in sources.items():
            if not isinstance(records, list) or not records:
                continue
            chunks = []
            for record in records[-4:]:
                if not isinstance(record, dict):
                    continue
                chapter = record.get("source_chapter") or 0
                version = record.get("source_version") or 0
                value = self._clean_display_text(record.get("value", ""))
                if len(value) > 34:
                    value = value[:34] + "..."
                source = f"第{chapter}章" if chapter else "来源未知"
                if version:
                    source += f" v{version}"
                chunks.append(f"{source}：{value}" if value else source)
            if chunks:
                rows.append(f"{self._field_label(str(field_name))}｜" + "；".join(chunks))
        if rows:
            self._add_list_block(layout, "字段来源", rows)

    def _render_entry_body(self, layout: QVBoxLayout, entry: dict) -> None:
        data = entry.get("data", {}) or {}
        kind = entry.get("kind", "")
        if kind == "角色":
            self._render_character_card(layout, data)
        elif kind == "时间状态":
            self._render_story_clock_card(layout, data)
        elif kind == "地点":
            self._render_location_card(layout, data)
        elif kind == "剧情线":
            self._render_plot_thread_card(layout, data)
        elif kind == "设定":
            self._render_worldbuilding_card(layout, data)
        elif kind == "伏笔":
            self._render_foreshadowing_card(layout, data)
        elif kind == "时间线":
            self._render_timeline_card(layout, data)
        elif kind == "关键对话":
            self._render_dialogue_card(layout, data)
        elif kind == "规则":
            self._add_text_block(layout, "规则内容", data.get("rule"))
        elif kind == "冲突提醒":
            self._render_warning_card(layout, data)
        else:
            self._render_generic_card(layout, data)

    def _render_story_clock_card(self, layout: QVBoxLayout, data: dict) -> None:
        state = data.get("history_state", "current")
        layout.addWidget(self._make_badge(
            "当前状态" if state == "current" else "历史状态",
            "#34513a" if state == "current" else "#4a445c", "#ffffff",
        ))
        self._add_meta_row(layout, data, [
            ("current_date", "当前日期"), ("time_of_day", "当前时段"),
            ("elapsed_time", "累计流逝"), ("story_phase", "故事阶段"),
            ("calendar_system", "纪年体系"), ("source_chapter", "来源章"),
            ("source_version", "来源版本"),
        ])
        self._add_text_block(layout, "使用规则", "这是续写硬约束；只有正文明确发生时间跳跃、跨日或生日时才更新。")

    def _render_character_card(self, layout: QVBoxLayout, data: dict) -> None:
        self._add_meta_row(layout, data, [
            ("status", "状态"), ("importance", "重要性"),
            ("current_location", "当前位置"), ("current_goal", "当前目标"),
            ("current_emotion", "当前情绪"),
        ])
        self._add_text_block(layout, "角色特征", data.get("traits"))

        def render_details(details_layout: QVBoxLayout) -> None:
            self._add_meta_row(details_layout, data, [
                ("first_appearance", "首次出现"), ("source_chapter", "来源章"),
                ("source_version", "来源版本"), ("last_updated_chapter", "最近更新章"),
                ("last_updated_version", "最近版本"),
            ])
            self._add_text_block(details_layout, "动机", data.get("motivation"))
            self._add_text_block(details_layout, "成长弧线", data.get("arc"))
            self._add_meta_row(details_layout, data, [
                ("birth_date", "出生日期/纪年"), ("current_age", "当前年龄"),
                ("life_stage", "人生/身份阶段"), ("age_basis", "年龄依据"),
            ])
            self._add_text_block(details_layout, "近期行动", data.get("recent_action"))
            self._add_text_block(details_layout, "已知信息", data.get("knowledge_state"))
            self._add_list_block(details_layout, "关键细节", data.get("key_details"), quote=True)
            self._add_list_block(details_layout, "关键台词", data.get("key_dialogues"), quote=True)
            self._add_list_block(details_layout, "未解决冲突", data.get("unresolved_conflicts"))
            relationships = data.get("relationships") or []
            if relationships:
                self._add_section_label(details_layout, "关系")
                for rel in relationships:
                    if not isinstance(rel, dict):
                        continue
                    text = " / ".join(part for part in [rel.get("target", ""), rel.get("type", ""), rel.get("description", "")] if part)
                    self._add_text_block(details_layout, "", text)
            self._add_fact_sources_block(details_layout, data)

        self._add_collapsible_details(layout, render_details)
    def _render_location_card(self, layout: QVBoxLayout, data: dict) -> None:
        self._add_meta_row(layout, data, [
            ("first_appearance", "首次出现"), ("last_updated_chapter", "最近更新章"),
        ])
        self._add_text_block(layout, "描述", data.get("description"))
        self._add_text_block(layout, "作用", data.get("significance"))

        def render_details(details_layout: QVBoxLayout) -> None:
            self._add_meta_row(details_layout, data, [
                ("source_chapter", "来源章"), ("source_version", "来源版本"),
                ("last_updated_version", "最近版本"),
            ])
            self._add_text_block(details_layout, "氛围", data.get("atmosphere"), quote=self._is_quote_text(data.get("atmosphere")))
            self._add_list_block(details_layout, "完整原文细节", data.get("key_details"), quote=True)
            self._add_fact_sources_block(details_layout, data)

        self._add_collapsible_details(layout, render_details)
    def _render_plot_thread_card(self, layout: QVBoxLayout, data: dict) -> None:
        self._add_meta_row(layout, data, [
            ("status", "状态"), ("importance", "重要性"), ("opened_chapter", "开启章"),
            ("last_touched_chapter", "最近触达章"), ("source_chapter", "来源章"),
            ("source_version", "来源版本"), ("last_updated_version", "最近版本"),
        ])
        self._add_tags_block(layout, "相关角色", data.get("involved_characters"))
        self._add_text_block(layout, "描述", data.get("description"))
        self._add_list_block(layout, "关键细节", data.get("key_details"), quote=True)
        self._add_tags_block(layout, "关联伏笔", data.get("foreshadowing_related"))
        self._add_text_block(layout, "预期回收", data.get("expected_payoff"))
        self._add_text_block(layout, "回收提示", data.get("payoff_hint"))
        self._add_fact_sources_block(layout, data)

    def _render_worldbuilding_card(self, layout: QVBoxLayout, data: dict) -> None:
        self._add_meta_row(layout, data, [
            ("chapter", "章节"), ("version", "版本"), ("source_chapter", "来源章"),
            ("source_version", "来源版本"), ("last_updated_chapter", "最近更新章"),
        ])
        self._add_text_block(layout, "核心内容", data.get("core_summary") or data.get("description"))
        self._add_list_block(layout, "规则与约束", data.get("constraints"))
        self._add_tags_block(layout, "检索关键词", data.get("keywords"))
        full_passage = data.get("full_passage") or data.get("passage")
        self._add_text_block(layout, "完整关键设定原文", full_passage, quote=True)
    def _render_foreshadowing_card(self, layout: QVBoxLayout, data: dict) -> None:
        self._add_meta_row(layout, data, [
            ("status", "状态"), ("introduced_chapter", "埋入章"),
            ("last_touched_chapter", "最近触达章"), ("source_chapter", "来源章"),
            ("source_version", "来源版本"),
        ])
        self._add_text_block(layout, "伏笔", data.get("hint"), quote=self._is_quote_text(data.get("hint")))
        self._add_text_block(layout, "关联对象", data.get("relates_to"))
        self._add_text_block(layout, "下一步", data.get("next_step"))
        self._add_text_block(layout, "回收规则", data.get("reveal_rule"))

    def _render_timeline_card(self, layout: QVBoxLayout, data: dict) -> None:
        self._add_meta_row(layout, data, [
            ("chapter", "章节"),
            ("source_version", "来源版本"),
            ("occurrence_count", "关键事件次数"),
        ])
        self._add_text_block(layout, "事件", data.get("event"))
        self._add_text_block(layout, "意义", data.get("significance"))
        self._add_list_block(layout, "关键原文", data.get("key_passages"), quote=True)
        self._add_list_block(layout, "埋下伏笔", data.get("foreshadowing_hints"))

    def _render_dialogue_card(self, layout: QVBoxLayout, data: dict) -> None:
        self._add_meta_row(layout, data, [
            ("chapter", "章节"), ("version", "版本"), ("source_chapter", "来源章"),
            ("source_version", "来源版本"),
        ])
        self._add_text_block(layout, "说话人", data.get("speaker"))
        self._add_text_block(layout, "台词", data.get("dialogue"), quote=True)
        self._add_text_block(layout, "语境", data.get("context"))

    def _render_warning_card(self, layout: QVBoxLayout, data: dict) -> None:
        severity = data.get("severity", "minor")
        severity_label = {"error": "阻断", "major": "严重", "minor": "一般", "info": "提示"}.get(severity, severity)
        color = {"major": "#6f3131", "minor": "#66552a", "info": "#2f4e68"}.get(severity, "#3c3c3c")
        row = QHBoxLayout()
        row.addWidget(self._make_badge(severity_label, color, "#ffffff"))
        if data.get("type"):
            row.addWidget(self._make_badge(data.get("type"), "#303030", "#dcdcdc"))
        row.addStretch()
        layout.addLayout(row)
        self._add_text_block(layout, "说明", data.get("message"))
        self._add_tags_block(layout, "相关对象", data.get("related"))

    def _render_generic_card(self, layout: QVBoxLayout, data: dict) -> None:
        field_labels = {
            "name": "名称", "description": "描述", "significance": "作用", "first_appearance": "首次出现",
            "key_details": "关键细节", "atmosphere": "氛围", "source_chapter": "来源章",
            "source_version": "来源版本", "hidden": "是否隐藏",
        }
        for key, value in data.items():
            if not self._truthy_value(value):
                continue
            label = field_labels.get(key, key)
            if isinstance(value, list):
                self._add_list_block(layout, label, value)
            else:
                self._add_text_block(layout, label, value)

    def _format_card_plain_text(self, kind: str, data: dict) -> str:
        labels = {
            "name": "名称", "aliases": "别名", "traits": "角色特征", "relationships": "关系",
            "status": "状态", "importance": "重要性", "first_appearance": "首次出现",
            "notes": "备注", "key_details": "关键细节", "key_dialogues": "关键台词",
            "motivation": "动机", "arc": "成长弧线", "birth_date": "出生日期/纪年",
            "current_age": "当前年龄", "age_basis": "年龄依据", "life_stage": "人生/身份阶段",
            "current_date": "当前日期", "time_of_day": "当前时段", "elapsed_time": "累计流逝",
            "story_phase": "故事阶段", "calendar_system": "纪年体系", "current_location": "当前位置",
            "current_goal": "当前目标", "current_emotion": "当前情绪", "recent_action": "近期行动",
            "knowledge_state": "已知信息", "unresolved_conflicts": "未解决冲突",
            "description": "描述", "significance": "作用", "atmosphere": "氛围",
            "event": "事件", "key_passages": "关键原文", "foreshadowing_hints": "埋下伏笔",
            "involved_characters": "相关角色", "foreshadowing_related": "关联伏笔",
            "opened_chapter": "开启章", "last_touched_chapter": "最近触达章",
            "expected_payoff": "预期回收", "payoff_hint": "回收提示", "topic": "主题",
            "passage": "设定内容", "hint": "伏笔", "relates_to": "关联对象",
            "next_step": "下一步", "reveal_rule": "回收规则", "speaker": "说话人",
            "dialogue": "台词", "context": "语境", "rule": "规则内容",
            "severity": "严重度", "type": "类型", "message": "说明", "related": "相关对象",
            "chapter": "章节", "version": "版本", "source_chapter": "来源章",
            "source_version": "来源版本", "last_updated_chapter": "最近更新章",
            "last_updated_version": "最近版本", "hidden": "是否隐藏",
        }
        lines = [f"# {kind}"]
        for key, value in data.items():
            if not self._truthy_value(value):
                continue
            label = labels.get(key)
            if not label:
                continue
            if isinstance(value, list):
                lines.append(f"\n【{label}】")
                for item in value:
                    if isinstance(item, dict):
                        item_text = " / ".join(
                            f"{labels.get(k, k)}：{self._clean_display_text(v)}"
                            for k, v in item.items() if self._truthy_value(v)
                        )
                    else:
                        item_text = self._clean_display_text(item)
                    if item_text:
                        lines.append(f"- {item_text}")
            else:
                lines.append(f"\n【{label}】\n{self._clean_display_text(value)}")
        return "\n".join(lines)

    def _build_card_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)

        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("类型"))
        self._card_type_combo = QComboBox()
        self._card_type_combo.addItems(["全部", "角色", "地点", "剧情线", "设定", "伏笔"])
        self._card_type_combo.currentIndexChanged.connect(self._refresh_card_list)
        filter_row.addWidget(self._card_type_combo)

        filter_row.addWidget(QLabel("状态"))
        self._card_status_combo = QComboBox()
        self._card_status_combo.addItems(["全部", "当前活跃路径", "显示项", "隐藏项", "已解决伏笔/剧情线"])
        self._card_status_combo.currentIndexChanged.connect(self._refresh_card_list)
        filter_row.addWidget(self._card_status_combo)

        filter_row.addWidget(QLabel("来源章节"))
        self._card_chapter_filter = QLineEdit()
        self._card_chapter_filter.setPlaceholderText("如 3，留空为全部")
        self._card_chapter_filter.textChanged.connect(self._refresh_card_list)
        filter_row.addWidget(self._card_chapter_filter)

        refresh_btn = QPushButton("刷新")
        refresh_btn.clicked.connect(self._on_refresh_cards)
        filter_row.addWidget(refresh_btn)
        filter_row.addStretch()
        layout.addLayout(filter_row)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self._card_list = QListWidget()
        self._card_list.currentItemChanged.connect(self._on_card_selected)
        splitter.addWidget(self._card_list)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        self._card_detail = QTextEdit()
        self._card_detail.setReadOnly(True)
        self._card_detail.setStyleSheet("""
            QTextEdit {
                background-color: #252526; color: #dcdcdc;
                border: 1px solid #444; font-size: 13px;
                font-family: Consolas, "Microsoft YaHei UI", monospace;
            }
        """)
        right_layout.addWidget(self._card_detail, stretch=1)
        action_row = QHBoxLayout()
        form_btn = QPushButton("表单编辑")
        form_btn.clicked.connect(self._on_edit_card_form)
        edit_btn = QPushButton("编辑 JSON")
        edit_btn.clicked.connect(self._on_edit_card_json)
        delete_btn = QPushButton("删除条目")
        delete_btn.clicked.connect(self._on_delete_card)
        action_row.addStretch()
        action_row.addWidget(form_btn)
        action_row.addWidget(edit_btn)
        action_row.addWidget(delete_btn)
        right_layout.addLayout(action_row)
        splitter.addWidget(right)
        splitter.setSizes([280, 500])
        layout.addWidget(splitter, stretch=1)

        self._refresh_card_list()
        return widget

    def _card_source_label(self, data: dict, *, fallback_chapter_key: str = "chapter") -> str:
        chapter = (
            data.get("last_updated_chapter")
            or data.get("source_chapter")
            or data.get("first_appearance")
            or data.get("last_touched_chapter")
            or data.get("introduced_chapter")
            or data.get(fallback_chapter_key)
            or 0
        )
        version = data.get("last_updated_version") or data.get("source_version") or data.get("version") or 0
        snapshot = self._card_snapshot_label(self._safe_int(chapter), self._safe_int(version))
        if chapter and version:
            return f"第{chapter}章 v{version}{snapshot}"
        if chapter:
            return f"第{chapter}章{snapshot}"
        return "来源未知"

    def _card_snapshot_label(self, chapter: int, version: int = 0) -> str:
        if chapter <= 0:
            return ""
        entries = getattr(self._bible, "chapter_world_entries", {}) or {}
        exact_key = f"ch{chapter:04d}_v{version:03d}"
        has_snapshot = exact_key in entries
        if not has_snapshot and not version:
            prefix = f"ch{chapter:04d}_v"
            has_snapshot = any(str(key).startswith(prefix) for key in entries)
        return "；快照：已保存" if has_snapshot else "；快照：缺失"

    def _safe_int(self, value) -> int:
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    def _card_entries(self) -> list[dict]:
        entries = []
        for idx, item in enumerate(self._bible.characters):
            data = asdict(item)
            entries.append({
                "kind": "角色",
                "index": idx,
                "title": item.name or f"角色 {idx + 1}",
                "subtitle": item.traits[:80],
                "source": self._card_source_label(data),
                "hidden": bool(getattr(item, "hidden", False)),
                "resolved": False,
                "data": data,
            })
        for idx, item in enumerate(self._bible.locations):
            data = asdict(item)
            entries.append({
                "kind": "地点",
                "index": idx,
                "title": item.name or f"地点 {idx + 1}",
                "subtitle": item.description[:80],
                "source": self._card_source_label(data),
                "hidden": bool(getattr(item, "hidden", False)),
                "resolved": False,
                "data": data,
            })
        for idx, item in enumerate(self._bible.active_plot_threads):
            data = asdict(item)
            entries.append({
                "kind": "剧情线",
                "index": idx,
                "title": item.name or f"剧情线 {idx + 1}",
                "subtitle": item.description[:80],
                "source": self._card_source_label(data),
                "hidden": bool(getattr(item, "hidden", False)),
                "resolved": item.status == "resolved",
                "data": data,
            })
        for idx, item in enumerate(self._bible.key_worldbuilding_passages):
            data = dict(item)
            entries.append({
                "kind": "设定",
                "index": idx,
                "title": data.get("topic") or f"设定 {idx + 1}",
                "subtitle": data.get("passage", "")[:80],
                "source": self._card_source_label(data),
                "hidden": bool(data.get("hidden")),
                "resolved": False,
                "data": data,
            })
        for idx, item in enumerate(self._bible.global_foreshadowing):
            data = dict(item)
            status = data.get("status", "open")
            entries.append({
                "kind": "伏笔",
                "index": idx,
                "title": data.get("hint") or f"伏笔 {idx + 1}",
                "subtitle": data.get("relates_to", "") or data.get("next_step", "")[:80],
                "source": self._card_source_label(data),
                "hidden": bool(data.get("hidden")),
                "resolved": status in {"resolved", "已回收"},
                "data": data,
            })
        return entries

    def _passes_card_filter(self, entry: dict) -> bool:
        kind_filter = self._card_type_combo.currentText() if hasattr(self, "_card_type_combo") else "全部"
        if kind_filter != "全部" and entry["kind"] != kind_filter:
            return False

        data = entry.get("data", {})
        chapter_values = {
            data.get("source_chapter"),
            data.get("last_updated_chapter"),
            data.get("first_appearance"),
            data.get("chapter"),
            data.get("opened_chapter"),
            data.get("last_touched_chapter"),
            data.get("introduced_chapter"),
        }
        entry_chapters = {int(v) for v in chapter_values if str(v).isdigit()}

        status_filter = self._card_status_combo.currentText() if hasattr(self, "_card_status_combo") else "全部"
        if status_filter == "当前活跃路径" and not (entry_chapters & self._active_chapters):
            return False
        if status_filter == "显示项" and entry.get("hidden"):
            return False
        if status_filter == "隐藏项" and not entry.get("hidden"):
            return False
        if status_filter == "已解决伏笔/剧情线" and not entry.get("resolved"):
            return False

        chapter_text = self._card_chapter_filter.text().strip() if hasattr(self, "_card_chapter_filter") else ""
        if chapter_text:
            try:
                chapter = int(chapter_text)
            except ValueError:
                return True
            if chapter not in entry_chapters:
                return False
        return True

    def _refresh_card_list(self) -> None:
        if not hasattr(self, "_card_list"):
            return
        current_key = self._current_card_key()
        self._card_list.blockSignals(True)
        self._card_list.clear()
        selected_row = 0
        row = 0
        for entry in self._card_entries():
            if not self._passes_card_filter(entry):
                continue
            flags = []
            if entry.get("hidden"):
                flags.append("隐藏")
            if entry.get("resolved"):
                flags.append("已解决")
            flag_text = f" [{' / '.join(flags)}]" if flags else ""
            subtitle = f"\n{entry['subtitle']}" if entry.get("subtitle") else ""
            item = QListWidgetItem(f"{entry['kind']} · {entry['title']}{flag_text}\n{entry['source']}{subtitle}")
            item.setData(Qt.ItemDataRole.UserRole, {"kind": entry["kind"], "index": entry["index"]})
            self._card_list.addItem(item)
            if current_key == (entry["kind"], entry["index"]):
                selected_row = row
            row += 1
        self._card_list.blockSignals(False)
        if self._card_list.count():
            self._card_list.setCurrentRow(min(selected_row, self._card_list.count() - 1))
        else:
            self._card_detail.setPlainText("没有符合筛选条件的世界书条目。")

    def _current_card_key(self) -> tuple[str, int] | None:
        if not hasattr(self, "_card_list"):
            return None
        item = self._card_list.currentItem()
        if not item:
            return None
        data = item.data(Qt.ItemDataRole.UserRole) or {}
        return (data.get("kind"), int(data.get("index", -1)))

    def _get_card_data(self, kind: str, index: int):
        if kind == "时间状态" and index == 0:
            return self._bible.story_clock
        if kind == "角色" and 0 <= index < len(self._bible.characters):
            return self._bible.characters[index]
        if kind == "地点" and 0 <= index < len(self._bible.locations):
            return self._bible.locations[index]
        if kind == "规则" and 0 <= index < len(self._bible.rules):
            return {"rule": self._bible.rules[index]}
        if kind == "时间线" and 0 <= index < len(self._bible.timeline):
            return self._bible.timeline[index]
        if kind == "剧情线" and 0 <= index < len(self._bible.active_plot_threads):
            return self._bible.active_plot_threads[index]
        if kind == "设定" and 0 <= index < len(self._bible.key_worldbuilding_passages):
            return self._bible.key_worldbuilding_passages[index]
        if kind == "伏笔" and 0 <= index < len(self._bible.global_foreshadowing):
            return self._bible.global_foreshadowing[index]
        if kind == "关键对话" and 0 <= index < len(self._bible.global_key_dialogues):
            return self._bible.global_key_dialogues[index]
        if kind == "冲突提醒" and 0 <= index < len(self._bible.consistency_warnings):
            return self._bible.consistency_warnings[index]
        return None

    def _on_card_selected(self, current=None, previous=None) -> None:
        key = self._current_card_key()
        if not key:
            self._card_detail.setPlainText("请选择一个世界书条目。")
            return
        kind, index = key
        obj = self._get_card_data(kind, index)
        if obj is None:
            self._card_detail.setPlainText("条目不存在，可能已被删除。")
            return
        payload = dict(obj) if isinstance(obj, dict) else asdict(obj)
        self._card_detail.setPlainText(self._format_card_plain_text(kind, payload))

    def _on_refresh_cards(self) -> None:
        try:
            self._sync_from_editors()
        except Exception as exc:
            QMessageBox.warning(self, "解析失败", f"请先修正高级文本页内容：{exc}")
            return
        self._audit_and_refresh()

    def _replace_card_data(self, kind: str, index: int, payload: dict) -> None:
        if kind == "时间状态":
            allowed = {"current_date", "time_of_day", "elapsed_time", "story_phase", "calendar_system", "source_chapter", "source_version"}
            previous = dict(self._bible.story_clock or {})
            updated = {key: value for key, value in payload.items() if key in allowed}
            if previous and previous != updated:
                history = {
                    **previous,
                    "changed_fields": [key for key in allowed if previous.get(key) != updated.get(key)],
                    "change_source": "manual_edit",
                }
                if history not in self._bible.story_clock_history:
                    self._bible.story_clock_history.append(history)
            self._bible.story_clock = updated
        elif kind == "角色":
            from core.world_bible import CharacterEntry, Relationship
            rels = [Relationship(**r) for r in payload.get("relationships", []) if isinstance(r, dict)]
            data = {k: v for k, v in payload.items() if k in CharacterEntry.__dataclass_fields__ and k != "relationships"}
            self._bible.characters[index] = CharacterEntry(relationships=rels, **data)
        elif kind == "地点":
            from core.world_bible import LocationEntry
            data = {k: v for k, v in payload.items() if k in LocationEntry.__dataclass_fields__}
            self._bible.locations[index] = LocationEntry(**data)
        elif kind == "规则":
            value = str(payload.get("rule", "")).strip()
            self._bible.rules[index] = value
            if index < len(getattr(self._bible, "world_rules", [])):
                self._bible.world_rules[index].content = value
                self._bible.world_rules[index].name = value[:40]
        elif kind == "时间线":
            from core.world_bible import TimelineEntry
            data = {k: v for k, v in payload.items() if k in TimelineEntry.__dataclass_fields__}
            self._bible.timeline[index] = TimelineEntry(**data)
        elif kind == "剧情线":
            from core.world_bible import PlotThread
            data = {k: v for k, v in payload.items() if k in PlotThread.__dataclass_fields__}
            self._bible.active_plot_threads[index] = PlotThread(**data)
        elif kind == "设定":
            self._bible.key_worldbuilding_passages[index] = payload
        elif kind == "伏笔":
            self._bible.global_foreshadowing[index] = payload
        elif kind == "关键对话":
            self._bible.global_key_dialogues[index] = payload
        elif kind == "冲突提醒":
            self._bible.consistency_warnings[index] = payload

    def _edit_card(self, kind: str, index: int) -> None:
        self._edit_card_form(kind, index)

    def _on_edit_card_form(self) -> None:
        key = self._current_card_key()
        if not key:
            QMessageBox.warning(self, "未选择条目", "请先选择一个世界书条目。")
            return
        self._edit_card_form(*key)

    def _edit_card_form(self, kind: str, index: int) -> None:
        obj = self._get_card_data(kind, index)
        if obj is None:
            QMessageBox.warning(self, "条目不存在", "该条目可能已被删除。")
            return
        payload = dict(obj) if isinstance(obj, dict) else asdict(obj)
        field_map = {
            "角色": ["name", "traits", "status", "importance", "motivation", "arc", "current_location", "current_goal", "current_emotion", "recent_action", "knowledge_state", "current_age", "life_stage"],
            "地点": ["name", "description", "significance", "atmosphere"],
            "剧情线": ["name", "status", "importance", "description", "expected_payoff", "payoff_hint"],
            "设定": ["topic", "passage", "knowledge_type", "confidence", "locked", "hidden"],
            "伏笔": ["hint", "relates_to", "status", "next_step", "reveal_rule", "hidden"],
            "规则": ["rule"],
            "时间状态": ["current_date", "time_of_day", "elapsed_time", "story_phase", "calendar_system"],
        }
        fields = field_map.get(kind)
        if not fields:
            self._on_edit_card_json()
            return
        dialog = QDialog(self)
        dialog.setWindowTitle(f"表单编辑 · {kind}")
        dialog.resize(620, 520)
        layout = QVBoxLayout(dialog)
        form = QFormLayout()
        editors = {}
        for field_name in fields:
            value = payload.get(field_name, "")
            if field_name in {"status", "importance", "knowledge_type"}:
                editor = QComboBox(dialog)
                choices = {
                    "status": ["alive", "dead", "missing", "transformed", "active", "dormant", "resolved", "open", "noticed", "advanced"],
                    "importance": ["major", "normal", "minor"],
                    "knowledge_type": ["canon", "constraint", "inference", "author_plan"],
                }[field_name]
                editor.addItems(list(dict.fromkeys([str(value), *choices])))
                editor.setCurrentText(str(value))
            elif field_name in {"locked", "hidden"}:
                editor = QComboBox(dialog)
                editor.addItems(["false", "true"])
                editor.setCurrentText("true" if value else "false")
            elif field_name in {"traits", "description", "passage", "motivation", "arc", "knowledge_state", "expected_payoff", "payoff_hint", "next_step", "reveal_rule"}:
                editor = QTextEdit(dialog)
                editor.setPlainText(str(value or ""))
                editor.setMaximumHeight(90)
            else:
                editor = QLineEdit(dialog)
                editor.setText(str(value or ""))
            editors[field_name] = editor
            form.addRow(self._field_label(field_name), editor)
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, dialog)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        for field_name, editor in editors.items():
            if isinstance(editor, QTextEdit):
                value = editor.toPlainText().strip()
            elif isinstance(editor, QComboBox):
                value = editor.currentText()
            else:
                value = editor.text().strip()
            if field_name in {"locked", "hidden"}:
                value = value == "true"
            elif isinstance(payload.get(field_name), float):
                try:
                    value = float(value)
                except ValueError:
                    value = payload.get(field_name)
            payload[field_name] = value
        self._replace_card_data(kind, index, payload)
        self._audit_and_refresh()
    def _delete_card(self, kind: str, index: int) -> None:
        reply = QMessageBox.question(self, "确认删除", f"删除这个{kind}条目？")
        if reply != QMessageBox.StandardButton.Yes:
            return
        if kind == "时间状态" and index == 0:
            self._bible.story_clock = {}
        elif kind == "角色" and 0 <= index < len(self._bible.characters):
            del self._bible.characters[index]
        elif kind == "地点" and 0 <= index < len(self._bible.locations):
            del self._bible.locations[index]
        elif kind == "规则" and 0 <= index < len(self._bible.rules):
            del self._bible.rules[index]
        elif kind == "时间线" and 0 <= index < len(self._bible.timeline):
            del self._bible.timeline[index]
        elif kind == "剧情线" and 0 <= index < len(self._bible.active_plot_threads):
            del self._bible.active_plot_threads[index]
        elif kind == "设定" and 0 <= index < len(self._bible.key_worldbuilding_passages):
            del self._bible.key_worldbuilding_passages[index]
        elif kind == "伏笔" and 0 <= index < len(self._bible.global_foreshadowing):
            del self._bible.global_foreshadowing[index]
        elif kind == "关键对话" and 0 <= index < len(self._bible.global_key_dialogues):
            del self._bible.global_key_dialogues[index]
        elif kind == "冲突提醒" and 0 <= index < len(self._bible.consistency_warnings):
            del self._bible.consistency_warnings[index]
        self._audit_and_refresh()

    def _on_edit_card_json(self) -> None:
        key = self._current_card_key()
        if not key:
            QMessageBox.warning(self, "未选择条目", "请先选择一个世界书条目。")
            return
        kind, index = key
        obj = self._get_card_data(kind, index)
        if obj is None:
            QMessageBox.warning(self, "条目不存在", "该条目已不存在。")
            return
        payload = dict(obj) if isinstance(obj, dict) else asdict(obj)

        dialog = QDialog(self)
        dialog.setWindowTitle(f"编辑{kind} JSON")
        dialog.resize(700, 560)
        layout = QVBoxLayout(dialog)
        edit = QTextEdit(dialog)
        edit.setPlainText(json.dumps(payload, ensure_ascii=False, indent=2))
        edit.setStyleSheet("font-family: Consolas, monospace; font-size: 13px;")
        layout.addWidget(edit)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            dialog,
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            new_payload = json.loads(edit.toPlainText())
            if not isinstance(new_payload, dict):
                raise ValueError("JSON 根节点必须是对象")
            self._replace_card_data(kind, index, new_payload)
        except Exception as exc:
            QMessageBox.critical(self, "JSON 无效", f"无法保存：{exc}")
            return
        self._audit_and_refresh()
        QMessageBox.information(self, "已保存", "条目已更新。")

    def _on_delete_card(self) -> None:
        key = self._current_card_key()
        if not key:
            QMessageBox.warning(self, "未选择条目", "请先选择一个世界书条目。")
            return
        kind, index = key
        obj = self._get_card_data(kind, index)
        if obj is None:
            QMessageBox.warning(self, "条目不存在", "该条目已不存在。")
            return
        title = obj.get("topic") or obj.get("hint") if isinstance(obj, dict) else getattr(obj, "name", "")
        reply = QMessageBox.question(
            self,
            "确认删除",
            f"确定删除{kind}「{title or index + 1}」？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        if kind == "角色":
            del self._bible.characters[index]
        elif kind == "地点":
            del self._bible.locations[index]
        elif kind == "剧情线":
            del self._bible.active_plot_threads[index]
        elif kind == "设定":
            del self._bible.key_worldbuilding_passages[index]
        elif kind == "伏笔":
            del self._bible.global_foreshadowing[index]
        self._audit_and_refresh()
        QMessageBox.information(self, "已删除", "条目已删除。")

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
            lines.append(
                f"- 第{t.chapter}章：{t.event} ({t.significance}) [关键事件次数：{t.occurrence_count}]"
            )
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
        lines = ["# 世界书健康检查\n"]
        warnings = getattr(self._bible, "consistency_warnings", []) or []
        if not warnings:
            return "未发现明显冲突/提醒。"
        for item in warnings:
            severity = item.get("severity", "minor")
            severity_label = {"error": "阻断", "major": "严重", "minor": "一般", "info": "提示"}.get(severity, severity)
            issue_type = item.get("type", "冲突")
            message = item.get("message", "")
            related = "、".join(str(x) for x in item.get("related", []) if x)
            line = f"- [{severity_label}] {issue_type}：{message}"
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
                count_match = re.search(r"\s*\[关键事件次数：(\d+)\]\s*$", after_ch)
                occurrence_count = int(count_match.group(1)) if count_match else 1
                if count_match:
                    after_ch = after_ch[:count_match.start()].strip()
                if "(" in after_ch and after_ch.endswith(")"):
                    event = after_ch[:after_ch.rindex("(")].strip()
                    significance = after_ch[after_ch.rindex("(")+1:-1].strip()
                else:
                    event = after_ch.strip()
                    significance = ""
                current = {
                    "chapter": chapter,
                    "event": event,
                    "significance": significance,
                    "occurrence_count": occurrence_count,
                    "key_passages": [],
                    "foreshadowing_hints": [],
                }
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
        raw = self._advanced_edit.toPlainText().strip()
        if not raw:
            return
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return
        if isinstance(data, dict) and data != asdict(self._bible):
            from core.world_bible import dict_to_world_bible
            self._bible = dict_to_world_bible(data)

    def _audit_and_refresh(self) -> None:
        try:
            from core.world_bible import audit_world_bible_consistency
            self._bible.consistency_warnings = audit_world_bible_consistency(self._bible)
        except Exception:
            pass
        self._advanced_edit.setPlainText(json.dumps(asdict(self._bible), ensure_ascii=False, indent=2))
        self._refresh_kind_cards()
        self._refresh_v2_tabs()
        if hasattr(self, "_card_list"):
            self._refresh_card_list()

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

    def _refresh_v2_tabs(self) -> None:
        if hasattr(self, "_snapshot_view"):
            snapshots = getattr(self._bible, "chapter_snapshots", {}) or {}
            summary = {
                key: {
                    "chapter": value.get("chapter"),
                    "version": value.get("version"),
                    "categories": {
                        name: len(value.get("data", {}).get(name, []))
                        for name in ("characters", "locations", "rules", "timeline", "plot_threads")
                    },
                }
                for key, value in snapshots.items() if isinstance(value, dict)
            }
            self._snapshot_view.setPlainText(json.dumps(summary, ensure_ascii=False, indent=2))
        if hasattr(self, "_override_view"):
            self._override_view.setPlainText(json.dumps([asdict(item) for item in getattr(self._bible, "manual_overrides", [])], ensure_ascii=False, indent=2))
        if hasattr(self, "_diagnostic_view"):
            payload = {
                "migration": getattr(self._bible, "migration_info", {}),
                "diagnostics": getattr(self._bible, "diagnostics", {}),
                "facts": len(getattr(self._bible, "facts", [])),
                "rules": len(getattr(self._bible, "world_rules", [])),
                "pending_duplicates": len([item for item in getattr(self._bible, "duplicate_candidates", []) if item.get("status", "pending") == "pending"]),
                "merge_history": [asdict(item) for item in getattr(self._bible, "merge_history", [])],
            }
            self._diagnostic_view.setPlainText(json.dumps(payload, ensure_ascii=False, indent=2))

    def _capture_manual_changes(self) -> None:
        from core.world_bible import _flat_view_dict, record_manual_view_changes
        record_manual_view_changes(self._bible, self._original_view)
        self._original_view = copy.deepcopy(_flat_view_dict(self._bible))
        self._refresh_v2_tabs()

    def _show_text_dialog(self, title: str, text: str) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        dialog.resize(760, 620)
        layout = QVBoxLayout(dialog)
        edit = QTextEdit(dialog)
        edit.setReadOnly(True)
        edit.setPlainText(text)
        layout.addWidget(edit)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok, dialog)
        buttons.accepted.connect(dialog.accept)
        layout.addWidget(buttons)
        dialog.exec()

    def _on_preview_retrieval(self) -> None:
        query, ok = QInputDialog.getMultiLineText(self, "世界书注入预览", "输入章节标题、情节或要求：")
        if not ok:
            return
        from core.world_bible import format_relevant_world_bible_for_prompt
        text, diagnostics = format_relevant_world_bible_for_prompt(
            self._bible,
            query,
            active_chapters=self._active_chapters,
            target_chapter=max(self._active_chapters or {getattr(self._bible, "last_updated_chapter", 0)}),
            return_diagnostics=True,
        )
        self._refresh_v2_tabs()
        self._show_text_dialog("世界书注入预览", text + "\n\n--- 检索诊断 ---\n" + json.dumps(diagnostics, ensure_ascii=False, indent=2))

    def _on_view_fact_history(self) -> None:
        key = self._current_card_key()
        entity_id = ""
        title = "全部事实"
        if key:
            obj = self._get_card_data(*key)
            if isinstance(obj, dict):
                entity_id = str(obj.get("id", ""))
                title = str(obj.get("name") or obj.get("topic") or obj.get("hint") or title)
            elif obj is not None:
                entity_id = str(getattr(obj, "id", ""))
                title = str(getattr(obj, "name", title))
        facts = [asdict(item) for item in getattr(self._bible, "facts", []) if not entity_id or item.subject_id == entity_id]
        self._show_text_dialog(f"事实历史 · {title}", json.dumps(facts, ensure_ascii=False, indent=2))

    def _on_review_duplicate(self) -> None:
        candidate = next((item for item in getattr(self._bible, "duplicate_candidates", []) if item.get("status", "pending") == "pending"), None)
        if not candidate:
            QMessageBox.information(self, "重复候选", "当前没有待确认的重复实体。")
            return
        names = "、".join(candidate.get("names", []))
        reply = QMessageBox.question(
            self,
            "确认重复实体",
            f"疑似重复：{names}\n置信度：{candidate.get('confidence', 0):.0%}\n\n确认合并？选择“否”将拒绝此候选。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if reply == QMessageBox.StandardButton.Cancel:
            return
        if reply == QMessageBox.StandardButton.No:
            candidate["status"] = "rejected"
        else:
            from core.world_bible import confirm_duplicate_candidate
            if not confirm_duplicate_candidate(self._bible, candidate.get("id", "")):
                QMessageBox.warning(self, "合并失败", "候选实体已变化，无法安全合并。")
                return
        self._audit_and_refresh()

    def _on_undo_merge(self) -> None:
        from core.world_bible import undo_entity_merge
        if not undo_entity_merge(self._bible):
            QMessageBox.information(self, "撤销合并", "没有可撤销的实体合并。")
            return
        self._audit_and_refresh()
        QMessageBox.information(self, "撤销合并", "最近一次实体合并已撤销。")
    def get_bible(self):
        """返回修改后的 WorldBible 对象（在 exec 返回 Accepted 后调用）"""
        self._capture_manual_changes()
        return self._bible

    def _on_save(self):
        """保存所有标签页的修改回 WorldBible"""
        try:
            self._sync_from_editors()
            self._capture_manual_changes()

            try:
                from core.world_bible import audit_world_bible_consistency
                self._bible.consistency_warnings = audit_world_bible_consistency(self._bible)
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

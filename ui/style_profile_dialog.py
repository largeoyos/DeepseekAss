"""Style profile library and extraction dialog."""
from __future__ import annotations

import os
import threading

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
    QLineEdit,
)

from core.style_profiles import (
    StyleAnchor,
    StyleExtractionCancelled,
    StyleExtractionService,
    StyleProfile,
    StyleProfileRepository,
    StyleSourceDocument,
)
from ui.dialog_utils import apply_responsive_dialog_size


class _StyleSignals(QObject):
    progress = pyqtSignal(str)
    completed = pyqtSignal(object, str)
    failed = pyqtSignal(str)


def _read_text(path: str) -> str:
    for encoding in ("utf-8", "gb18030", "gbk"):
        try:
            with open(path, "r", encoding=encoding) as handle:
                return handle.read()
        except UnicodeDecodeError:
            continue
    return ""


class StyleProfileDialog(QDialog):
    profiles_changed = pyqtSignal()

    def __init__(self, parent, novel_manager, client, model: str, *, book_title: str = "") -> None:
        super().__init__(parent)
        self.manager = novel_manager
        self.client = client
        self.model = model
        self.book_title = book_title
        self.repository = StyleProfileRepository(novel_manager)
        self._busy = False
        self._cancel_requested = False
        self._replace_profile_id = ""
        self._signals = _StyleSignals()
        self.setWindowTitle("文风档案管理")
        self._build_ui()
        self._signals.progress.connect(self._status.setText)
        self._signals.completed.connect(self._on_extraction_completed)
        self._signals.failed.connect(self._on_extraction_failed)
        self._refresh()
        apply_responsive_dialog_size(
            self,
            1100,
            760,
            minimum_width=900,
            minimum_height=620,
            width_ratio=0.82,
            height_ratio=0.82,
        )

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        splitter = QSplitter()
        root.addWidget(splitter, stretch=1)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        self._list = QListWidget()
        self._list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._list.currentItemChanged.connect(self._load_selected)
        left_layout.addWidget(self._list, stretch=1)
        for label, callback in (
            ("从文件提取", self._extract_file),
            ("从文件夹提取", self._extract_folder),
            ("从当前书籍提取", self._extract_book),
        ):
            button = QPushButton(label)
            button.clicked.connect(callback)
            left_layout.addWidget(button)
        splitter.addWidget(left)

        right = QWidget()
        form = QVBoxLayout(right)
        form.addWidget(QLabel("档案名称"))
        self._name = QLineEdit()
        form.addWidget(self._name)
        form.addWidget(QLabel("说明"))
        self._description = QTextEdit()
        self._description.setMaximumHeight(70)
        form.addWidget(self._description)
        self._meta = QLabel("")
        self._meta.setWordWrap(True)
        self._meta.setStyleSheet("color: #999;")
        form.addWidget(self._meta)
        form.addWidget(QLabel("稳定写法（每行一条）"))
        self._rules = QTextEdit()
        form.addWidget(self._rules, stretch=2)
        form.addWidget(QLabel("应避免写法（每行一条）"))
        self._avoid = QTextEdit()
        form.addWidget(self._avoid, stretch=1)
        form.addWidget(QLabel("核心模仿例文（最多20段；类型可用通用/对白/动作/心理/环境/章末；用 --- 分隔）"))
        self._anchors = QTextEdit()
        form.addWidget(self._anchors, stretch=2)

        action_row = QHBoxLayout()
        for label, callback in (
            ("保存修改", self._save_selected),
            ("复制", self._duplicate_selected),
            ("用当前书重新提取", self._reextract_selected),
            ("删除", self._delete_selected),
        ):
            button = QPushButton(label)
            button.clicked.connect(callback)
            action_row.addWidget(button)
        form.addLayout(action_row)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)

        status_row = QHBoxLayout()
        self._status = QLabel("就绪")
        self._status.setWordWrap(True)
        status_row.addWidget(self._status, stretch=1)
        self._cancel_btn = QPushButton("取消提取")
        self._cancel_btn.setEnabled(False)
        self._cancel_btn.clicked.connect(self._cancel_extraction)
        status_row.addWidget(self._cancel_btn)
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.accept)
        status_row.addWidget(close_btn)
        root.addLayout(status_row)

    def _refresh(self, select_id: str = "") -> None:
        current = select_id or self._selected_id()
        self._list.blockSignals(True)
        self._list.clear()
        for profile in self.repository.list_profiles():
            item = QListWidgetItem(f"{profile.name}\n{profile.sample_chars} 字 · {profile.confidence} · v{profile.revision}")
            item.setData(256, profile.profile_id)
            self._list.addItem(item)
            if profile.profile_id == current:
                self._list.setCurrentItem(item)
        self._list.blockSignals(False)
        if self._list.currentItem() is None and self._list.count():
            self._list.setCurrentRow(0)
        else:
            self._load_selected(self._list.currentItem(), None)

    def _selected_id(self) -> str:
        item = self._list.currentItem()
        return str(item.data(256) or "") if item else ""

    def _load_selected(self, current, _previous) -> None:
        profile = self.repository.get(str(current.data(256))) if current else None
        enabled = profile is not None
        for widget in (self._name, self._description, self._rules, self._avoid, self._anchors):
            widget.setEnabled(enabled)
        if profile is None:
            self._name.clear()
            self._description.clear()
            self._rules.clear()
            self._avoid.clear()
            self._anchors.clear()
            self._meta.setText("暂无文风档案。")
            return
        self._name.setText(profile.name)
        self._description.setPlainText(profile.description)
        self._rules.setPlainText("\n".join(profile.stable_rules))
        self._avoid.setPlainText("\n".join(profile.avoid_rules))
        blocks = [f"{item.facet}|{item.source_name}\n{item.text}" for item in profile.anchors]
        self._anchors.setPlainText("\n---\n".join(blocks))
        self._meta.setText(
            f"来源：{'、'.join(profile.source_names) or '手动'}\n"
            f"样本：{profile.sample_chars} 字，{profile.chunk_count} 块；置信度：{profile.confidence}；"
            f"模型：{profile.extraction_model or '未知'}"
        )

    @staticmethod
    def _lines(text: str) -> list[str]:
        result: list[str] = []
        for line in text.splitlines():
            value = line.strip().lstrip("-• ")
            if value and value not in result:
                result.append(value)
        return result

    def _parse_anchors(self) -> list[StyleAnchor]:
        facet_aliases = {
            "通用": "general",
            "对白": "dialogue",
            "动作": "action",
            "心理": "psychology",
            "环境": "environment",
            "章末": "ending",
        }
        result: list[StyleAnchor] = []
        for block in self._anchors.toPlainText().split("\n---\n"):
            lines = block.strip().splitlines()
            if len(lines) < 2:
                continue
            head = lines[0].split("|", 1)
            raw_facet = head[0].strip() or "general"
            facet = facet_aliases.get(raw_facet, raw_facet)
            result.append(StyleAnchor(
                facet=facet if facet in set(facet_aliases.values()) else "general",
                source_name=head[1].strip() if len(head) > 1 else "手动",
                text="\n".join(lines[1:]).strip()[:700],
                reason="用户确认的形式范例",
            ))
        return result[:20]

    def _save_selected(self) -> None:
        profile = self.repository.get(self._selected_id())
        if profile is None:
            return
        if not self._name.text().strip():
            QMessageBox.warning(self, "提示", "档案名称不能为空。")
            return
        profile.name = self._name.text().strip()
        profile.description = self._description.toPlainText().strip()
        profile.stable_rules = self._lines(self._rules.toPlainText())[:12]
        profile.avoid_rules = self._lines(self._avoid.toPlainText())[:8]
        profile.anchors = self._parse_anchors()
        profile.revision += 1
        self.repository.save(profile)
        self._refresh(profile.profile_id)
        self.profiles_changed.emit()
        self._status.setText("文风档案已保存。")

    def _duplicate_selected(self) -> None:
        profile_id = self._selected_id()
        if not profile_id:
            return
        name, ok = QInputDialog.getText(self, "复制文风", "新档案名称：")
        if ok:
            profile = self.repository.duplicate(profile_id, name)
            self._refresh(profile.profile_id)
            self.profiles_changed.emit()

    def _delete_selected(self) -> None:
        profile = self.repository.get(self._selected_id())
        if profile is None:
            return
        affected = [title for title in self.manager.list_books() if getattr(self.manager.load_meta(title), "style_profile_id", "") == profile.profile_id]
        suffix = f"\n将同时清除以下书籍的默认绑定：{'、'.join(affected)}" if affected else ""
        if QMessageBox.question(self, "删除文风", f"确定删除“{profile.name}”吗？{suffix}") != QMessageBox.StandardButton.Yes:
            return
        self.repository.delete(profile.profile_id)
        self._refresh()
        self.profiles_changed.emit()

    def _extract_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "选择文风样本", "", "文本文件 (*.txt *.md *.html *.htm)")
        if not path:
            return
        text = _read_text(path)
        if not text.strip():
            QMessageBox.warning(self, "提示", "未能读取样本文本。")
            return
        self._start_extraction([StyleSourceDocument(os.path.basename(path), text)], os.path.splitext(os.path.basename(path))[0], "file")

    def _extract_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "选择文风样本文件夹")
        if not folder:
            return
        documents = []
        for name in sorted(os.listdir(folder)):
            if os.path.splitext(name)[1].lower() not in {".txt", ".md", ".html", ".htm"}:
                continue
            text = _read_text(os.path.join(folder, name))
            if text.strip():
                documents.append(StyleSourceDocument(name, text))
        if not documents:
            QMessageBox.warning(self, "提示", "文件夹中没有可读取的文本文件。")
            return
        self._start_extraction(documents, os.path.basename(folder), "folder")

    def _book_documents(self) -> list[StyleSourceDocument]:
        if not self.book_title:
            return []
        result = []
        for node in self.manager.get_active_path_nodes(self.book_title):
            content = self.manager.read_chapter_node(self.book_title, str(node.get("id", ""))) or ""
            if content.strip():
                result.append(StyleSourceDocument(str(node.get("title") or node.get("id")), content))
        return result

    def _extract_book(self) -> None:
        documents = self._book_documents()
        if not documents:
            QMessageBox.warning(self, "提示", "当前书籍没有可用于提取的章节。")
            return
        self._start_extraction(documents, self.book_title, "book")

    def _reextract_selected(self) -> None:
        if not self._selected_id():
            return
        documents = self._book_documents()
        if not documents:
            QMessageBox.warning(self, "提示", "重新提取需要当前书籍存在章节。")
            return
        self._replace_profile_id = self._selected_id()
        profile = self.repository.get(self._replace_profile_id)
        self._start_extraction(documents, profile.name if profile else self.book_title, "book")

    def _start_extraction(self, documents: list[StyleSourceDocument], base_name: str, source_kind: str) -> None:
        if self._busy:
            return
        service = StyleExtractionService(self.client, self.repository)
        calls = service.estimate_calls(documents)
        total_chars = sum(len(item.text) for item in documents)
        self._busy = True
        self._cancel_requested = False
        self._cancel_btn.setEnabled(True)
        self._status.setText(f"准备分析 {total_chars} 字，预计约 {calls} 次模型调用……")
        run_id = f"manual_{self._replace_profile_id or base_name}_{total_chars}"

        def run() -> None:
            try:
                profiles = service.extract_documents(
                    documents, self.model, base_name=base_name, source_kind=source_kind,
                    progress=lambda message, current, total: self._signals.progress.emit(f"{message}（{current + 1}/{total}）"),
                    cancelled=lambda: self._cancel_requested,
                    run_id=run_id,
                )
                self._signals.completed.emit(profiles, self._replace_profile_id)
            except StyleExtractionCancelled:
                self._signals.failed.emit("文风提取已取消；已完成分块可在下次继续使用。")
            except Exception as exc:
                self._signals.failed.emit(f"文风提取失败：{exc}")

        threading.Thread(target=run, daemon=True).start()

    def _cancel_extraction(self) -> None:
        self._cancel_requested = True
        self._status.setText("正在取消；当前模型调用返回后停止……")

    def _on_extraction_completed(self, profiles: list[StyleProfile], replace_id: str) -> None:
        selected = ""
        for index, profile in enumerate(profiles):
            if index == 0 and replace_id:
                old = self.repository.get(replace_id)
                profile.profile_id = replace_id
                profile.revision = (old.revision + 1) if old else 1
                profile.created_at = old.created_at if old else profile.created_at
            self.repository.save(profile)
            selected = selected or profile.profile_id
        self._replace_profile_id = ""
        self._busy = False
        self._cancel_btn.setEnabled(False)
        self._refresh(selected)
        self.profiles_changed.emit()
        self._status.setText(f"提取完成：生成 {len(profiles)} 个文风档案。")

    def _on_extraction_failed(self, message: str) -> None:
        self._replace_profile_id = ""
        self._busy = False
        self._cancel_btn.setEnabled(False)
        self._status.setText(message)

    def closeEvent(self, event) -> None:
        if self._busy:
            QMessageBox.information(self, "正在提取", "请先取消文风提取，等待当前调用结束后再关闭。")
            event.ignore()
            return
        super().closeEvent(event)


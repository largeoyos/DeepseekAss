from __future__ import annotations

import os
import shutil

import markdown
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTextBrowser,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)


NOTE_PREVIEW_CSS = """
:root {
    --paper: #ffffff;
    --ink: #4a5568;
    --ink-strong: #1a202c;
    --blue: #0984e3;
    --green: #2ecc71;
    --amber: #ffb347;
    --line: #e2e8f0;
    --muted: #718096;
}
* { box-sizing: border-box; }
body {
    margin: 0;
    padding: 24px;
    background: #f8f9fa;
    color: var(--ink);
    font-family: "Microsoft YaHei", "Segoe UI", sans-serif;
    font-size: 15px;
    line-height: 1.9;
}
.markdown-body {
    max-width: 900px;
    margin: 0 auto;
    padding: 42px 44px;
    background: var(--paper);
    border: 1px solid rgba(9, 132, 227, 0.10);
    border-radius: 16px;
    box-shadow: 0 12px 32px rgba(31, 41, 55, 0.08);
}
h1, h2, h3, h4, h5, h6 {
    color: var(--ink-strong);
    font-weight: 650;
    line-height: 1.3;
}
h1 {
    margin: 0 0 1.05em;
    padding-bottom: .45em;
    border-bottom: 2px solid rgba(9, 132, 227, .30);
    color: #2b6cb0;
    font-size: 2.15em;
    text-align: center;
}
h2 {
    margin-top: 1.8em;
    padding-bottom: .32em;
    border-bottom: 1px solid rgba(99, 102, 241, .22);
    color: #3b82f6;
    font-size: 1.65em;
}
h2::before { content: "◆ "; color: var(--amber); font-size: .7em; }
h3 { margin-top: 1.55em; color: #4338ca; font-size: 1.3em; }
p { margin: 0 0 1.2em; }
strong {
    padding: 0 .22em;
    border-radius: 4px;
    background: rgba(255, 179, 71, .20);
    color: #2d3748;
}
em { color: #047857; }
a { color: var(--blue); text-decoration: none; border-bottom: 1px dashed rgba(9, 132, 227, .45); }
a:hover { color: #2563eb; border-bottom-style: solid; }
blockquote {
    margin: 1.45em 0;
    padding: .85em 1.2em;
    border-left: 4px solid #3b82f6;
    border-radius: 0 10px 10px 0;
    background: rgba(59, 130, 246, .055);
    color: #4a5568;
}
blockquote p:last-child { margin-bottom: 0; }
code {
    padding: .16em .42em;
    border-radius: 5px;
    background: rgba(148, 163, 184, .16);
    color: #d53f8c;
    font-family: "Cascadia Mono", Consolas, monospace;
    font-size: .9em;
}
pre {
    margin: 1.5em 0;
    padding: 1.2em;
    overflow: auto;
    border-radius: 10px;
    background: #1e293b;
    box-shadow: inset 0 2px 4px rgba(0, 0, 0, .22);
}
pre code { padding: 0; background: transparent; color: #f8fafc; font-size: .88em; }
table { width: 100%; margin: 1.45em 0; border-collapse: collapse; overflow: hidden; border-radius: 8px; }
th, td { padding: 10px 14px; border: 1px solid var(--line); text-align: left; }
th { background: #f1f5f9; color: #1e293b; font-weight: 650; }
tr:nth-child(even) { background: rgba(248, 250, 252, .7); }
ul, ol { padding-left: 1.8em; margin-bottom: 1.3em; }
li { margin: .38em 0; }
li::marker { color: #3b82f6; }
hr { height: 2px; margin: 2em 0; border: 0; background: rgba(148, 163, 184, .22); }
img { max-width: 100%; margin: 1em 0; border-radius: 10px; box-shadow: 0 5px 14px rgba(0,0,0,.10); }
.empty-note { margin: 5em 0; text-align: center; color: var(--muted); font-style: italic; }
"""


class MarkdownWorkspaceWidget(QWidget):
    """Encrypted per-user Markdown notes with direct editing and live preview."""

    def __init__(self, parent, *, user_dir: str, auth, enc_key: bytes | None) -> None:
        super().__init__(parent)
        self._root = os.path.join(user_dir, "markdown_workspace")
        self._auth = auth
        self._enc_key = enc_key
        self._current_path = ""
        self._dirty = False
        os.makedirs(self._root, exist_ok=True)
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.timeout.connect(self._render_preview)
        self._build_ui()
        self.refresh_tree()

    def _build_ui(self) -> None:
        self.setObjectName("markdownWorkspace")
        self.setStyleSheet("""
            #markdownWorkspace { background: #f8f9fa; }
            QLabel#notesStatus { color: #718096; font-size: 12px; }
            QWidget#notesToolbar { background: #ffffff; border: 1px solid #e9eef2; border-radius: 12px; }
            QLineEdit#noteSearch {
                min-height: 30px; padding: 4px 10px; border: 1px solid #dce6ee;
                border-radius: 8px; background: #fbfdff; color: #2d3436;
            }
            QLineEdit#noteSearch:focus { border-color: #0984e3; background: #ffffff; }
            QPushButton#notePrimary {
                background: #2ecc71; color: #ffffff; border: none; border-radius: 8px;
                padding: 7px 12px; font-weight: 700;
            }
            QPushButton#notePrimary:hover { background: #27ae60; }
            QPushButton#noteAction {
                background: #ffffff; color: #2d3436; border: 1px solid #dce6ee;
                border-radius: 8px; padding: 7px 11px;
            }
            QPushButton#noteAction:hover { background: #eef8ff; border-color: #8ec5ee; color: #0984e3; }
            QPushButton#noteAction:disabled { color: #a0aec0; background: #f8fafc; border-color: #edf2f7; }
            QTreeWidget {
                background: #ffffff; color: #2d3436; border: 1px solid #e9eef2;
                border-radius: 12px; padding: 6px;
            }
            QTreeWidget::header { background: #f6fbff; color: #0984e3; border: none; font-weight: 700; }
            QTreeWidget::item { min-height: 27px; padding: 2px 5px; border-radius: 5px; }
            QTreeWidget::item:hover { background: #eef8ff; }
            QTreeWidget::item:selected { background: #dbeeff; color: #0b5d99; }
            QTextEdit#noteEditor {
                background: #ffffff; color: #273444; border: 1px solid #e9eef2;
                border-radius: 12px; padding: 14px; selection-background-color: #b8dcf4;
            }
            QTextBrowser#notePreview { background: #f8f9fa; border: 1px solid #e9eef2; border-radius: 12px; }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)

        toolbar = QWidget()
        toolbar.setObjectName("notesToolbar")
        toolbar_layout = QHBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(8, 7, 8, 7)
        toolbar_layout.setSpacing(7)
        self._new_note_button = QPushButton("＋ 新建笔记")
        self._new_note_button.setObjectName("notePrimary")
        self._new_note_button.clicked.connect(self._new_file)
        toolbar_layout.addWidget(self._new_note_button)
        self._new_folder_button = QPushButton("新建文件夹")
        self._new_folder_button.setObjectName("noteAction")
        self._new_folder_button.clicked.connect(self._new_folder)
        toolbar_layout.addWidget(self._new_folder_button)
        self._save_button = QPushButton("保存  Ctrl+S")
        self._save_button.setObjectName("noteAction")
        self._save_button.clicked.connect(self.save_current)
        toolbar_layout.addWidget(self._save_button)
        self._rename_button = QPushButton("重命名")
        self._rename_button.setObjectName("noteAction")
        self._rename_button.clicked.connect(self._rename_selected)
        toolbar_layout.addWidget(self._rename_button)
        self._export_button = QPushButton("导出")
        self._export_button.setObjectName("noteAction")
        self._export_button.clicked.connect(self._export_file)
        toolbar_layout.addWidget(self._export_button)
        toolbar_layout.addStretch(1)
        self._status = QLabel("选择一篇笔记开始编辑")
        self._status.setObjectName("notesStatus")
        self._status.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        toolbar_layout.addWidget(self._status)
        self._search = QLineEdit()
        self._search.setObjectName("noteSearch")
        self._search.setPlaceholderText("搜索笔记名称…")
        self._search.setClearButtonEnabled(True)
        self._search.setMaximumWidth(260)
        self._search.textChanged.connect(self._apply_filter)
        toolbar_layout.addWidget(self._search)
        layout.addWidget(toolbar)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        self._tree = QTreeWidget()
        self._tree.setHeaderLabel("笔记库 · 单击文件即可编辑")
        self._tree.setAnimated(True)
        self._tree.setMinimumWidth(210)
        self._tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._tree.customContextMenuRequested.connect(self._show_context_menu)
        self._tree.itemClicked.connect(self._on_tree_item_clicked)
        self._tree.itemSelectionChanged.connect(self._update_action_state)
        splitter.addWidget(self._tree)

        editor_split = QSplitter(Qt.Orientation.Horizontal)
        editor_split.setChildrenCollapsible(False)
        self._editor = QTextEdit()
        self._editor.setObjectName("noteEditor")
        self._editor.setAcceptRichText(False)
        self._editor.setPlaceholderText("单击左侧的 Markdown 文件即可进入编辑。\n\n支持 Ctrl+S 保存；右侧会实时预览。")
        self._editor.setFontFamily("Cascadia Mono")
        self._editor.textChanged.connect(self._on_text_changed)
        self._preview = QTextBrowser()
        self._preview.setObjectName("notePreview")
        self._preview.setOpenExternalLinks(True)
        editor_split.addWidget(self._editor)
        editor_split.addWidget(self._preview)
        editor_split.setSizes([540, 620])
        splitter.addWidget(editor_split)
        splitter.setSizes([260, 980])
        layout.addWidget(splitter, stretch=1)

        self._save_shortcut = QShortcut(QKeySequence.StandardKey.Save, self)
        self._save_shortcut.activated.connect(self.save_current)
        self._update_action_state()
        self._render_preview()

    def _show_context_menu(self, position) -> None:
        item = self._tree.itemAt(position)
        if item is None:
            self._tree.setCurrentItem(None)
            kind = "empty"
            path = self._root
        else:
            self._tree.setCurrentItem(item)
            kind = str(item.data(0, Qt.ItemDataRole.UserRole + 1) or "")
            path = str(item.data(0, Qt.ItemDataRole.UserRole) or "")

        menu = QMenu(self)
        if kind == "file":
            menu.addAction("编辑", lambda: self._open_path(path))
            save_action = menu.addAction("保存", self.save_current)
            save_action.setEnabled(path == self._current_path)
            menu.addAction("重命名", self._rename_selected)
            menu.addSeparator()
            menu.addAction("导出 Markdown", lambda: self._export_file(path))
            menu.addAction("删除", self._delete_selected)
        else:
            menu.addAction("新建 Markdown", self._new_file)
            menu.addAction("新建文件夹", self._new_folder)
            if kind == "folder" and item is not None and item.parent() is not None:
                menu.addAction("重命名文件夹", self._rename_selected)
                menu.addAction("删除文件夹", self._delete_selected)
            menu.addSeparator()
            menu.addAction("导出文件夹", self._export_folder)

        menu.exec(self._tree.viewport().mapToGlobal(position))

    @property
    def extension(self) -> str:
        return ".md.enc" if self._enc_key else ".md"

    def _relative_display(self, path: str) -> str:
        display = os.path.relpath(path, self._root)
        return display[:-4] if display.endswith(".md.enc") else display

    def refresh_tree(self) -> None:
        expanded = self._expanded_folder_paths()
        self._tree.clear()
        root_item = QTreeWidgetItem(["📚 笔记"])
        root_item.setData(0, Qt.ItemDataRole.UserRole, self._root)
        root_item.setData(0, Qt.ItemDataRole.UserRole + 1, "folder")
        self._tree.addTopLevelItem(root_item)
        self._populate(root_item, self._root)
        self._restore_expanded_folders(root_item, expanded | {self._root})
        if self._current_path:
            current_item = self._find_item_by_path(root_item, self._current_path)
            if current_item is not None:
                self._tree.setCurrentItem(current_item)
        self._apply_filter(self._search.text())
        self._update_action_state()

    def _populate(self, parent: QTreeWidgetItem, directory: str) -> None:
        try:
            names = sorted(
                os.listdir(directory),
                key=lambda value: (not os.path.isdir(os.path.join(directory, value)), value.lower()),
            )
        except OSError:
            return
        for name in names:
            path = os.path.join(directory, name)
            if os.path.isdir(path):
                item = QTreeWidgetItem([f"📁 {name}"])
                item.setData(0, Qt.ItemDataRole.UserRole + 1, "folder")
                item.setData(0, Qt.ItemDataRole.UserRole, path)
                parent.addChild(item)
                self._populate(item, path)
            elif name.endswith(self.extension):
                display = name[:-7] + ".md" if name.endswith(".md.enc") else name
                item = QTreeWidgetItem([f"📝 {display}"])
                item.setData(0, Qt.ItemDataRole.UserRole + 1, "file")
                item.setData(0, Qt.ItemDataRole.UserRole, path)
                parent.addChild(item)

    def _expanded_folder_paths(self) -> set[str]:
        paths: set[str] = set()
        for item in self._iter_items():
            if (
                item.isExpanded()
                and item.data(0, Qt.ItemDataRole.UserRole + 1) == "folder"
            ):
                paths.add(str(item.data(0, Qt.ItemDataRole.UserRole) or ""))
        return paths

    def _restore_expanded_folders(self, item: QTreeWidgetItem, paths: set[str]) -> None:
        path = str(item.data(0, Qt.ItemDataRole.UserRole) or "")
        item.setExpanded(path in paths)
        for index in range(item.childCount()):
            self._restore_expanded_folders(item.child(index), paths)

    def _iter_items(self):
        root = self._tree.invisibleRootItem()
        stack = [root.child(index) for index in range(root.childCount())]
        while stack:
            item = stack.pop()
            yield item
            stack.extend(item.child(index) for index in range(item.childCount()))

    def _find_item_by_path(self, parent: QTreeWidgetItem, path: str) -> QTreeWidgetItem | None:
        if str(parent.data(0, Qt.ItemDataRole.UserRole) or "") == path:
            return parent
        for index in range(parent.childCount()):
            found = self._find_item_by_path(parent.child(index), path)
            if found is not None:
                return found
        return None

    def _apply_filter(self, query: str) -> None:
        needle = query.strip().casefold()
        root = self._tree.topLevelItem(0)
        if root is None:
            return
        if not needle:
            for item in self._iter_items():
                item.setHidden(False)
            root.setHidden(False)
            return

        def filter_item(item: QTreeWidgetItem) -> bool:
            own_match = needle in item.text(0).casefold()
            child_match = False
            for index in range(item.childCount()):
                child_match = filter_item(item.child(index)) or child_match
            visible = own_match or child_match
            item.setHidden(not visible)
            if child_match:
                item.setExpanded(True)
            return visible

        filter_item(root)
        root.setHidden(False)
        root.setExpanded(True)

    def _on_tree_item_clicked(self, item: QTreeWidgetItem, _column: int) -> None:
        if item.data(0, Qt.ItemDataRole.UserRole + 1) == "file":
            self._open_path(str(item.data(0, Qt.ItemDataRole.UserRole) or ""))

    def _selected_folder(self) -> str:
        item = self._tree.currentItem()
        if item is None:
            return self._root
        path = str(item.data(0, Qt.ItemDataRole.UserRole) or self._root)
        kind = item.data(0, Qt.ItemDataRole.UserRole + 1)
        return path if kind == "folder" else os.path.dirname(path)

    def _new_folder(self) -> None:
        name, ok = QInputDialog.getText(self, "新建文件夹", "文件夹名称：")
        if not ok or not name.strip():
            return
        try:
            path = self._safe_child(self._selected_folder(), name.strip())
            os.makedirs(path, exist_ok=False)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "无法创建文件夹", str(exc))
            return
        self.refresh_tree()

    def _new_file(self) -> None:
        name, ok = QInputDialog.getText(self, "新建 Markdown", "笔记名称：")
        if not ok or not name.strip():
            return
        name = name.strip()
        if name.lower().endswith(".md"):
            name = name[:-3]
        try:
            path = self._safe_child(self._selected_folder(), name + self.extension)
        except ValueError as exc:
            QMessageBox.warning(self, "无法创建笔记", str(exc))
            return
        if os.path.exists(path):
            QMessageBox.warning(self, "文件已存在", "同名 Markdown 笔记已经存在。")
            return
        self._write(path, f"# {name}\n\n")
        self._current_path = path
        self._dirty = False
        self.refresh_tree()
        self._load_path(path)
        self._editor.setFocus()

    def _open_path(self, path: str) -> None:
        if not path or path == self._current_path:
            self._editor.setFocus()
            return
        if self._dirty and not self._confirm_discard():
            return
        self._load_path(path)
        self._editor.setFocus()

    def _load_path(self, path: str) -> None:
        try:
            content = self._read(path)
        except OSError as exc:
            QMessageBox.warning(self, "无法打开笔记", str(exc))
            return
        self._current_path = path
        self._editor.blockSignals(True)
        self._editor.setPlainText(content)
        self._editor.blockSignals(False)
        self._dirty = False
        self._set_status(f"正在编辑 · {self._relative_display(path)}")
        self._render_preview()
        self.refresh_tree()

    def _on_text_changed(self) -> None:
        if not self._current_path:
            return
        self._dirty = True
        self._set_status(f"● 未保存 · {self._relative_display(self._current_path)}")
        self._update_action_state()
        self._preview_timer.start(180)

    def _render_preview(self) -> None:
        raw = self._editor.toPlainText()
        body = markdown.markdown(
            raw,
            extensions=["fenced_code", "tables", "nl2br", "sane_lists"],
        )
        if not body:
            body = "<p class='empty-note'>从左侧选择一篇笔记，或新建一篇开始写作。</p>"
        self._preview.setHtml(
            f"<html><head><meta charset='utf-8'><style>{NOTE_PREVIEW_CSS}</style></head>"
            f"<body><main class='markdown-body'>{body}</main></body></html>"
        )

    def save_current(self) -> bool:
        if not self._current_path:
            return False
        try:
            self._write(self._current_path, self._editor.toPlainText())
        except OSError as exc:
            QMessageBox.warning(self, "保存失败", str(exc))
            return False
        self._dirty = False
        self._set_status(f"已保存 · {self._relative_display(self._current_path)}")
        self._update_action_state()
        return True

    def _rename_selected(self) -> None:
        item = self._tree.currentItem()
        if item is None or item.parent() is None:
            return
        path = str(item.data(0, Qt.ItemDataRole.UserRole) or "")
        kind = str(item.data(0, Qt.ItemDataRole.UserRole + 1) or "")
        if not path or kind not in {"file", "folder"}:
            return
        visible_name = self._relative_display(path) if kind == "file" else os.path.basename(path)
        visible_name = os.path.basename(visible_name)
        name, ok = QInputDialog.getText(self, "重命名", "新名称：", text=visible_name)
        if not ok or not name.strip():
            return
        name = name.strip()
        if kind == "file":
            if name.lower().endswith(".md"):
                name = name[:-3]
            name += self.extension
        try:
            target = self._safe_child(os.path.dirname(path), name)
        except ValueError as exc:
            QMessageBox.warning(self, "无法重命名", str(exc))
            return
        if target == path:
            return
        if os.path.exists(target):
            QMessageBox.warning(self, "名称已存在", "目标位置已有同名文件或文件夹。")
            return
        try:
            os.replace(path, target)
        except OSError as exc:
            QMessageBox.warning(self, "重命名失败", str(exc))
            return
        if self._current_path == path:
            self._current_path = target
        elif kind == "folder" and self._current_path.startswith(path + os.sep):
            self._current_path = target + self._current_path[len(path):]
        self.refresh_tree()
        if self._current_path:
            self._set_status(
                f"{'● 未保存' if self._dirty else '正在编辑'} · {self._relative_display(self._current_path)}"
            )

    def _delete_selected(self) -> None:
        item = self._tree.currentItem()
        if item is None or item.parent() is None:
            return
        path = str(item.data(0, Qt.ItemDataRole.UserRole) or "")
        if not path:
            return
        deleting_current = self._current_path == path or self._current_path.startswith(path + os.sep)
        if deleting_current and self._dirty and not self._confirm_discard():
            return
        if QMessageBox.question(self, "确认删除", f"确定删除「{item.text(0)}」吗？") != QMessageBox.StandardButton.Yes:
            return
        try:
            if os.path.isdir(path):
                shutil.rmtree(path)
            elif os.path.exists(path):
                os.remove(path)
        except OSError as exc:
            QMessageBox.warning(self, "删除失败", str(exc))
            return
        if deleting_current:
            self._current_path = ""
            self._dirty = False
            self._editor.blockSignals(True)
            self._editor.clear()
            self._editor.blockSignals(False)
            self._set_status("选择一篇笔记开始编辑")
            self._render_preview()
        self.refresh_tree()

    def _export_file(self, path: str | None = None) -> None:
        path = path or self._current_path
        if not path:
            QMessageBox.information(self, "未选择文件", "请先选择一个 Markdown 笔记。")
            return
        target, _ = QFileDialog.getSaveFileName(
            self,
            "导出 Markdown",
            self._relative_display(path),
            "Markdown (*.md)",
        )
        if not target:
            return
        try:
            with open(target, "w", encoding="utf-8") as handle:
                use_editor = path == self._current_path and self._dirty
                handle.write(self._editor.toPlainText() if use_editor else self._read(path))
        except OSError as exc:
            QMessageBox.warning(self, "导出失败", str(exc))

    def _export_folder(self) -> None:
        source = self._selected_folder()
        target = QFileDialog.getExistingDirectory(self, "选择导出目录")
        if not target:
            return
        destination = os.path.join(target, os.path.basename(source.rstrip(os.sep)) or "markdown_notes")
        try:
            os.makedirs(destination, exist_ok=True)
            for root, _dirs, files in os.walk(source):
                relative = os.path.relpath(root, source)
                out_dir = destination if relative == "." else os.path.join(destination, relative)
                os.makedirs(out_dir, exist_ok=True)
                for name in files:
                    if not name.endswith(self.extension):
                        continue
                    exported_name = name[:-4] if name.endswith(".enc") else name
                    with open(os.path.join(out_dir, exported_name), "w", encoding="utf-8") as handle:
                        handle.write(self._read(os.path.join(root, name)))
        except OSError as exc:
            QMessageBox.warning(self, "导出失败", str(exc))
            return
        QMessageBox.information(self, "导出完成", destination)

    def _set_status(self, text: str) -> None:
        self._status.setText(text)

    def _update_action_state(self) -> None:
        has_current = bool(self._current_path)
        item = self._tree.currentItem() if hasattr(self, "_tree") else None
        can_rename = item is not None and item.parent() is not None
        self._save_button.setEnabled(has_current)
        self._export_button.setEnabled(has_current)
        self._rename_button.setEnabled(can_rename)

    def _read(self, path: str) -> str:
        if self._enc_key:
            return self._auth.decrypt_text(self._enc_key, path) or ""
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read()

    def _write(self, path: str, text: str) -> None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if self._enc_key:
            self._auth.encrypt_text(self._enc_key, path, text)
        else:
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(text)

    def can_leave(self) -> bool:
        return not self._dirty or self._confirm_discard()

    def _confirm_discard(self) -> bool:
        result = QMessageBox.question(
            self,
            "笔记尚未保存",
            "当前 Markdown 笔记有未保存的修改，是否保存？",
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
        )
        if result == QMessageBox.StandardButton.Save:
            return self.save_current()
        return result == QMessageBox.StandardButton.Discard

    def _safe_child(self, parent: str, name: str) -> str:
        if not name or any(char in name for char in '<>:"/\\|?*') or name in {".", ".."}:
            raise ValueError("名称包含 Windows 不允许使用的字符。")
        path = os.path.abspath(os.path.join(parent, name))
        root = os.path.abspath(self._root)
        if os.path.commonpath([root, path]) != root:
            raise ValueError("目标路径超出 Markdown 笔记目录。")
        return path

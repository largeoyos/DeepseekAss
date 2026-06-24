from __future__ import annotations

import os
import shutil

import markdown
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
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


class MarkdownWorkspaceWidget(QWidget):
    """Encrypted per-user Markdown notes with folders, preview, and export."""

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
        layout = QVBoxLayout(self)
        toolbar = QHBoxLayout()
        self._status = QLabel("Markdown ?? ? ????????????")
        toolbar.addWidget(self._status, stretch=1)
        for text, handler in (
            ("?????", self._new_folder),
            ("?? Markdown", self._new_file),
            ("??", self.save_current),
            ("??", self._delete_selected),
            ("????", self._export_file),
            ("?????", self._export_folder),
        ):
            button = QPushButton(text)
            button.clicked.connect(handler)
            toolbar.addWidget(button)
        layout.addLayout(toolbar)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self._tree = QTreeWidget()
        self._tree.setHeaderLabel("???? Markdown")
        self._tree.itemSelectionChanged.connect(self._open_selected)
        splitter.addWidget(self._tree)

        editor_split = QSplitter(Qt.Orientation.Horizontal)
        self._editor = QTextEdit()
        self._editor.setPlaceholderText("????? Markdown??")
        self._editor.textChanged.connect(self._on_text_changed)
        self._preview = QTextBrowser()
        self._preview.setOpenExternalLinks(True)
        editor_split.addWidget(self._editor)
        editor_split.addWidget(self._preview)
        editor_split.setSizes([520, 520])
        splitter.addWidget(editor_split)
        splitter.setSizes([260, 900])
        layout.addWidget(splitter, stretch=1)

    @property
    def extension(self) -> str:
        return ".md.enc" if self._enc_key else ".md"

    def refresh_tree(self) -> None:
        self._tree.clear()
        root_item = QTreeWidgetItem(["??"])
        root_item.setData(0, Qt.ItemDataRole.UserRole, self._root)
        root_item.setData(0, Qt.ItemDataRole.UserRole + 1, "folder")
        self._tree.addTopLevelItem(root_item)
        self._populate(root_item, self._root)
        root_item.setExpanded(True)

    def _populate(self, parent: QTreeWidgetItem, directory: str) -> None:
        try:
            names = sorted(os.listdir(directory), key=lambda value: (not os.path.isdir(os.path.join(directory, value)), value.lower()))
        except OSError:
            return
        for name in names:
            path = os.path.join(directory, name)
            if os.path.isdir(path):
                item = QTreeWidgetItem([name])
                item.setData(0, Qt.ItemDataRole.UserRole + 1, "folder")
                self._populate(item, path)
            elif name.endswith(self.extension):
                display = name[:-7] + ".md" if name.endswith(".md.enc") else name
                item = QTreeWidgetItem([display])
                item.setData(0, Qt.ItemDataRole.UserRole + 1, "file")
            else:
                continue
            item.setData(0, Qt.ItemDataRole.UserRole, path)
            parent.addChild(item)

    def _selected_folder(self) -> str:
        item = self._tree.currentItem()
        if item is None:
            return self._root
        path = str(item.data(0, Qt.ItemDataRole.UserRole) or self._root)
        kind = item.data(0, Qt.ItemDataRole.UserRole + 1)
        return path if kind == "folder" else os.path.dirname(path)

    def _new_folder(self) -> None:
        name, ok = QInputDialog.getText(self, "?????", "??????")
        if not ok or not name.strip():
            return
        path = self._safe_child(self._selected_folder(), name.strip())
        os.makedirs(path, exist_ok=False)
        self.refresh_tree()

    def _new_file(self) -> None:
        name, ok = QInputDialog.getText(self, "?? Markdown", "?????")
        if not ok or not name.strip():
            return
        name = name.strip()
        if name.lower().endswith(".md"):
            name = name[:-3]
        path = self._safe_child(self._selected_folder(), name + self.extension)
        if os.path.exists(path):
            QMessageBox.warning(self, "?????", "?? Markdown ???????")
            return
        self._write(path, f"# {name}\n\n")
        self.refresh_tree()
        self._load_path(path)

    def _open_selected(self) -> None:
        item = self._tree.currentItem()
        if item is None or item.data(0, Qt.ItemDataRole.UserRole + 1) != "file":
            return
        path = str(item.data(0, Qt.ItemDataRole.UserRole) or "")
        if path == self._current_path:
            return
        if self._dirty and not self._confirm_discard():
            return
        self._load_path(path)

    def _load_path(self, path: str) -> None:
        self._current_path = path
        self._editor.blockSignals(True)
        self._editor.setPlainText(self._read(path))
        self._editor.blockSignals(False)
        self._dirty = False
        self._status.setText(f"Markdown ?? ? {self._relative_display(path)}")
        self._render_preview()

    def _on_text_changed(self) -> None:
        self._dirty = bool(self._current_path)
        self._preview_timer.start(180)

    def _render_preview(self) -> None:
        body = markdown.markdown(
            self._editor.toPlainText(),
            extensions=["fenced_code", "tables", "nl2br", "sane_lists"],
        )
        self._preview.setHtml(f"<html><body style='font-family:Segoe UI,sans-serif;padding:18px'>{body}</body></html>")

    def save_current(self) -> bool:
        if not self._current_path:
            return False
        self._write(self._current_path, self._editor.toPlainText())
        self._dirty = False
        self._status.setText(f"??? ? {self._relative_display(self._current_path)}")
        return True

    def _delete_selected(self) -> None:
        item = self._tree.currentItem()
        if item is None or item.parent() is None:
            return
        path = str(item.data(0, Qt.ItemDataRole.UserRole) or "")
        if QMessageBox.question(self, "????", f"?????{item.text(0)}???") != QMessageBox.StandardButton.Yes:
            return
        if os.path.isdir(path):
            shutil.rmtree(path)
        elif os.path.exists(path):
            os.remove(path)
        if self._current_path == path or self._current_path.startswith(path + os.sep):
            self._current_path = ""
            self._editor.clear()
            self._preview.clear()
        self.refresh_tree()

    def _export_file(self) -> None:
        if not self._current_path:
            QMessageBox.information(self, "?????", "?????? Markdown ???")
            return
        target, _ = QFileDialog.getSaveFileName(self, "?? Markdown", self._relative_display(self._current_path), "Markdown (*.md)")
        if target:
            with open(target, "w", encoding="utf-8") as handle:
                handle.write(self._editor.toPlainText() if self._dirty else self._read(self._current_path))

    def _export_folder(self) -> None:
        source = self._selected_folder()
        target = QFileDialog.getExistingDirectory(self, "??????")
        if not target:
            return
        destination = os.path.join(target, os.path.basename(source.rstrip(os.sep)) or "markdown_notes")
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
        QMessageBox.information(self, "????", destination)

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
            "?????",
            "?? Markdown ?????????????",
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
        )
        if result == QMessageBox.StandardButton.Save:
            return self.save_current()
        return result == QMessageBox.StandardButton.Discard

    def _safe_child(self, parent: str, name: str) -> str:
        if any(char in name for char in '<>:"/\\|?*') or name in {".", ".."}:
            raise ValueError("???? Windows ??????")
        path = os.path.abspath(os.path.join(parent, name))
        root = os.path.abspath(self._root)
        if os.path.commonpath([root, path]) != root:
            raise ValueError("?????? Markdown ???")

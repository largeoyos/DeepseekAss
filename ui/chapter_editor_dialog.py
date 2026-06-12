import os

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
)

from core.novel_manager import NovelManager
from utils.supplement import count_cn


class ChapterEditorDialog(QDialog):
    """Edit a chapter node and save edits as a new version."""

    def __init__(self, parent, novel_manager: NovelManager, book_title: str, node: dict):
        super().__init__(parent)
        self._novel_manager = novel_manager
        self._book_title = book_title
        self._node = node
        self._dirty = False
        self.setWindowTitle(f"章节编辑 - 第{node.get('chapter_num')}章「{node.get('title', '')}」")
        self.resize(820, 620)
        self._init_ui()
        self._load_content()

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        self._info = QLabel("")
        layout.addWidget(self._info)

        self._editor = QTextEdit()
        self._editor.textChanged.connect(self._on_text_changed)
        layout.addWidget(self._editor, stretch=1)

        row = QHBoxLayout()
        self._draft_label = QLabel("")
        row.addWidget(self._draft_label, stretch=1)
        preview_btn = QPushButton("刷新字数")
        preview_btn.clicked.connect(self._refresh_count)
        save_btn = QPushButton("保存为新版本")
        save_btn.clicked.connect(self._save_new_version)
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.accept)
        row.addWidget(preview_btn)
        row.addWidget(save_btn)
        row.addWidget(close_btn)
        layout.addLayout(row)

        self._autosave_timer = QTimer(self)
        self._autosave_timer.setInterval(30_000)
        self._autosave_timer.timeout.connect(self._autosave_draft)

    def _load_content(self) -> None:
        content = self._novel_manager.read_chapter_node(self._book_title, self._node["id"]) or ""
        self._editor.setPlainText(content)
        self._dirty = False
        self._refresh_count()

    def _refresh_count(self) -> None:
        chars = count_cn(self._editor.toPlainText())
        self._info.setText(
            f"节点: {self._node.get('id')} | 版本: v{self._node.get('version')} | 中文字数: {chars}"
        )

    def _on_text_changed(self) -> None:
        self._dirty = True
        self._refresh_count()
        self._autosave_timer.start()

    def _draft_path(self) -> str:
        book_dir = self._novel_manager._book_dir(self._book_title)  # internal path reuse for user-local draft
        draft_dir = os.path.join(book_dir, ".drafts")
        os.makedirs(draft_dir, exist_ok=True)
        return os.path.join(draft_dir, f"{self._node['id']}.draft.txt")

    def _autosave_draft(self) -> None:
        if not self._dirty:
            return
        try:
            self._novel_manager._write_encrypted_text(self._draft_path(), self._editor.toPlainText())
            self._draft_label.setText("草稿已自动保存")
            self._dirty = False
        except Exception as exc:
            self._draft_label.setText(f"草稿保存失败: {exc}")

    def _save_new_version(self) -> None:
        content = self._editor.toPlainText()
        chapter_num = int(self._node["chapter_num"])
        title = self._node.get("title") or f"第{chapter_num}章"
        version = self._novel_manager.get_next_version(self._book_title, chapter_num)
        path, saved_version = self._novel_manager.save_chapter_version(
            self._book_title, chapter_num, title, content, version=version
        )
        self._novel_manager.set_active_version(self._book_title, chapter_num, saved_version)
        QMessageBox.information(self, "保存完成", f"已保存为 v{saved_version}：\n{path}")
        self.accept()

"""Application diagnostics dialog."""
from __future__ import annotations

from PyQt6.QtWidgets import QDialog, QDialogButtonBox, QPlainTextEdit, QVBoxLayout


class DiagnosticsDialog(QDialog):
    def __init__(self, parent, *, novel_manager, task_runner, settings: dict, current_book: str = "") -> None:
        super().__init__(parent)
        self.setWindowTitle("诊断中心")
        self.resize(860, 560)
        layout = QVBoxLayout(self)
        text = QPlainTextEdit()
        text.setReadOnly(True)
        text.setPlainText(self._build_report(novel_manager, task_runner, settings, current_book))
        layout.addWidget(text, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _build_report(self, novel_manager, task_runner, settings: dict, current_book: str) -> str:
        lines = ["DeepseekAss Diagnostics", ""]
        books = novel_manager.list_books()
        lines.append(f"Books: {len(books)}")
        lines.append(f"Current book: {current_book or '-'}")
        lines.append(f"Active tasks: {len(task_runner.active())}")
        failed = [item for item in task_runner.history(limit=50) if item.status == "failed"]
        lines.append(f"Recent failed tasks: {len(failed)}")
        lines.append(f"Generation mode: {settings.get('novel_generation_mode', 'classic')}")
        lines.append(f"Retrieval backend: {settings.get('retrieval_backend', 'classic')}")
        lines.append(f"Agent runtime: {settings.get('agent_runtime_backend', 'legacy')}")
        if current_book:
            try:
                chapters = novel_manager.list_chapters(current_book)
                lines.append(f"Current book chapters: {len(chapters)}")
                load_error = novel_manager.world_bible_load_error(current_book)
                lines.append(f"World bible load state: {'error - ' + load_error if load_error else 'ok'}")
            except Exception as exc:
                lines.append(f"Book diagnostics failed: {exc}")
        lines.extend(["", "Verification command:", "python -m unittest discover -s tests"])
        return "\n".join(lines)

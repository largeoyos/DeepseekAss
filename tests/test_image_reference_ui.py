import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QEventLoop, QTimer
from PyQt6.QtWidgets import QApplication
from ui.image_reference_dialog import ImageReferenceDialog


class ImageReferenceUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_result_is_editable_only_after_worker_finishes(self):
        dialog = ImageReferenceDialog(None)
        dialog.paths = ["scene.png"]
        loop = QEventLoop()
        with patch("ui.image_reference_dialog.ImageReferenceService.describe", return_value={"content": "石砌钟楼", "source": "current_model", "model": "vision"}):
            dialog._read()
            self.assertFalse(dialog.insert.isEnabled())
            dialog.worker.finished.connect(loop.quit)
            QTimer.singleShot(3000, loop.quit)
            loop.exec()
            dialog.worker.wait(3000)
            self.app.processEvents()
        self.assertEqual("石砌钟楼", dialog.content.toPlainText())
        self.assertTrue(dialog.insert.isEnabled())
        self.assertTrue(dialog.read.isEnabled())
        dialog.content.setPlainText("旧钟楼")
        dialog.insert.click()
        self.assertEqual(dialog.DialogCode.Accepted, dialog.result())

    def test_error_keeps_reference_empty_and_allows_retry(self):
        dialog = ImageReferenceDialog(None)
        dialog.paths = ["scene.png"]
        loop = QEventLoop()
        with patch("ui.image_reference_dialog.ImageReferenceService.describe", side_effect=ValueError("模型不支持看图")):
            dialog._read()
            dialog.worker.finished.connect(loop.quit)
            QTimer.singleShot(3000, loop.quit)
            loop.exec()
            dialog.worker.wait(3000)
            self.app.processEvents()
        self.assertFalse(dialog.insert.isEnabled())
        self.assertTrue(dialog.read.isEnabled())
        self.assertEqual("模型不支持看图", dialog.status.text())
        dialog.close()

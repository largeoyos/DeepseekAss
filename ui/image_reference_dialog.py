"""Review image descriptions before inserting them into a writing request."""
from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtWidgets import QDialog, QFileDialog, QLabel, QLineEdit, QPushButton, QTextEdit, QVBoxLayout

from core.image_reference import ImageReferenceService


class _Reader(QThread):
    result = pyqtSignal(object)
    error = pyqtSignal(str)

    def __init__(self, gateway, paths, instruction, parent):
        super().__init__(parent)
        self.gateway, self.paths, self.instruction = gateway, paths, instruction

    def run(self):
        try:
            self.result.emit(ImageReferenceService(self.gateway).describe(self.paths, self.instruction))
        except Exception as exc:
            self.error.emit(str(exc))


class ImageReferenceDialog(QDialog):
    def __init__(self, gateway, parent=None):
        super().__init__(parent)
        self.gateway, self.paths, self.worker = gateway, [], None
        self.setWindowTitle("图片写作参考")
        self.resize(640, 500)
        layout = QVBoxLayout(self)
        info = QLabel("当前互动模型支持看图时直接识别，否则使用图片识别备用 API。识别时会将图片发送给该服务；结果可编辑后加入写作输入。")
        info.setWordWrap(True)
        layout.addWidget(info)
        self.choose = QPushButton("选择图片（最多 4 张）")
        self.choose.clicked.connect(self._choose)
        layout.addWidget(self.choose)
        self.files = QLabel("尚未选择图片")
        self.files.setWordWrap(True)
        layout.addWidget(self.files)
        self.instruction = QLineEdit()
        self.instruction.setPlaceholderText("例如：描述服饰、建筑和光线，作为本章场景参考")
        layout.addWidget(self.instruction)
        self.read = QPushButton("识别图片")
        self.read.clicked.connect(self._read)
        layout.addWidget(self.read)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.content = QTextEdit()
        layout.addWidget(self.content)
        self.insert = QPushButton("加入当前输入")
        self.insert.setEnabled(False)
        self.insert.clicked.connect(self.accept)
        layout.addWidget(self.insert)
        close = QPushButton("关闭")
        close.clicked.connect(self.reject)
        layout.addWidget(close)

    def _choose(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "选择图片", "", "图片 (*.png *.jpg *.jpeg *.webp *.gif)")
        if paths:
            self.paths = paths
            self.files.setText("\n".join(paths))
            self.content.clear()
            self.insert.setEnabled(False)

    def _read(self):
        if not self.paths or len(self.paths) > 4:
            self.status.setText("请选择 1 到 4 张图片。")
            return
        self.choose.setEnabled(False)
        self.read.setEnabled(False)
        self.insert.setEnabled(False)
        self.content.clear()
        self.status.setText("正在识别图片……")
        self.worker = _Reader(self.gateway, list(self.paths), self.instruction.text(), self)
        self.worker.result.connect(self._result)
        self.worker.error.connect(self.status.setText)
        self.worker.finished.connect(self._finished)
        self.worker.start()

    def _result(self, result):
        self.content.setPlainText(result["content"])
        source = "当前模型" if result["source"] == "current_model" else "备用视觉 API"
        self.status.setText(f"识别完成：{source} / {result['model']}。请检查内容后再加入。")

    def _finished(self):
        self.choose.setEnabled(True)
        self.read.setEnabled(True)
        self.insert.setEnabled(bool(self.content.toPlainText().strip()))

    def reject(self):
        if self.worker and self.worker.isRunning():
            self.status.setText("图片识别尚在进行，请等待请求结束后关闭。")
            return
        super().reject()

    def closeEvent(self, event):
        if self.worker and self.worker.isRunning():
            event.ignore()
            self.reject()
        else:
            super().closeEvent(event)

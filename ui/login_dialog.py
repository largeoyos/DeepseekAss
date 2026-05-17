"""
登录/注册对话框
启动时弹出，用户通过后方能进入主界面
"""
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QHBoxLayout,
    QMessageBox,
)

from core.auth_manager import AuthManager, AuthError


class LoginDialog(QDialog):
    """登录/注册对话框"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.username = ""
        self.password = ""
        self.enc_key: bytes | None = None
        self._auth = AuthManager()

        self.setWindowTitle("DeepSeekAss - 登录")
        self.setFixedSize(380, 300)
        self.setModal(True)
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        title = QLabel("DeepSeekAss 用户登录")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("font-size: 18px; font-weight: bold; color: #569cd6; padding: 10px;")
        layout.addWidget(title)

        layout.addWidget(QLabel("用户名:"))
        self._username_input = QLineEdit()
        self._username_input.setPlaceholderText("输入用户名")
        layout.addWidget(self._username_input)

        layout.addWidget(QLabel("密码:"))
        self._password_input = QLineEdit()
        self._password_input.setPlaceholderText("输入密码")
        self._password_input.setEchoMode(QLineEdit.EchoMode.Password)
        layout.addWidget(self._password_input)

        # 确认密码（默认隐藏，注册模式显示）
        self._confirm_label = QLabel("确认密码:")
        self._confirm_label.setVisible(False)
        layout.addWidget(self._confirm_label)

        self._confirm_input = QLineEdit()
        self._confirm_input.setPlaceholderText("再次输入密码")
        self._confirm_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._confirm_input.setVisible(False)
        layout.addWidget(self._confirm_input)

        # 登录按钮
        btn_layout = QHBoxLayout()
        self._login_btn = QPushButton("登 录")
        btn_layout.addWidget(self._login_btn)
        layout.addLayout(btn_layout)

        # 注册按钮（独立一行，仅在注册模式显示）
        self._register_btn = QPushButton("注 册")
        self._register_btn.setVisible(False)
        layout.addWidget(self._register_btn)

        self._switch_btn = QPushButton("没有账号？去注册")
        self._switch_btn.setStyleSheet(
            "QPushButton { color: #569cd6; border: none; }"
            "QPushButton:hover { color: #7fb8e8; }"
        )
        layout.addWidget(self._switch_btn)

        # 信号
        self._login_btn.clicked.connect(self._on_login)
        self._register_btn.clicked.connect(self._on_register)
        self._switch_btn.clicked.connect(self._toggle_mode)
        self._username_input.returnPressed.connect(self._password_input.setFocus)
        self._password_input.returnPressed.connect(self._on_login)
        self._confirm_input.returnPressed.connect(self._on_register)

        self._register_mode = False
        layout.addStretch()

    def _toggle_mode(self):
        self._register_mode = not self._register_mode
        if self._register_mode:
            self.setWindowTitle("DeepSeekAss - 注册")
            self._login_btn.setVisible(False)
            self._register_btn.setVisible(True)
            self._switch_btn.setText("已有账号？去登录")
            self._confirm_label.setVisible(True)
            self._confirm_input.setVisible(True)
        else:
            self.setWindowTitle("DeepSeekAss - 登录")
            self._login_btn.setVisible(True)
            self._register_btn.setVisible(False)
            self._switch_btn.setText("没有账号？去注册")
            self._confirm_label.setVisible(False)
            self._confirm_input.setVisible(False)

    def _on_login(self):
        username = self._username_input.text().strip()
        password = self._password_input.text()
        if not username or not password:
            QMessageBox.warning(self, "提示", "请输入用户名和密码")
            return

        success, enc_key = self._auth.authenticate(username, password)
        if success:
            self.username = username
            self.password = password
            self.enc_key = enc_key
            self.accept()
        else:
            QMessageBox.critical(self, "登录失败", "用户名或密码错误")

    def _on_register(self):
        username = self._username_input.text().strip()
        password = self._password_input.text()
        confirm = self._confirm_input.text()

        if not username:
            QMessageBox.warning(self, "提示", "用户名不能为空")
            return
        if not password:
            QMessageBox.warning(self, "提示", "密码不能为空")
            return
        if password != confirm:
            QMessageBox.warning(self, "提示", "两次密码输入不一致")
            return

        try:
            enc_key = self._auth.register(username, password)
            self.username = username
            self.password = password
            self.enc_key = enc_key
            QMessageBox.information(
                self, "注册成功",
                f"用户 '{username}' 注册成功！\n\n"
                "⚠️ 请牢记您的密码。\n密码丢失后将无法恢复数据。"
            )
            self.accept()
        except AuthError as e:
            QMessageBox.warning(self, "注册失败", str(e))

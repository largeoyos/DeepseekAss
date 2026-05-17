"""
PyQt6 图形界面主窗口模块
- 启动时要求输入 API Key
- 实时 Markdown 渲染（通过 QWebEngineView）
- 模式切换、模型切换、温度/ top_p/ max_tokens/ frequency_penalty 调节
- 流式输出对话
- 小说写作模式：书架管理、章节控制、参数设定、自动摘要
"""

import json
import os
import re
import sys
import threading

from PyQt6.QtCore import Qt, pyqtSignal, QObject
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QSplitter,
    QPushButton,
    QTextEdit,
    QComboBox,
    QLabel,
    QScrollArea,
    QSlider,
    QInputDialog,
    QMessageBox,
    QGroupBox,
    QStackedWidget,
    QSpinBox,
    QLineEdit,
    QCheckBox,
    QFrame,
    QRadioButton,
    QButtonGroup,
    QFileDialog,
)
from PyQt6.QtGui import QColor, QIcon
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebEngineCore import QWebEngineSettings
import markdown as md_lib

from config import Config
from core.chat_client import DeepSeekChatClient
from core.novel_manager import NovelManager
from core.conversation_manager import ConversationManager
from strategies import (
    RolePlayStrategy,
    NovelStrategy,
    ContinuationStrategy,
)
from utils.prompts import Prompts
from utils.export import (
    export_chapter,
    export_book,
    export_conversation,
    EXPORT_FORMATS,
    FORMAT_LABELS,
)
from ui.world_bible_dialog import WorldBibleDialog
from ui.presets import PRESETS, CUSTOM_LABEL, COMBO_ITEMS
from ui.continuation_dialogs import (
    analyze_source_text, suggest_directions,
    ContinuationAnalysisDialog, DirectionSelectionDialog,
    SectionPreviewDialog,
)


# 用户全局提示词持久化文件
_USER_PREFS_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "user_prefs.json")


# ========== 自定义输入框（拦截 Ctrl+Enter） ==========

class InputTextEdit(QTextEdit):
    """重写 keyPressEvent，确保 Ctrl+Enter/Ctrl+Return 触发发送"""

    send_requested = pyqtSignal()

    def keyPressEvent(self, event):
        # Ctrl+Enter 或 Ctrl+Return → 发送信号
        if event.modifiers() == Qt.KeyboardModifier.ControlModifier and event.key() in (Qt.Key.Key_Enter, Qt.Key.Key_Return):
            self.send_requested.emit()
            return
        # 单独 Enter 保持默认行为（插入换行）
        super().keyPressEvent(event)


# ========== 信号中转 ==========

class StreamSignals(QObject):
    """用于跨线程安全地将流式 token 传递到主线程"""
    token = pyqtSignal(str)
    finished = pyqtSignal()
    error = pyqtSignal(str)
    analysis_done = pyqtSignal(str, str, str)     # setting, plot, source_text
    directions_ready = pyqtSignal(list, str, str, int)  # directions, setting, plot, word_count
    novel_imported = pyqtSignal(str)               # 从源文档导入小说完成，参数：标题
    refresh_chapter_info = pyqtSignal(str)          # 安全刷新章节信息，参数：书名


# ========== 模式配置 ==========

STRATEGY_OPTIONS = {
    "角色扮演": RolePlayStrategy,
    "小说写作": NovelStrategy,
    "续写小说": ContinuationStrategy,
}

MODEL_OPTIONS = [
    Config.MODEL_V4_FLASH,
    Config.MODEL_V4_PRO,
]


# ========== HTML / CSS 模板（深色主题） ==========

HTML_STYLE = """
<style>
  * {
    scrollbar-width: thin;
    scrollbar-color: #444 #1e1e1e;
  }
  ::-webkit-scrollbar { width: 8px; height: 8px; }
  ::-webkit-scrollbar-track { background: #1e1e1e; }
  ::-webkit-scrollbar-thumb { background: #444; border-radius: 4px; }
  ::-webkit-scrollbar-thumb:hover { background: #555; }

  body {
    font-family: "Microsoft YaHei", "Segoe UI", -apple-system, Arial, sans-serif;
    font-size: 14.5px;
    line-height: 1.8;
    color: #d4d4d4;
    background: linear-gradient(135deg, #1a1a2e 0%, #1e1e2e 50%, #1a1a2e 100%);
    padding: 20px;
    margin: 0;
  }

  /* 消息通用过渡动画 */
  .user-msg, .assistant-msg, .system-msg {
    animation: fadeIn 0.25s ease-out;
  }
  @keyframes fadeIn {
    from { opacity: 0; transform: translateY(6px); }
    to { opacity: 1; transform: translateY(0); }
  }

  /* 用户消息气泡 */
  .user-msg {
    background: linear-gradient(135deg, #1e3a5f 0%, #264f78 100%);
    border-radius: 12px 12px 4px 12px;
    padding: 12px 18px;
    margin: 10px 0 10px 20%;
    color: #cee4ff;
    border: 1px solid rgba(86, 156, 214, 0.25);
    box-shadow: 0 2px 8px rgba(0, 0, 0, 0.2);
    font-size: 14px;
    line-height: 1.7;
  }

  /* 助手消息 */
  .assistant-msg {
    margin: 10px 20% 10px 0;
    padding: 12px 18px;
    background: rgba(45, 45, 58, 0.8);
    border-radius: 12px 12px 12px 4px;
    border: 1px solid rgba(255, 255, 255, 0.06);
    box-shadow: 0 2px 8px rgba(0, 0, 0, 0.15);
    font-size: 14px;
    line-height: 1.8;
  }

  .system-msg {
    color: #6a9955;
    font-style: italic;
    margin: 10px auto;
    text-align: center;
    font-size: 13px;
    opacity: 0.85;
    padding: 6px 12px;
    background: rgba(106, 153, 85, 0.08);
    border-radius: 8px;
    max-width: 80%;
  }

  /* 代码块 */
  pre {
    background: #0d0d1a !important;
    border-radius: 8px;
    padding: 14px 18px;
    overflow-x: auto;
    font-family: "JetBrains Mono", "Consolas", "Courier New", monospace;
    font-size: 13px;
    line-height: 1.6;
    border: 1px solid rgba(255, 255, 255, 0.08);
    box-shadow: inset 0 1px 4px rgba(0, 0, 0, 0.3);
    margin: 12px 0;
  }
  code {
    background: rgba(86, 156, 214, 0.12);
    border-radius: 4px;
    padding: 2px 7px;
    font-family: "JetBrains Mono", "Consolas", "Courier New", monospace;
    font-size: 13px;
    color: #dcdcaa;
  }
  pre code {
    background: transparent;
    padding: 0;
    color: #d4d4d4;
    font-size: 13px;
  }

  /* 引用 */
  blockquote {
    border-left: 3px solid #569cd6;
    margin: 10px 0;
    padding: 8px 18px;
    color: #b0c4de;
    background: linear-gradient(90deg, rgba(86, 156, 214, 0.08) 0%, transparent 100%);
    border-radius: 0 6px 6px 0;
  }

  /* 表格 */
  table {
    border-collapse: collapse;
    margin: 14px 0;
    width: 100%;
    border-radius: 8px;
    overflow: hidden;
    box-shadow: 0 1px 6px rgba(0, 0, 0, 0.15);
  }
  th, td {
    border: 1px solid rgba(255, 255, 255, 0.08);
    padding: 10px 14px;
    text-align: left;
  }
  th {
    background: #0d0d1a;
    color: #569cd6;
    font-weight: 600;
    letter-spacing: 0.3px;
  }
  td { background: rgba(45, 45, 58, 0.4); }
  tr:nth-child(even) td { background: rgba(45, 45, 58, 0.2); }

  a {
    color: #569cd6;
    text-decoration: none;
    border-bottom: 1px solid transparent;
    transition: border-color 0.2s;
  }
  a:hover {
    border-bottom-color: #569cd6;
  }

  h1, h2, h3, h4, h5, h6 {
    color: #569cd6;
    margin-top: 1.3em;
    margin-bottom: 0.5em;
    font-weight: 600;
    letter-spacing: 0.3px;
  }
  h1 { font-size: 1.6em; border-bottom: 1px solid rgba(86, 156, 214, 0.2); padding-bottom: 8px; }
  h2 { font-size: 1.35em; }
  h3 { font-size: 1.2em; }

  hr {
    border: none;
    height: 1px;
    background: linear-gradient(90deg, transparent, rgba(86, 156, 214, 0.3), transparent);
    margin: 20px 0;
  }

  p { margin: 0.6em 0; }
  ul, ol { padding-left: 26px; }
  li { margin: 4px 0; }

  /* 图片 */
  img {
    max-width: 100%;
    border-radius: 8px;
    margin: 8px 0;
    box-shadow: 0 2px 8px rgba(0, 0, 0, 0.2);
  }
</style>
"""

# 初始页面模板
INITIAL_HTML = f"""
<html><head>{HTML_STYLE}</head><body>
<div style="text-align:center; padding: 40px 20px;">
  <div style="font-size: 48px; margin-bottom: 16px;">🚀</div>
  <h1 style="border:none; font-size: 1.8em;">DeepSeek 多功能聊天客户端</h1>
  <p style="color: #888; font-size: 14px; margin-bottom: 32px;">请在左侧面板选择模式和模型，然后开始对话</p>

  <div style="display:inline-block; text-align:left; max-width:480px; background:rgba(255,255,255,0.03); border-radius:12px; padding:24px 32px; border:1px solid rgba(255,255,255,0.06);">
    <h3 style="margin-top:0; font-size:15px;">当前可用模式</h3>
    <table style="box-shadow:none;">
      <tr><td style="border:none; padding:8px 0;"><strong>🎭 角色扮演</strong></td><td style="border:none; padding:8px 0; color:#999;">模拟特定人物/身份的对话风格</td></tr>
      <tr><td style="border:none; padding:8px 0;"><strong>📚 小说写作</strong></td><td style="border:none; padding:8px 0; color:#999;">创意写作、情节构思、文笔润色（支持书架管理 + 章节续写）</td></tr>
    </table>

    <h3 style="margin-top:20px; font-size:15px;">可用模型</h3>
    <table style="box-shadow:none;">
      <tr><td style="border:none; padding:6px 0;"><code>deepseek-v4-flash</code></td><td style="border:none; padding:6px 0; color:#999;">v4 闪电版</td></tr>
      <tr><td style="border:none; padding:6px 0;"><code>deepseek-v4-pro</code></td><td style="border:none; padding:6px 0; color:#999;">v4 专业版</td></tr>
    </table>

    <p style="color:#6a9955; font-size: 13px; margin-top: 24px; text-align:center; background:rgba(106,153,85,0.08); border-radius:6px; padding:8px;">若尚未配置 API Key，程序启动时会弹出输入框</p>
  </div>
</div>
</body></html>
"""


# ========== 工具函数 ==========

def md_to_html(text: str) -> str:
    """将 Markdown 文本转换为带样式的 HTML"""
    md_body = md_lib.markdown(
        text,
        extensions=[
            "fenced_code",
            "tables",
            "codehilite",
            "nl2br",
            "sane_lists",
        ],
    )
    return f"<html><head>{HTML_STYLE}</head><body>{md_body}</body></html>"


# ========== 主窗口 ==========

class DeepSeekChatGUI(QMainWindow):
    """DeepSeek 聊天客户端主窗口"""

    def __init__(self) -> None:
        super().__init__()
        self._client: DeepSeekChatClient | None = None
        self._stream_signals = StreamSignals()
        self._stream_signals.token.connect(self._on_stream_token)
        self._stream_signals.finished.connect(self._on_stream_finished)
        self._stream_signals.error.connect(self._on_stream_error)
        self._stream_signals.analysis_done.connect(self._show_analysis_dialog)
        self._stream_signals.directions_ready.connect(self._show_direction_selector)
        self._stream_signals.novel_imported.connect(self._on_novel_imported)
        self._stream_signals.novel_imported.connect(self._on_cont_novel_imported)
        self._stream_signals.refresh_chapter_info.connect(self._refresh_chapter_info_display)

        # 小说管理器
        self._novel_manager = NovelManager()
        # 对话历史管理器
        self._conversation_manager = ConversationManager()
        self._current_conversation_id: str | None = None
        self._current_conversation_title: str = ""

        # 累积的文本（用于流式追加）
        self._assistant_text_buffer: list[str] = []
        self._streaming = False
        # 正在加载对话（阻止模式切换时覆盖显示）
        self._loading_conversation = False
        # 参数预设守卫：预设驱动滑块时阻止 handler 切回"自定义"
        self._preset_applying = False

        # 获取并验证 API Key（失败可重试）
        api_key = self._get_api_key_with_retry()
        if not api_key:
            sys.exit(0)

        Config.API_KEY = api_key

        self._init_client()
        # 加载用户全局提示词
        loaded_prompt = self._load_global_user_prompt()
        if loaded_prompt:
            self._client.global_user_prompt = loaded_prompt

        self._init_ui()
        # 默认预设方案：狂野
        self._preset_combo.setCurrentText("狂野")
        self._apply_dark_theme()
        self._refresh_novel_bookshelf()

    # ========== API Key ==========

    def _get_api_key_with_retry(self) -> str | None:
        """获取并验证 API Key，失败则弹窗重试"""
        # 先从 .env 加载的 key 开始
        api_key = Config.API_KEY

        while True:
            # 无 key 或占位符 → 弹窗索取
            if not api_key or api_key == "your_deepseek_api_key_here":
                api_key = self._request_api_key()
                if not api_key:  # 用户取消
                    return None

            # 验证 key
            if self._verify_api_key(api_key):
                return api_key

            # 验证失败 → 弹窗报错 + 重新索取
            QMessageBox.critical(
                None, "API Key 无效",
                "API Key 验证失败，请检查后重新输入。\n"
                "常见问题：\n"
                "  - Key 已过期或未生效\n"
                "  - 网络连接异常\n"
                "  - Base URL 配置错误"
            )
            api_key = ""  # 强制下次循环弹窗

        return None  # unreachable

    def _request_api_key(self) -> str | None:
        """弹出对话框要求输入 API Key"""
        from pathlib import Path
        env_path = Path(__file__).resolve().parent.parent / ".env"
        key, ok = QInputDialog.getText(
            None,
            "DeepSeek API Key",
            "请输入您的 DeepSeek API Key：\n"
            "（可在 https://platform.deepseek.com 获取）\n\n"
            f"也可将 Key 写入以下文件后重启，跳过此步骤：\n{env_path}",
        )
        if ok and key.strip():
            return key.strip()
        return None

    @staticmethod
    def _verify_api_key(api_key: str) -> bool:
        """通过轻量 API 调用验证 Key 是否可用"""
        try:
            from openai import OpenAI
            client = OpenAI(api_key=api_key, base_url=Config.BASE_URL, timeout=10)
            client.models.list()
            return True
        except Exception:
            return False

    # ========== 初始化 ==========

    def _init_client(self) -> None:
        """创建初始聊天客户端（默认角色扮演模式）"""
        strategy = RolePlayStrategy()
        self._client = DeepSeekChatClient(strategy=strategy, model=strategy.recommended_model)

    def _load_global_user_prompt(self) -> str:
        """从 user_prefs.json 加载全局提示词"""
        try:
            if os.path.exists(_USER_PREFS_FILE):
                with open(_USER_PREFS_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return data.get("global_user_prompt", "")
        except Exception:
            pass
        return ""

    def _save_global_user_prompt(self, prompt: str) -> None:
        """保存全局提示词到 user_prefs.json"""
        try:
            data = {}
            if os.path.exists(_USER_PREFS_FILE):
                with open(_USER_PREFS_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
            data["global_user_prompt"] = prompt
            with open(_USER_PREFS_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[Warning] Failed to save user prefs: {e}")

    def _init_ui(self) -> None:
        """构建 UI 布局"""
        self.setWindowTitle("DeepSeek 多功能聊天客户端")
        self.resize(1200, 780)

        # 中央分割器
        splitter = QSplitter(Qt.Orientation.Horizontal)

        left_panel = self._build_left_panel()
        splitter.addWidget(left_panel)

        right_panel = self._build_right_panel()
        splitter.addWidget(right_panel)

        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([450, 750])

        self.setCentralWidget(splitter)

        self._display.setHtml(INITIAL_HTML)
        self._mode_stack.setCurrentIndex(0)  # 默认显示角色扮演面板
        self._refresh_history_list()

    def closeEvent(self, event):
        """关闭窗口时检查是否正在流式输出"""
        if self._streaming:
            reply = QMessageBox.question(
                self, "确认退出",
                "AI 正在生成回答中，确定要退出吗？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.No:
                event.ignore()
                return
        event.accept()

    def _build_left_panel(self) -> QWidget:
        """构建左侧控制面板（含小说专属区域）"""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMaximumWidth(360)
        scroll.setMinimumWidth(280)

        container = QWidget()
        container.setStyleSheet("QWidget { background: transparent; }")
        layout = QVBoxLayout(container)
        layout.setSpacing(4)
        layout.setContentsMargins(6, 6, 6, 6)

        # ── 聊天模式 ──
        mode_group = QGroupBox("📌 聊天模式")
        mode_layout = QVBoxLayout(mode_group)
        mode_layout.setContentsMargins(8, 4, 8, 4)
        self._mode_combo = QComboBox()
        self._mode_combo.addItems(list(STRATEGY_OPTIONS.keys()))
        self._mode_combo.currentTextChanged.connect(self._on_mode_changed)
        mode_layout.addWidget(self._mode_combo)
        layout.addWidget(mode_group)

        # ── 模型选择 ──
        model_group = QGroupBox("🧠 模型选择")
        model_layout = QVBoxLayout(model_group)
        model_layout.setContentsMargins(8, 4, 8, 4)
        self._model_combo = QComboBox()
        self._model_combo.addItems(MODEL_OPTIONS)
        self._model_combo.currentTextChanged.connect(self._on_model_changed)
        model_layout.addWidget(self._model_combo)
        layout.addWidget(model_group)

        # ── 生成参数 ──
        param_group = QGroupBox("⚙️ 生成参数")
        param_layout = QVBoxLayout(param_group)
        param_layout.setSpacing(4)
        param_layout.setContentsMargins(8, 4, 8, 4)

        # ── 参数预设方案 ──
        preset_row = QHBoxLayout()
        preset_row.setContentsMargins(0, 0, 0, 0)
        preset_label = QLabel("预设方案")
        preset_label.setFixedWidth(60)
        self._preset_combo = QComboBox()
        self._preset_combo.addItems(COMBO_ITEMS)
        self._preset_combo.currentTextChanged.connect(self._on_preset_changed)
        preset_row.addWidget(preset_label)
        preset_row.addWidget(self._preset_combo, stretch=1)
        param_layout.addLayout(preset_row)

        # 温度
        temp_row = QHBoxLayout()
        temp_row.setContentsMargins(0, 0, 0, 0)
        temp_label = QLabel("温度")
        temp_label.setFixedWidth(36)
        self._temp_slider = QSlider(Qt.Orientation.Horizontal)
        self._temp_slider.setRange(0, 200)
        self._temp_slider.setValue(int(self._client.recommended_temperature * 100))
        self._temp_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self._temp_slider.setTickInterval(50)
        self._temp_slider.valueChanged.connect(self._on_temp_changed)
        self._temp_value = QLabel(f"{self._temp_slider.value() / 100:.2f}")
        self._temp_value.setFixedWidth(36)
        self._temp_value.setAlignment(Qt.AlignmentFlag.AlignCenter)
        temp_row.addWidget(temp_label)
        temp_row.addWidget(self._temp_slider, stretch=1)
        temp_row.addWidget(self._temp_value)
        param_layout.addLayout(temp_row)

        # top_p
        top_p_row = QHBoxLayout()
        top_p_row.setContentsMargins(0, 0, 0, 0)
        top_p_label = QLabel("top_p")
        top_p_label.setFixedWidth(36)
        self._top_p_slider = QSlider(Qt.Orientation.Horizontal)
        self._top_p_slider.setRange(0, 100)
        self._top_p_slider.setValue(int(self._client.recommended_top_p * 100))
        self._top_p_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self._top_p_slider.setTickInterval(25)
        self._top_p_slider.valueChanged.connect(self._on_top_p_changed)
        self._top_p_value = QLabel(f"{self._top_p_slider.value() / 100:.2f}")
        self._top_p_value.setFixedWidth(36)
        self._top_p_value.setAlignment(Qt.AlignmentFlag.AlignCenter)
        top_p_row.addWidget(top_p_label)
        top_p_row.addWidget(self._top_p_slider, stretch=1)
        top_p_row.addWidget(self._top_p_value)
        param_layout.addLayout(top_p_row)

        # frequency_penalty
        fp_row = QHBoxLayout()
        fp_row.setContentsMargins(0, 0, 0, 0)
        fp_label = QLabel("freq_p")
        fp_label.setFixedWidth(36)
        self._fp_slider = QSlider(Qt.Orientation.Horizontal)
        self._fp_slider.setRange(-200, 200)
        self._fp_slider.setValue(int(self._client.recommended_frequency_penalty * 100))
        self._fp_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self._fp_slider.setTickInterval(50)
        self._fp_slider.valueChanged.connect(self._on_fp_changed)
        self._fp_value = QLabel(f"{self._fp_slider.value() / 100:.2f}")
        self._fp_value.setFixedWidth(36)
        self._fp_value.setAlignment(Qt.AlignmentFlag.AlignCenter)
        fp_row.addWidget(fp_label)
        fp_row.addWidget(self._fp_slider, stretch=1)
        fp_row.addWidget(self._fp_value)
        param_layout.addLayout(fp_row)

        # max_tokens
        mt_row = QHBoxLayout()
        mt_row.setContentsMargins(0, 0, 0, 0)
        mt_label = QLabel("max_tk")
        mt_label.setFixedWidth(36)
        self._mt_spin = QSpinBox()
        self._mt_spin.setRange(1, 300000)
        self._mt_spin.setValue(self._client.recommended_max_tokens)
        self._mt_spin.setSingleStep(512)
        self._mt_spin.setToolTip("单次输出上限（tokens）。数值越大，模型单次可生成的字数越多。\n如需 20000 中文字输出，建议设为 ≥40000。")
        self._mt_spin.valueChanged.connect(self._on_mt_changed)
        mt_row.addWidget(mt_label)
        mt_row.addWidget(self._mt_spin, stretch=1)
        param_layout.addLayout(mt_row)

        layout.addWidget(param_group)

        # ── 操作按钮 ──
        self._btn_group = QGroupBox("操作")
        btn_layout = QVBoxLayout(self._btn_group)
        btn_layout.setContentsMargins(8, 4, 8, 4)
        btn_layout.setSpacing(4)

        clear_btn = QPushButton("🗑️ 清除对话")
        clear_btn.setStyleSheet("""
            QPushButton {
                background: #6b2a2a;
                color: white;
                border: none;
                border-radius: 6px;
                padding: 7px 16px;
                font-weight: bold;
            }
            QPushButton:hover {
                background: #8b3a3a;
                border: 1px solid rgba(255, 255, 255, 0.1);
            }
            QPushButton:pressed {
                background: #5b1a1a;
            }
        """)
        clear_btn.clicked.connect(self._on_clear)
        btn_layout.addWidget(clear_btn)

        layout.addWidget(self._btn_group)

        # ── 💬 对话历史 ──
        self._history_group = QGroupBox("💬 对话历史")
        history_layout = QVBoxLayout(self._history_group)
        history_layout.setContentsMargins(8, 4, 8, 4)
        history_layout.setSpacing(4)

        save_hist_btn = QPushButton("💾 保存当前对话")
        save_hist_btn.clicked.connect(self._on_save_conversation)
        history_layout.addWidget(save_hist_btn)

        hist_list_row = QHBoxLayout()
        self._history_combo = QComboBox()
        self._history_combo.setMinimumWidth(120)
        self._history_combo.setToolTip("选择已保存的对话")
        self._history_combo.currentIndexChanged.connect(self._on_history_selection_changed)
        hist_list_row.addWidget(self._history_combo, stretch=1)

        load_hist_btn = QPushButton("📂 加载")
        load_hist_btn.setToolTip("加载选中的对话历史")
        load_hist_btn.clicked.connect(self._on_load_conversation)
        hist_list_row.addWidget(load_hist_btn)

        delete_hist_btn = QPushButton("🗑")
        delete_hist_btn.setToolTip("删除选中的对话历史")
        delete_hist_btn.setMaximumWidth(40)
        delete_hist_btn.clicked.connect(self._on_delete_conversation)
        hist_list_row.addWidget(delete_hist_btn)

        history_layout.addLayout(hist_list_row)

        self._history_status_label = QLabel("暂无已保存对话")
        self._history_status_label.setWordWrap(True)
        history_layout.addWidget(self._history_status_label)

        # ── 导出对话 ──
        hist_export_row = QHBoxLayout()
        self._hist_export_format_combo = QComboBox()
        for fmt in EXPORT_FORMATS:
            self._hist_export_format_combo.addItem(FORMAT_LABELS[fmt], userData=fmt)
        hist_export_row.addWidget(self._hist_export_format_combo, stretch=1)
        export_hist_btn = QPushButton("📤 导出对话")
        export_hist_btn.clicked.connect(self._on_export_conversation)
        hist_export_row.addWidget(export_hist_btn)
        history_layout.addLayout(hist_export_row)

        layout.addWidget(self._history_group)

        # ── 状态信息 ──
        status_group = QGroupBox("📋 状态")
        status_layout = QVBoxLayout(status_group)
        status_layout.setContentsMargins(8, 4, 8, 4)
        self._status_label = QLabel("当前模式: 角色扮演\n模型: deepseek-v4-flash")
        self._status_label.setWordWrap(True)
        status_layout.addWidget(self._status_label)
        self._stream_count_label = QLabel("")
        self._stream_count_label.setStyleSheet("color: #6a9955; font-size: 12px; padding: 2px 0;")
        self._stream_count_label.setVisible(False)
        status_layout.addWidget(self._stream_count_label)
        layout.addWidget(status_group)

        # ── 模式专属面板容器（QStackedWidget 切换不触发布局重排）──
        self._role_play_panel = self._build_role_play_panel()
        self._novel_panel = self._build_novel_panel()
        self._continuation_panel = self._build_continuation_panel()
        self._mode_stack = QStackedWidget()
        self._mode_stack.addWidget(self._role_play_panel)    # idx 0
        self._mode_stack.addWidget(self._novel_panel)        # idx 1
        self._mode_stack.addWidget(self._continuation_panel) # idx 2
        layout.addWidget(self._mode_stack)

        layout.addStretch()
        scroll.setWidget(container)
        return scroll

    def _build_role_play_panel(self) -> QGroupBox:
        """构建角色扮演专属面板"""
        panel = QGroupBox("🎭 角色扮演 · 角色设定")
        layout = QVBoxLayout(panel)
        layout.setSpacing(4)
        layout.setContentsMargins(8, 4, 8, 4)

        # ── 角色描述 ──
        char_label = QLabel("👤 角色描述")
        layout.addWidget(char_label)
        self._role_char_edit = QTextEdit()
        self._role_char_edit.setPlaceholderText(
            "描述要扮演的角色：姓名、性格、外貌、身份、语言风格...\n"
            "例如：一位傲娇的中世纪骑士，身材高大，说话简短有力..."
        )
        self._role_char_edit.setMaximumHeight(100)
        self._role_char_edit.setMinimumHeight(70)
        self._role_char_edit.textChanged.connect(self._on_role_char_changed)
        layout.addWidget(self._role_char_edit)

        # ── 故事背景 ──
        bg_label = QLabel("🌍 故事背景")
        layout.addWidget(bg_label)
        self._role_bg_edit = QTextEdit()
        self._role_bg_edit.setPlaceholderText(
            "描述故事发生的世界、时代、情境...\n"
            "例如：中世纪欧洲，魔法存在，正值十字军东征时期..."
        )
        self._role_bg_edit.setMaximumHeight(100)
        self._role_bg_edit.setMinimumHeight(70)
        self._role_bg_edit.textChanged.connect(self._on_role_bg_changed)
        layout.addWidget(self._role_bg_edit)

        # ── 回复方式 ──
        mode_label = QLabel("💬 回复方式")
        layout.addWidget(mode_label)

        self._reply_mode_group = QButtonGroup(panel)
        self._radio_character = QRadioButton("角色回答（第一人称）")
        self._radio_narrator = QRadioButton("旁白描述（第三人称叙述）")
        self._radio_character.setChecked(True)
        self._reply_mode_group.addButton(self._radio_character, 0)
        self._reply_mode_group.addButton(self._radio_narrator, 1)
        self._reply_mode_group.idClicked.connect(self._on_reply_mode_changed)
        layout.addWidget(self._radio_character)
        layout.addWidget(self._radio_narrator)

        # ── 应用设定按钮 ──
        apply_btn = QPushButton("✅ 应用设定（重置对话）")
        apply_btn.setMinimumHeight(36)
        apply_btn.setStyleSheet("""
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #1a6b3c, stop:1 #2a8b5c);
                color: white;
                border: none;
                border-radius: 6px;
                padding: 8px 16px;
                font-weight: bold;
                font-size: 13px;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #2a8b4c, stop:1 #3a9b6c);
                border: 1px solid rgba(255, 255, 255, 0.1);
            }
            QPushButton:pressed {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #0a5b2c, stop:1 #1a7b4c);
            }
        """)
        apply_btn.clicked.connect(self._on_apply_role_settings)
        layout.addWidget(apply_btn)

        layout.addStretch()
        return panel

    def _build_novel_panel(self) -> QGroupBox:
        """构建小说写作专属面板"""
        panel = QGroupBox("📚 小说写作 · 书架 & 章节")
        layout = QVBoxLayout(panel)
        layout.setSpacing(4)
        layout.setContentsMargins(8, 4, 8, 4)

        # ── 书架选择 ──
        bookshelf_label = QLabel("📖 书架（当前小说）")
        layout.addWidget(bookshelf_label)

        bookshelf_row = QHBoxLayout()
        self._bookshelf_combo = QComboBox()
        self._bookshelf_combo.setMinimumWidth(120)
        self._bookshelf_combo.currentTextChanged.connect(self._on_book_selected)
        bookshelf_row.addWidget(self._bookshelf_combo, stretch=1)

        create_book_btn = QPushButton("➕ 新建")
        create_book_btn.setMinimumWidth(70)
        create_book_btn.clicked.connect(self._on_create_book)
        bookshelf_row.addWidget(create_book_btn)

        delete_book_btn = QPushButton("🗑 删除")
        delete_book_btn.setMinimumWidth(70)
        delete_book_btn.clicked.connect(self._on_delete_book)
        bookshelf_row.addWidget(delete_book_btn)

        layout.addLayout(bookshelf_row)

        # ── 小说标题 ──
        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_label = QLabel("标题")
        title_label.setFixedWidth(36)
        self._novel_title_edit = QLineEdit()
        self._novel_title_edit.setPlaceholderText("输入小说标题...")
        self._novel_title_edit.textChanged.connect(self._on_novel_title_changed)
        title_row.addWidget(title_label)
        title_row.addWidget(self._novel_title_edit, stretch=1)
        layout.addLayout(title_row)

        # ── 章节标题 ──
        ch_row = QHBoxLayout()
        ch_row.setContentsMargins(0, 0, 0, 0)
        ch_label = QLabel("章节")
        ch_label.setFixedWidth(36)
        self._chapter_title_edit = QLineEdit()
        self._chapter_title_edit.setPlaceholderText("本章标题（如'少年踏上征途'）")
        self._chapter_title_edit.textChanged.connect(self._on_chapter_title_changed)
        ch_row.addWidget(ch_label)
        ch_row.addWidget(self._chapter_title_edit, stretch=1)
        layout.addLayout(ch_row)

        # ── 章节信息 ──
        self._chapter_info_label = QLabel("尚未选择小说")
        self._chapter_info_label.setWordWrap(True)
        layout.addWidget(self._chapter_info_label)

        # ── 章节模式开关 ──
        self._chapter_mode_check = QCheckBox("📖 章节续写模式（勾选后发送即生成下一章）")
        self._chapter_mode_check.setChecked(False)
        self._chapter_mode_check.toggled.connect(self._on_chapter_mode_toggled)
        layout.addWidget(self._chapter_mode_check)

        # ── 主角设定 ──
        protag_label = QLabel("👤 主角设定")
        layout.addWidget(protag_label)
        self._protagonist_edit = QTextEdit()
        self._protagonist_edit.setPlaceholderText("描述主角背景、性格、外貌...")
        self._protagonist_edit.setMaximumHeight(80)
        self._protagonist_edit.setMinimumHeight(60)
        self._protagonist_edit.textChanged.connect(self._on_protagonist_changed)
        layout.addWidget(self._protagonist_edit)

        # ── 世界观/背景 ──
        bg_label = QLabel("🌍 世界观 / 背景故事")
        layout.addWidget(bg_label)
        self._background_edit = QTextEdit()
        self._background_edit.setPlaceholderText("描述世界观、时代背景、核心设定...")
        self._background_edit.setMaximumHeight(80)
        self._background_edit.setMinimumHeight(60)
        self._background_edit.textChanged.connect(self._on_background_changed)
        layout.addWidget(self._background_edit)

        # ── 写作要求 ──
        demand_label = QLabel("✍️ 写作要求")
        layout.addWidget(demand_label)
        self._demand_edit = QTextEdit()
        self._demand_edit.setPlaceholderText("本章具体写作要求（风格、节奏、必须包含的元素...）")
        self._demand_edit.setMaximumHeight(60)
        self._demand_edit.setMinimumHeight(48)
        self._demand_edit.textChanged.connect(self._on_demand_changed)
        layout.addWidget(self._demand_edit)

        # ── 用户全局提示词 ──
        global_prompt_btn = QPushButton("🌐 编辑全局偏好提示词")
        global_prompt_btn.setMinimumHeight(32)
        global_prompt_btn.setToolTip(
            "点击编辑您的写作偏好、习惯风格等全局提示词，"
            "将自动注入所有生成和摘要请求"
        )
        global_prompt_btn.setStyleSheet("""
            QPushButton { background: #3a3a5a; color: #d4d4d4; border: 1px solid #5a5a7a;
                          border-radius: 6px; padding: 4px 8px; font-size: 12px; }
            QPushButton:hover { background: #4a4a6a; border-color: #7a7a9a; }
        """)
        global_prompt_btn.clicked.connect(self._on_edit_global_prompt)
        layout.addWidget(global_prompt_btn)

        # ── 本章情节输入 ──
        plot_label = QLabel("📝 本章情节输入（可选）")
        layout.addWidget(plot_label)
        self._plot_edit = QTextEdit()
        self._plot_edit.setPlaceholderText(
            "在此填写本章你想写的关键情节、对话、场景或任何具体内容。\n"
            "AI 会以此为基础扩展为完整章节。留空则 AI 完全自主创作。"
        )
        self._plot_edit.setMaximumHeight(120)
        self._plot_edit.setMinimumHeight(80)
        layout.addWidget(self._plot_edit)

        # ── 目标字数 ──
        word_row = QHBoxLayout()
        word_row.setContentsMargins(0, 0, 0, 0)
        word_label = QLabel("字数")
        word_label.setFixedWidth(36)
        self._chapter_word_count = QSpinBox()
        self._chapter_word_count.setRange(100, 100000)
        self._chapter_word_count.setValue(10000)
        self._chapter_word_count.setSingleStep(500)
        self._chapter_word_count.setSuffix(" 字")
        word_row.addWidget(word_label)
        word_row.addWidget(self._chapter_word_count, stretch=1)
        layout.addLayout(word_row)

        # ── 生成章节按钮 ──
        generate_btn = QPushButton("🚀 生成下一章")
        generate_btn.setMinimumHeight(40)
        generate_btn.setStyleSheet("""
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #7a4a9c, stop:1 #9a6abc);
                color: white;
                border: none;
                border-radius: 8px;
                padding: 8px 16px;
                font-weight: bold;
                font-size: 14px;
                letter-spacing: 1px;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #8a5aac, stop:1 #aa7acc);
                border: 1px solid rgba(255, 255, 255, 0.1);
            }
            QPushButton:pressed {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #6a3a8c, stop:1 #8a5aac);
            }
        """)
        generate_btn.clicked.connect(self._on_generate_chapter)
        layout.addWidget(generate_btn)

        # ── 保存/加载设定按钮 ──
        save_settings_row = QHBoxLayout()
        save_settings_btn = QPushButton("💾 保存小说设定")
        save_settings_btn.clicked.connect(self._on_save_novel_settings)
        save_settings_row.addWidget(save_settings_btn)

        load_settings_btn = QPushButton("📂 加载小说设定")
        load_settings_btn.clicked.connect(self._on_load_novel_settings)
        save_settings_row.addWidget(load_settings_btn)
        layout.addLayout(save_settings_row)

        # ── 章节管理按钮 ──
        manage_chapters_btn = QPushButton("⚙ 章节管理（查看 / 删除 / 选择版本）")
        manage_chapters_btn.setMinimumHeight(32)
        manage_chapters_btn.clicked.connect(self._on_manage_chapters)
        layout.addWidget(manage_chapters_btn)

        # ── 世界书 + 分段摘要按钮 ──
        tool_row = QHBoxLayout()
        world_bible_btn = QPushButton("📖 世界书")
        world_bible_btn.setToolTip("查看/编辑已建立的世界观设定库")
        world_bible_btn.clicked.connect(self._on_world_bible)
        tool_row.addWidget(world_bible_btn)
        split_summary_btn = QPushButton("📄 分段摘要")
        split_summary_btn.setToolTip("对选定的文件进行分段摘要分析")
        split_summary_btn.clicked.connect(self._on_split_summarize)
        tool_row.addWidget(split_summary_btn)
        layout.addLayout(tool_row)

        # ── 导出按钮 ──
        export_label = QLabel("导出")
        layout.addWidget(export_label)
        export_format_row = QHBoxLayout()
        self._export_format_combo = QComboBox()
        for fmt in EXPORT_FORMATS:
            self._export_format_combo.addItem(FORMAT_LABELS[fmt], userData=fmt)
        export_format_row.addWidget(self._export_format_combo, stretch=1)
        layout.addLayout(export_format_row)

        export_btn_row = QHBoxLayout()
        export_chapter_btn = QPushButton("📄 导出当前章节")
        export_chapter_btn.clicked.connect(self._on_export_chapter)
        export_btn_row.addWidget(export_chapter_btn)
        export_book_btn = QPushButton("📚 导出全书")
        export_book_btn.clicked.connect(self._on_export_book)
        export_btn_row.addWidget(export_book_btn)
        layout.addLayout(export_btn_row)

        return panel

    def _build_continuation_panel(self) -> QGroupBox:
        """构建续写小说专属面板（大修版：含书架、章节管理、设定编辑）"""
        panel = QGroupBox("📄 续写小说 · 源文档 & 书架")
        layout = QVBoxLayout(panel)
        layout.setSpacing(4)
        layout.setContentsMargins(8, 4, 8, 4)

        # ================================================================
        # ① 源文档选择（续写独有）
        # ================================================================
        file_label = QLabel("📄 源文档")
        layout.addWidget(file_label)
        file_row = QHBoxLayout()
        self._continue_file_path = QLineEdit()
        self._continue_file_path.setPlaceholderText("未选择文件...")
        self._continue_file_path.setReadOnly(True)
        file_row.addWidget(self._continue_file_path, stretch=1)
        browse_file_btn = QPushButton("浏览")
        browse_file_btn.setMaximumWidth(60)
        browse_file_btn.clicked.connect(self._on_browse_continue_file)
        file_row.addWidget(browse_file_btn)
        layout.addLayout(file_row)

        folder_row = QHBoxLayout()
        self._continue_folder_path = QLineEdit()
        self._continue_folder_path.setPlaceholderText("未选择文件夹...")
        self._continue_folder_path.setReadOnly(True)
        folder_row.addWidget(self._continue_folder_path, stretch=1)
        browse_folder_btn = QPushButton("浏览")
        browse_folder_btn.setMaximumWidth(60)
        browse_folder_btn.clicked.connect(self._on_browse_continue_folder)
        folder_row.addWidget(browse_folder_btn)
        layout.addLayout(folder_row)

        # ── 源文档快速分析 + 直接续写按钮行 ──
        source_btn_row = QHBoxLayout()
        analyze_cont_btn = QPushButton("🔍 分析源文档并导入设定")
        analyze_cont_btn.setMinimumHeight(32)
        analyze_cont_btn.setStyleSheet("""
            QPushButton { background: #2d5a8b; color: white; border: none;
                          border-radius: 6px; padding: 6px 12px; font-weight: bold; }
            QPushButton:hover { background: #3d7abb; }
        """)
        analyze_cont_btn.clicked.connect(self._on_analyze_continuation)
        source_btn_row.addWidget(analyze_cont_btn)

        quick_cont_btn = QPushButton("⚡ 直接续写")
        quick_cont_btn.setMinimumHeight(32)
        quick_cont_btn.setStyleSheet("""
            QPushButton { background: #b85a2c; color: white; border: none;
                          border-radius: 6px; padding: 6px 12px; font-weight: bold; }
            QPushButton:hover { background: #d87a4c; }
        """)
        quick_cont_btn.clicked.connect(self._on_start_continuation)
        source_btn_row.addWidget(quick_cont_btn)
        layout.addLayout(source_btn_row)

        # ================================================================
        # ② 书架与章节管理（新增，复用小说模式设计）
        # ================================================================
        sep1 = QLabel("── 书架与章节管理 ──")
        sep1.setStyleSheet("color: #888; font-size: 11px; padding: 2px 0;")
        layout.addWidget(sep1)

        bookshelf_row = QHBoxLayout()
        self._cont_bookshelf_combo = QComboBox()
        self._cont_bookshelf_combo.setMinimumWidth(120)
        self._cont_bookshelf_combo.currentTextChanged.connect(self._on_cont_book_selected)
        bookshelf_row.addWidget(self._cont_bookshelf_combo, stretch=1)

        cont_create_book_btn = QPushButton("➕ 新建")
        cont_create_book_btn.setMinimumWidth(70)
        cont_create_book_btn.clicked.connect(self._on_cont_create_book)
        bookshelf_row.addWidget(cont_create_book_btn)

        cont_delete_book_btn = QPushButton("🗑 删除")
        cont_delete_book_btn.setMinimumWidth(70)
        cont_delete_book_btn.clicked.connect(self._on_cont_delete_book)
        bookshelf_row.addWidget(cont_delete_book_btn)
        layout.addLayout(bookshelf_row)

        # 章节标题
        cont_ch_row = QHBoxLayout()
        cont_ch_row.setContentsMargins(0, 0, 0, 0)
        cont_ch_label = QLabel("章节")
        cont_ch_label.setFixedWidth(36)
        self._cont_chapter_title_edit = QLineEdit()
        self._cont_chapter_title_edit.setPlaceholderText("本章标题（如'风雨欲来'）")
        cont_ch_row.addWidget(cont_ch_label)
        cont_ch_row.addWidget(self._cont_chapter_title_edit, stretch=1)
        layout.addLayout(cont_ch_row)

        # 章节信息
        self._cont_chapter_info_label = QLabel("尚未选择小说")
        self._cont_chapter_info_label.setWordWrap(True)
        layout.addWidget(self._cont_chapter_info_label)

        # 章节模式
        self._cont_chapter_mode_check = QCheckBox("📖 章节续写模式（勾选后发送即生成下一章）")
        self._cont_chapter_mode_check.setChecked(False)
        self._cont_chapter_mode_check.toggled.connect(self._on_cont_chapter_mode_toggled)
        layout.addWidget(self._cont_chapter_mode_check)

        # ================================================================
        # ③ 小说设定（新增，复用小说模式设计）
        # ================================================================
        sep2 = QLabel("── 小说设定 ──")
        sep2.setStyleSheet("color: #888; font-size: 11px; padding: 2px 0;")
        layout.addWidget(sep2)

        protag_label = QLabel("👤 主角设定")
        layout.addWidget(protag_label)
        self._cont_protagonist_edit = QTextEdit()
        self._cont_protagonist_edit.setPlaceholderText("描述主角背景、性格、外貌...")
        self._cont_protagonist_edit.setMaximumHeight(80)
        self._cont_protagonist_edit.setMinimumHeight(60)
        self._cont_protagonist_edit.textChanged.connect(self._on_cont_protagonist_changed)
        layout.addWidget(self._cont_protagonist_edit)

        bg_label = QLabel("🌍 世界观 / 背景故事")
        layout.addWidget(bg_label)
        self._cont_background_edit = QTextEdit()
        self._cont_background_edit.setPlaceholderText("描述世界观、时代背景、核心设定...")
        self._cont_background_edit.setMaximumHeight(80)
        self._cont_background_edit.setMinimumHeight(60)
        self._cont_background_edit.textChanged.connect(self._on_cont_background_changed)
        layout.addWidget(self._cont_background_edit)

        demand_label = QLabel("✍️ 写作要求")
        layout.addWidget(demand_label)
        self._cont_demand_edit = QTextEdit()
        self._cont_demand_edit.setPlaceholderText("本章具体写作要求（风格、节奏、必须包含的元素...）")
        self._cont_demand_edit.setMaximumHeight(60)
        self._cont_demand_edit.setMinimumHeight(48)
        self._cont_demand_edit.textChanged.connect(self._on_cont_demand_changed)
        layout.addWidget(self._cont_demand_edit)

        # ── 用户全局提示词 ──
        cont_global_prompt_btn = QPushButton("🌐 编辑全局偏好提示词")
        cont_global_prompt_btn.setMinimumHeight(32)
        cont_global_prompt_btn.setToolTip(
            "点击编辑您的写作偏好、习惯风格等全局提示词，"
            "将自动注入所有生成和摘要请求"
        )
        cont_global_prompt_btn.setStyleSheet("""
            QPushButton { background: #3a3a5a; color: #d4d4d4; border: 1px solid #5a5a7a;
                          border-radius: 6px; padding: 4px 8px; font-size: 12px; }
            QPushButton:hover { background: #4a4a6a; border-color: #7a7a9a; }
        """)
        cont_global_prompt_btn.clicked.connect(self._on_edit_global_prompt)
        layout.addWidget(cont_global_prompt_btn)

        # ── 保存/加载设定按钮 ──
        cont_save_settings_row = QHBoxLayout()
        cont_save_settings_btn = QPushButton("💾 保存小说设定")
        cont_save_settings_btn.clicked.connect(self._on_cont_save_settings)
        cont_save_settings_row.addWidget(cont_save_settings_btn)
        cont_load_settings_btn = QPushButton("📂 加载小说设定")
        cont_load_settings_btn.clicked.connect(self._on_cont_load_settings)
        cont_save_settings_row.addWidget(cont_load_settings_btn)
        layout.addLayout(cont_save_settings_row)

        # ── 章节管理 + 世界书按钮 ──
        cont_mgr_row = QHBoxLayout()
        cont_mgr_btn = QPushButton("⚙ 章节管理")
        cont_mgr_btn.setMinimumHeight(32)
        cont_mgr_btn.clicked.connect(self._on_manage_chapters)
        cont_mgr_row.addWidget(cont_mgr_btn)
        cont_wb_btn = QPushButton("📖 世界书")
        cont_wb_btn.setMinimumHeight(32)
        cont_wb_btn.clicked.connect(self._on_world_bible)
        cont_mgr_row.addWidget(cont_wb_btn)
        layout.addLayout(cont_mgr_row)

        # ================================================================
        # ④ 续写操作（保留原有 + 新增生成按钮）
        # ================================================================
        sep3 = QLabel("── 续写操作 ──")
        sep3.setStyleSheet("color: #888; font-size: 11px; padding: 2px 0;")
        layout.addWidget(sep3)

        # 续写要求
        req_label = QLabel("续写要求")
        layout.addWidget(req_label)
        self._continue_requirement = QTextEdit()
        self._continue_requirement.setPlaceholderText(
            "对续写的具体要求：风格、视角、节奏、必须包含的元素...\n"
            "例如：保持悬疑风格，增加环境描写，每段不超过200字"
        )
        self._continue_requirement.setMaximumHeight(80)
        self._continue_requirement.setMinimumHeight(60)
        layout.addWidget(self._continue_requirement)

        # 字数
        word_row = QHBoxLayout()
        word_row.setContentsMargins(0, 0, 0, 0)
        word_label = QLabel("字数")
        word_label.setFixedWidth(36)
        self._continue_word_count = QSpinBox()
        self._continue_word_count.setRange(100, 100000)
        self._continue_word_count.setValue(10000)
        self._continue_word_count.setSingleStep(500)
        self._continue_word_count.setSuffix(" 字")
        word_row.addWidget(word_label)
        word_row.addWidget(self._continue_word_count, stretch=1)
        layout.addLayout(word_row)

        # 续写剧情（可选）
        plot_label = QLabel("续写剧情（可选）")
        layout.addWidget(plot_label)
        self._continue_plot = QTextEdit()
        self._continue_plot.setPlaceholderText(
            "续写的剧情走向、关键事件、对话等。\n留空则 AI 根据原文风格自主续写。"
        )
        self._continue_plot.setMaximumHeight(80)
        self._continue_plot.setMinimumHeight(60)
        layout.addWidget(self._continue_plot)

        # ── 续写辅助：AI 建议 / 自行指定剧情 ──
        plot_helper_row = QHBoxLayout()
        cont_suggest_btn = QPushButton("🎲 AI 建议发展方向")
        cont_suggest_btn.setStyleSheet("""
            QPushButton { background: #2d6b2d; color: white; border: none;
                          border-radius: 6px; padding: 6px 12px; font-weight: bold; }
            QPushButton:hover { background: #3d8b3d; }
        """)
        cont_suggest_btn.clicked.connect(self._on_cont_panel_suggest)
        plot_helper_row.addWidget(cont_suggest_btn)

        cont_specify_btn = QPushButton("📝 我指定剧情")
        cont_specify_btn.setStyleSheet("""
            QPushButton { background: #6b4d2d; color: white; border: none;
                          border-radius: 6px; padding: 6px 12px; font-weight: bold; }
            QPushButton:hover { background: #8b6d3d; }
        """)
        cont_specify_btn.clicked.connect(self._on_cont_panel_specify)
        plot_helper_row.addWidget(cont_specify_btn)
        layout.addLayout(plot_helper_row)

        # ── 生成下一章按钮 ──
        cont_generate_btn = QPushButton("🚀 生成下一章")
        cont_generate_btn.setMinimumHeight(40)
        cont_generate_btn.setStyleSheet("""
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #7a4a9c, stop:1 #9a6abc);
                color: white;
                border: none;
                border-radius: 8px;
                padding: 8px 16px;
                font-weight: bold;
                font-size: 14px;
                letter-spacing: 1px;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #8a5aac, stop:1 #aa7acc);
                border: 1px solid rgba(255, 255, 255, 0.1);
            }
            QPushButton:pressed {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #6a3a8c, stop:1 #8a5aac);
            }
        """)
        cont_generate_btn.clicked.connect(self._on_cont_generate_chapter)
        layout.addWidget(cont_generate_btn)

        # ── 导出 ──
        export_label = QLabel("导出")
        layout.addWidget(export_label)
        export_fmt_row = QHBoxLayout()
        self._cont_export_format_combo = QComboBox()
        for fmt in EXPORT_FORMATS:
            self._cont_export_format_combo.addItem(FORMAT_LABELS[fmt], userData=fmt)
        export_fmt_row.addWidget(self._cont_export_format_combo, stretch=1)
        layout.addLayout(export_fmt_row)

        export_btn_row = QHBoxLayout()
        cont_export_chapter_btn = QPushButton("📄 导出当前章节")
        cont_export_chapter_btn.clicked.connect(self._on_export_cont_chapter)
        export_btn_row.addWidget(cont_export_chapter_btn)
        cont_export_book_btn = QPushButton("📚 导出全书")
        cont_export_book_btn.clicked.connect(self._on_export_cont_book)
        export_btn_row.addWidget(cont_export_book_btn)
        layout.addLayout(export_btn_row)

        return panel

    def _build_right_panel(self) -> QWidget:
        """构建右侧聊天区域"""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Markdown 渲染显示区
        self._display = QWebEngineView()
        self._display.settings().setAttribute(
            QWebEngineSettings.WebAttribute.JavascriptEnabled, True
        )
        self._display.page().setBackgroundColor(QColor("#1e1e1e"))
        self._display.setMinimumHeight(300)
        layout.addWidget(self._display, stretch=1)

        # 底部输入区
        input_frame = QFrame()
        input_frame.setFrameShape(QFrame.Shape.StyledPanel)
        input_frame.setStyleSheet("""
            QFrame {
                background: rgba(26, 26, 46, 0.95);
                border: none;
                border-top: 1px solid rgba(255, 255, 255, 0.06);
            }
        """)
        input_layout = QHBoxLayout(input_frame)
        input_layout.setContentsMargins(12, 8, 12, 8)
        input_layout.setSpacing(10)

        self._input_box = InputTextEdit()
        self._input_box.setPlaceholderText("输入消息，按 Ctrl+Enter 发送...")
        self._input_box.setMaximumHeight(120)
        self._input_box.setMinimumHeight(64)
        self._input_box.send_requested.connect(self._on_send)
        input_layout.addWidget(self._input_box, stretch=1)

        send_btn = QPushButton("发 送")
        send_btn.setMinimumHeight(64)
        send_btn.setMinimumWidth(80)
        send_btn.setStyleSheet("""
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #0e639c, stop:1 #4a9fd8);
                color: white;
                border: none;
                border-radius: 8px;
                padding: 8px 20px;
                font-size: 14px;
                font-weight: bold;
                letter-spacing: 2px;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #1177bb, stop:1 #5aafe8);
                border: 1px solid rgba(255, 255, 255, 0.15);
            }
            QPushButton:pressed {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #094771, stop:1 #3a7ab0);
                padding-top: 10px;
                padding-bottom: 6px;
            }
        """)
        send_btn.clicked.connect(self._on_send)
        input_layout.addWidget(send_btn)

        layout.addWidget(input_frame)

        return widget

    # ========== 主题 ==========

    def _apply_dark_theme(self) -> None:
        """应用现代化深色主题样式"""
        self.setStyleSheet("""
            /* ========== 全局 ========== */
            QMainWindow {
                background-color: #1a1a2e;
            }
            QWidget {
                background-color: #1a1a2e;
                color: #d4d4d4;
                font-family: "Microsoft YaHei", "Segoe UI", sans-serif;
            }

            /* ========== 滚动条全局（Windows） ========== */
            QScrollBar:vertical {
                background: #1a1a2e;
                width: 8px;
                border: none;
            }
            QScrollBar::handle:vertical {
                background: #3a3a4a;
                border-radius: 4px;
                min-height: 30px;
            }
            QScrollBar::handle:vertical:hover { background: #569cd6; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
            QScrollBar:horizontal {
                background: #1a1a2e;
                height: 8px;
                border: none;
            }
            QScrollBar::handle:horizontal {
                background: #3a3a4a;
                border-radius: 4px;
                min-width: 30px;
            }
            QScrollBar::handle:horizontal:hover { background: #569cd6; }
            QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }

            /* ========== 分组框（卡片风格） ========== */
            QGroupBox {
                color: #d4d4d4;
                font-weight: 600;
                font-size: 13px;
                border: 1px solid rgba(255, 255, 255, 0.06);
                border-radius: 10px;
                margin-top: 12px;
                padding: 16px 12px 12px 12px;
                background: rgba(30, 30, 46, 0.6);
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 8px;
                color: #569cd6;
                background: transparent;
                letter-spacing: 0.3px;
            }

            /* ========== 下拉框 ========== */
            QComboBox {
                background: #2a2a3e;
                color: #d4d4d4;
                border: 1px solid rgba(255, 255, 255, 0.1);
                border-radius: 6px;
                padding: 6px 10px;
                min-height: 24px;
                font-size: 13px;
            }
            QComboBox:hover { border-color: #569cd6; background: #30304a; }
            QComboBox::drop-down {
                subcontrol-origin: padding;
                subcontrol-position: top right;
                width: 24px;
                border: none;
                border-radius: 0 6px 6px 0;
            }
            QComboBox::down-arrow {
                image: none;
                border-left: 5px solid transparent;
                border-right: 5px solid transparent;
                border-top: 6px solid #888;
                margin-right: 6px;
            }
            QComboBox QAbstractItemView {
                background: #2a2a3e;
                color: #d4d4d4;
                selection-background-color: #1e3a5f;
                selection-color: #fff;
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 4px;
                padding: 4px;
                outline: none;
            }

            /* ========== 按钮 ========== */
            QPushButton {
                background: #0e639c;
                color: white;
                border: none;
                border-radius: 6px;
                padding: 7px 16px;
                font-weight: 500;
                font-size: 12.5px;
                min-height: 24px;
            }
            QPushButton:hover {
                background: #1177bb;
                border: 1px solid rgba(255, 255, 255, 0.1);
            }
            QPushButton:pressed {
                background: #094771;
                padding-top: 9px;
                padding-bottom: 5px;
            }
            QPushButton:disabled {
                background: #3a3a4a;
                color: #666;
            }

            /* ========== 滑块 ========== */
            QSlider::groove:horizontal {
                background: #2a2a3e;
                height: 4px;
                border-radius: 2px;
            }
            QSlider::handle:horizontal {
                background: qradialgradient(cx:0.5, cy:0.5, radius:0.5, fx:0.5, fy:0.5,
                    stop:0 #569cd6, stop:0.7 #4a8fc8, stop:1 #3a7ab0);
                width: 16px;
                height: 16px;
                margin: -6px 0;
                border-radius: 8px;
            }
            QSlider::handle:horizontal:hover {
                background: qradialgradient(cx:0.5, cy:0.5, radius:0.5, fx:0.5, fy:0.5,
                    stop:0 #69b5ff, stop:0.7 #5a9fd8, stop:1 #4a8ac0);
                width: 18px;
                height: 18px;
                margin: -7px 0;
            }
            QSlider::sub-page:horizontal {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #0e639c, stop:1 #4a9fd8);
                border-radius: 2px;
            }

            /* ========== 标签 ========== */
            QLabel {
                color: #b0b0c0;
                font-size: 12.5px;
                background: transparent;
            }

            /* ========== 文本框 ========== */
            QTextEdit, QLineEdit {
                background: #222238;
                color: #d4d4d4;
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 6px;
                padding: 8px;
                font-size: 13px;
                selection-background-color: #264f78;
            }
            QTextEdit:hover, QLineEdit:hover { border-color: rgba(86, 156, 214, 0.4); }
            QTextEdit:focus, QLineEdit:focus {
                border-color: #569cd6;
                background: #252540;
            }
            QTextEdit { padding: 6px 8px; }
            QLineEdit { padding: 5px 10px; min-height: 24px; }

            /* ========== 滚动区域 ========== */
            QScrollArea {
                background: transparent;
                border: none;
            }
            QScrollArea > QWidget > QWidget { background: transparent; }

            /* ========== 数字输入框 ========== */
            QSpinBox {
                background: #2a2a3e;
                color: #d4d4d4;
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 6px;
                padding: 4px 8px;
                min-height: 24px;
                font-size: 13px;
            }
            QSpinBox:hover { border-color: rgba(86, 156, 214, 0.4); }
            QSpinBox:focus { border-color: #569cd6; }
            QSpinBox::up-button, QSpinBox::down-button {
                border: none;
                width: 18px;
            }

            /* ========== 复选框 ========== */
            QCheckBox {
                color: #b0b0c0;
                font-size: 12.5px;
                spacing: 8px;
                background: transparent;
            }
            QCheckBox:hover { color: #d4d4d4; }
            QCheckBox::indicator {
                width: 18px;
                height: 18px;
                background: #2a2a3e;
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 4px;
            }
            QCheckBox::indicator:hover { border-color: #569cd6; }
            QCheckBox::indicator:checked {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #0e639c, stop:1 #4a9fd8);
                border-color: #569cd6;
            }

            /* ========== 单选框 ========== */
            QRadioButton {
                color: #b0b0c0;
                font-size: 12.5px;
                spacing: 8px;
                background: transparent;
            }
            QRadioButton:hover { color: #d4d4d4; }
            QRadioButton::indicator {
                width: 18px;
                height: 18px;
                border-radius: 10px;
                background: #2a2a3e;
                border: 1px solid rgba(255, 255, 255, 0.12);
            }
            QRadioButton::indicator:hover { border-color: #569cd6; }
            QRadioButton::indicator:checked {
                background: qradialgradient(cx:0.5, cy:0.5, radius:0.4, fx:0.5, fy:0.5,
                    stop:0 #fff, stop:0.5 #569cd6, stop:1 #0e639c);
                border-color: #569cd6;
            }

            /* ========== 分割器 ========== */
            QSplitter::handle {
                background: rgba(86, 156, 214, 0.08);
                width: 2px;
                margin: 4px 0;
                border-radius: 1px;
            }
            QSplitter::handle:hover { background: rgba(86, 156, 214, 0.3); }

        """)

    # ========== 信号处理 ==========

    def _on_mode_changed(self, text: str) -> None:
        """模式下拉框变化"""
        strategy_cls = STRATEGY_OPTIONS.get(text)
        if strategy_cls is None:
            return

        strategy = strategy_cls()
        self._client.switch_strategy(strategy)
        # 用户主动切换模式时清除当前对话ID，避免覆盖其他模式的保存
        if not self._loading_conversation:
            self._current_conversation_id = None
            self._current_conversation_title = ""
        self._model_combo.setCurrentText(self._client.model)
        # 同步滑块时阻止滑块事件把预设改成"自定义"
        current_preset = self._preset_combo.currentText()
        self._preset_applying = True
        self._sync_sliders_to_client()
        # 如果当前是命名预设，直接应用预设值（setCurrentText 在文本未变时不触发信号）
        if current_preset != CUSTOM_LABEL:
            preset = PRESETS.get(current_preset)
            if preset:
                self._temp_slider.setValue(preset["temp"])
                self._top_p_slider.setValue(preset["top_p"])
                self._fp_slider.setValue(preset["fp"])
                self._mt_spin.setValue(preset["max_tokens"])
        self._preset_applying = False

        # QStackedWidget 切换面板，零布局重排
        is_novel = isinstance(strategy, NovelStrategy)
        is_role_play = isinstance(strategy, RolePlayStrategy)
        is_continuation = isinstance(strategy, ContinuationStrategy)
        if is_role_play:
            self._mode_stack.setCurrentIndex(0)
        elif is_novel:
            self._mode_stack.setCurrentIndex(1)
        elif is_continuation:
            self._mode_stack.setCurrentIndex(2)

        # 操作/对话历史面板仅在聊天模式可见
        is_chat_mode = is_role_play
        self._btn_group.setVisible(is_chat_mode)
        self._history_group.setVisible(is_chat_mode)

        # 只在进入小说/续写模式时刷新书架
        if is_novel or is_continuation:
            self._refresh_novel_bookshelf()

        if is_novel:
            self._on_book_selected(self._bookshelf_combo.currentText())
            if not self._loading_conversation:
                self._display.setHtml(md_to_html(strategy.get_welcome_message()))
        elif is_continuation:
            self._on_cont_book_selected(self._cont_bookshelf_combo.currentText())
            if not self._loading_conversation:
                self._display.setHtml(md_to_html(strategy.get_welcome_message()))
        elif not self._loading_conversation:
            self._display.setHtml(md_to_html(strategy.get_welcome_message()))

    def _on_model_changed(self, model: str) -> None:
        self._client.switch_model(model)
        self._update_status()

    def _on_temp_changed(self, value: int) -> None:
        temp = value / 100.0
        self._client.set_temperature(temp)
        self._temp_value.setText(f"{temp:.2f}")
        self._update_status()
        if not self._preset_applying and self._preset_combo.currentText() != CUSTOM_LABEL:
            self._preset_combo.setCurrentText(CUSTOM_LABEL)

    def _on_top_p_changed(self, value: int) -> None:
        top_p = value / 100.0
        self._client.set_top_p(top_p)
        self._top_p_value.setText(f"{top_p:.2f}")
        self._update_status()
        if not self._preset_applying and self._preset_combo.currentText() != CUSTOM_LABEL:
            self._preset_combo.setCurrentText(CUSTOM_LABEL)

    def _on_fp_changed(self, value: int) -> None:
        fp = value / 100.0
        self._client.set_frequency_penalty(fp)
        self._fp_value.setText(f"{fp:.2f}")
        self._update_status()
        if not self._preset_applying and self._preset_combo.currentText() != CUSTOM_LABEL:
            self._preset_combo.setCurrentText(CUSTOM_LABEL)

    def _on_mt_changed(self, value: int) -> None:
        self._client.set_max_tokens(value)
        self._update_status()
        if not self._preset_applying and self._preset_combo.currentText() != CUSTOM_LABEL:
            self._preset_combo.setCurrentText(CUSTOM_LABEL)

    def _on_preset_changed(self, text: str) -> None:
        """参数预设下拉框切换时应用预设值。"""
        if text == CUSTOM_LABEL:
            return
        preset = PRESETS.get(text)
        if preset is None:
            return
        self._preset_applying = True
        self._temp_slider.setValue(preset["temp"])
        self._top_p_slider.setValue(preset["top_p"])
        self._fp_slider.setValue(preset["fp"])
        self._mt_spin.setValue(preset["max_tokens"])
        self._preset_applying = False
        self._update_status()

    def _sync_sliders_to_client(self) -> None:
        self._temp_slider.setValue(int(self._client.temperature * 100))
        self._temp_value.setText(f"{self._client.temperature:.2f}")
        self._top_p_slider.setValue(int(self._client.top_p * 100))
        self._top_p_value.setText(f"{self._client.top_p:.2f}")
        self._fp_slider.setValue(int(self._client.frequency_penalty * 100))
        self._fp_value.setText(f"{self._client.frequency_penalty:.2f}")
        self._mt_spin.setValue(self._client.max_tokens)

    def _on_clear(self) -> None:
        self._client.clear_context()
        self._display.setHtml(INITIAL_HTML)

    def _update_status(self) -> None:
        self._status_label.setText(
            f"模式: {self._client.strategy.get_name()}\n"
            f"模型: {self._client.model}\n"
            f"温度: {self._client.temperature:.2f} | "
            f"top_p: {self._client.top_p:.2f}\n"
            f"freq_p: {self._client.frequency_penalty:.2f} | "
            f"max_tk: {self._client.max_tokens}"
        )

    # ========== 🎭 角色扮演面板事件 ==========

    def _on_role_char_changed(self) -> None:
        if isinstance(self._client.strategy, RolePlayStrategy):
            self._client.strategy.character_description = self._role_char_edit.toPlainText()

    def _on_role_bg_changed(self) -> None:
        if isinstance(self._client.strategy, RolePlayStrategy):
            self._client.strategy.story_background = self._role_bg_edit.toPlainText()

    def _on_reply_mode_changed(self, button_id: int) -> None:
        """切换回复方式时立即更新 system prompt，不重置对话"""
        if isinstance(self._client.strategy, RolePlayStrategy):
            self._client.strategy.reply_mode = (
                RolePlayStrategy.REPLY_MODE_NARRATOR
                if button_id == 1
                else RolePlayStrategy.REPLY_MODE_CHARACTER
            )
            self._client.update_system_prompt()

    def _on_apply_role_settings(self) -> None:
        """将角色描述、故事背景、回复方式写入 system prompt，不重置对话"""
        if not isinstance(self._client.strategy, RolePlayStrategy):
            return
        self._client.strategy.character_description = self._role_char_edit.toPlainText()
        self._client.strategy.story_background = self._role_bg_edit.toPlainText()
        self._client.strategy.reply_mode = (
            RolePlayStrategy.REPLY_MODE_NARRATOR
            if self._radio_narrator.isChecked()
            else RolePlayStrategy.REPLY_MODE_CHARACTER
        )
        self._client.update_system_prompt()
        char = self._client.strategy.character_description.strip()
        bg = self._client.strategy.story_background.strip()
        is_narrator = self._client.strategy.reply_mode == RolePlayStrategy.REPLY_MODE_NARRATOR
        mode_text = "旁白描述（第三人称）" if is_narrator else "角色回答（第一人称）"
        notice_parts = [f"🎭 **角色设定已更新。**\n", f"**回复方式：** {mode_text}\n"]
        if char:
            notice_parts.append(f"**角色描述：** {char[:80]}{'…' if len(char) > 80 else ''}\n")
        if bg:
            notice_parts.append(f"**故事背景：** {bg[:80]}{'…' if len(bg) > 80 else ''}\n")
        if not char and not bg:
            notice_parts.append("（未填写角色描述或故事背景，使用默认角色扮演模式）\n")
        notice_parts.append("\n对话历史已保留，可以继续对话。")

        # 在现有显示底部追加通知，不清屏
        notice_html = md_lib.markdown(
            "".join(notice_parts),
            extensions=["fenced_code", "tables", "codehilite", "nl2br", "sane_lists"],
        )
        escaped = self._escape_for_js(notice_html)
        self._display.page().runJavaScript(f"""
            (function() {{
                var div = document.createElement('div');
                div.className = 'system-msg';
                div.innerHTML = `{escaped}`;
                document.body.appendChild(div);
                window.scrollTo(0, document.body.scrollHeight);
            }})();
        """)

    # ========== 📚 小说面板事件 ==========

    def _refresh_novel_bookshelf(self) -> None:
        """刷新书架下拉列表（按最近编辑时间排序，同时更新续写面板）"""
        books = self._novel_manager.list_books()
        books.sort(
            key=lambda t: self._novel_manager.load_meta(t).updated_at or "",
            reverse=True,
        )
        current = self._bookshelf_combo.currentText()
        self._bookshelf_combo.blockSignals(True)
        self._bookshelf_combo.clear()
        if books:
            self._bookshelf_combo.addItems(books)
            if current in books:
                self._bookshelf_combo.setCurrentText(current)
        else:
            self._bookshelf_combo.addItem("（暂无小说，请新建）")
        self._bookshelf_combo.blockSignals(False)

        cont_current = self._cont_bookshelf_combo.currentText()
        self._cont_bookshelf_combo.blockSignals(True)
        self._cont_bookshelf_combo.clear()
        if books:
            self._cont_bookshelf_combo.addItems(books)
            if cont_current in books:
                self._cont_bookshelf_combo.setCurrentText(cont_current)
        else:
            self._cont_bookshelf_combo.addItem("（暂无小说，请新建）")
        self._cont_bookshelf_combo.blockSignals(False)

    def _on_create_book(self) -> None:
        """新建小说"""
        title, ok = QInputDialog.getText(
            self, "新建小说", "请输入小说标题："
        )
        if ok and title.strip():
            existing = self._novel_manager.list_books()
            if title.strip() in existing:
                QMessageBox.warning(self, "警告", f"小说「{title.strip()}」已存在。")
                return
            self._novel_manager.create_book(title.strip())
            self._refresh_novel_bookshelf()
            self._bookshelf_combo.setCurrentText(title.strip())

    def _on_delete_book(self) -> None:
        """删除选中的小说"""
        current = self._get_current_book_title()
        if not current:
            return
        reply = QMessageBox.question(
            self,
            "确认删除",
            f"确定要删除小说「{current}」及其所有章节吗？\n此操作不可恢复！",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._novel_manager.delete_book(current)
            self._refresh_novel_bookshelf()
            self._on_book_selected(self._bookshelf_combo.currentText())

    def _get_current_book_title(self) -> str | None:
        """获取当前活动面板的书架选中项，若为占位符则返回 None"""
        combo = self._cont_bookshelf_combo if self._continuation_panel.isVisible() else self._bookshelf_combo
        text = combo.currentText()
        if not text or text.startswith("（暂无小说"):
            return None
        return text

    def _on_book_selected(self, text: str) -> None:
        """书架选择变化 → 加载已有小说设定"""
        title = text if text and not text.startswith("（暂无小说") else None
        if not title:
            self._novel_title_edit.setText("")
            self._chapter_info_label.setText("尚未选择小说")
            return

        self._novel_title_edit.setText(title)
        meta = self._novel_manager.load_meta(title)
        self._protagonist_edit.blockSignals(True)
        self._protagonist_edit.setPlainText(meta.protagonist_bio)
        self._protagonist_edit.blockSignals(False)
        self._background_edit.blockSignals(True)
        self._background_edit.setPlainText(meta.background_story)
        self._background_edit.blockSignals(False)
        self._demand_edit.blockSignals(True)
        self._demand_edit.setPlainText(meta.writing_demand)
        self._demand_edit.blockSignals(False)
        next_ch = self._novel_manager.get_next_chapter_num(title)
        chapters = self._novel_manager.list_chapters(title)
        self._chapter_info_label.setText(
            f"已有 {len(chapters)} 章，下一章编号: 第{next_ch}章"
        )

        # 同步到策略
        if isinstance(self._client.strategy, NovelStrategy):
            self._client.strategy.novel_title = title
            self._client.strategy.protagonist_bio = meta.protagonist_bio
            self._client.strategy.background_story = meta.background_story
            self._client.strategy.writing_demand = meta.writing_demand

    def _on_novel_title_changed(self, text: str) -> None:
        if isinstance(self._client.strategy, NovelStrategy):
            self._client.strategy.novel_title = text.strip()

    def _on_chapter_title_changed(self, text: str) -> None:
        if isinstance(self._client.strategy, NovelStrategy):
            self._client.strategy.chapter_title = text.strip()

    def _auto_save_novel_settings(self) -> None:
        """自动保存当前小说的设定到 meta.json"""
        title = self._novel_title_edit.text().strip()
        if not title or title.startswith("（暂无小说"):
            return
        # 确保小说目录存在
        self._novel_manager.create_book(title)
        self._novel_manager.save_meta(
            title,
            protagonist_bio=self._protagonist_edit.toPlainText().strip(),
            background_story=self._background_edit.toPlainText().strip(),
            writing_demand=self._demand_edit.toPlainText().strip(),
        )

    def _on_protagonist_changed(self) -> None:
        if isinstance(self._client.strategy, NovelStrategy):
            self._client.strategy.protagonist_bio = self._protagonist_edit.toPlainText().strip()
            self._auto_save_novel_settings()

    def _on_background_changed(self) -> None:
        if isinstance(self._client.strategy, NovelStrategy):
            self._client.strategy.background_story = self._background_edit.toPlainText().strip()
            self._auto_save_novel_settings()

    def _on_demand_changed(self) -> None:
        if isinstance(self._client.strategy, NovelStrategy):
            self._client.strategy.writing_demand = self._demand_edit.toPlainText().strip()
            self._auto_save_novel_settings()

    def _on_chapter_mode_toggled(self, checked: bool) -> None:
        if isinstance(self._client.strategy, NovelStrategy):
            self._client.strategy.chapter_mode = checked
            self._update_status()
            if checked:
                self._append_user_message(
                    "📖 **章节续写模式已开启** — 发送消息将自动根据设定生成新章节"
                )
            else:
                self._append_user_message(
                    "💬 **自由对话模式** — 可随意交流写作问题"
                )

    def _on_edit_global_prompt(self) -> None:
        """打开编辑对话框，编辑用户全局提示词"""
        from PyQt6.QtWidgets import QDialog, QVBoxLayout, QTextEdit, QHBoxLayout, QPushButton

        dialog = QDialog(self)
        dialog.setWindowTitle("编辑全局偏好提示词")
        dialog.resize(550, 350)

        layout = QVBoxLayout(dialog)
        hint = QLabel(
            "此处的内容将自动注入所有章节生成、续写、重新生成、字数补充、\n"
            "摘要生成、世界书提取和方向建议等请求。可用于表达您的写作偏好。"
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)

        edit = QTextEdit()
        edit.setPlainText(self._client.global_user_prompt)
        edit.setPlaceholderText(
            "在此填写您的写作偏好、习惯风格、常用要求等。\n"
            "例如：我喜欢细腻的环境描写，对话要自然，每章结尾留悬念。"
        )
        layout.addWidget(edit)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(dialog.reject)
        btn_layout.addWidget(cancel_btn)

        ok_btn = QPushButton("确定")
        ok_btn.setDefault(True)
        ok_btn.clicked.connect(dialog.accept)
        btn_layout.addWidget(ok_btn)

        layout.addLayout(btn_layout)

        if dialog.exec() == QDialog.DialogCode.Accepted:
            value = edit.toPlainText()
            self._client.global_user_prompt = value
            self._save_global_user_prompt(value)

    # ========== 📄 续写面板 Handler（新增） ==========

    def _on_cont_book_selected(self, text: str) -> None:
        """续写面板：书架选择变化 → 加载已有小说设定"""
        title = text if text and not text.startswith("（暂无小说") else None
        if not title:
            self._cont_chapter_info_label.setText("尚未选择小说")
            self._cont_protagonist_edit.clear()
            self._cont_background_edit.clear()
            self._cont_demand_edit.clear()
            return

        meta = self._novel_manager.load_meta(title)
        self._cont_protagonist_edit.blockSignals(True)
        self._cont_protagonist_edit.setPlainText(meta.protagonist_bio)
        self._cont_protagonist_edit.blockSignals(False)
        self._cont_background_edit.blockSignals(True)
        self._cont_background_edit.setPlainText(meta.background_story)
        self._cont_background_edit.blockSignals(False)
        self._cont_demand_edit.blockSignals(True)
        self._cont_demand_edit.setPlainText(meta.writing_demand)
        self._cont_demand_edit.blockSignals(False)

        # 同步分析上下文，使左侧面板的 AI 建议 / 我指定剧情按钮可用
        self._cont_analysis_settings = {
            "background_story": meta.background_story or "",
            "protagonist_bio": meta.protagonist_bio or "",
            "writing_demand": meta.writing_demand or "",
        }
        wb = self._novel_manager.load_world_bible(title)
        self._cont_analysis_world_data = {
            "characters": [{"name": c.name, "traits": c.traits} for c in wb.characters],
            "locations": [{"name": l.name, "description": l.description} for l in wb.locations],
            "rules": list(wb.rules),
            "timeline": [{"event": t.event, "significance": t.significance} for t in wb.timeline],
            "plot_threads": [{"name": p.name, "status": p.status, "description": p.description} for p in wb.active_plot_threads],
            "global_foreshadowing": list(wb.global_foreshadowing),
            "key_worldbuilding": list(wb.key_worldbuilding_passages),
        }

        next_ch = self._novel_manager.get_next_chapter_num(title)
        chapters = self._novel_manager.list_chapters(title)
        self._cont_chapter_info_label.setText(
            f"已有 {len(chapters)} 章，下一章编号: 第{next_ch}章"
        )

    def _on_cont_create_book(self) -> None:
        """续写面板：新建小说"""
        from PyQt6.QtWidgets import QInputDialog
        title, ok = QInputDialog.getText(self, "新建小说", "请输入小说标题：")
        if ok and title.strip():
            self._novel_manager.create_book(title.strip())
            self._refresh_novel_bookshelf()
            self._cont_bookshelf_combo.setCurrentText(title.strip())

    def _on_cont_delete_book(self) -> None:
        """续写面板：删除小说"""
        title = self._cont_bookshelf_combo.currentText()
        if not title or title.startswith("（暂无小说"):
            return
        reply = QMessageBox.question(
            self, "确认删除",
            f"确定要删除「{title}」及其所有章节吗？\n此操作不可恢复！",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._novel_manager.delete_book(title)
            self._refresh_novel_bookshelf()
            self._on_cont_book_selected(self._cont_bookshelf_combo.currentText())

    def _on_cont_protagonist_changed(self) -> None:
        """续写面板：主角设定变更 → 自动保存"""
        title = self._get_current_book_title()
        if title:
            self._novel_manager.save_meta(
                title, protagonist_bio=self._cont_protagonist_edit.toPlainText().strip()
            )

    def _on_cont_background_changed(self) -> None:
        """续写面板：背景变更 → 自动保存"""
        title = self._get_current_book_title()
        if title:
            self._novel_manager.save_meta(
                title, background_story=self._cont_background_edit.toPlainText().strip()
            )

    def _on_cont_demand_changed(self) -> None:
        """续写面板：写作要求变更 → 自动保存"""
        title = self._get_current_book_title()
        if title:
            self._novel_manager.save_meta(
                title, writing_demand=self._cont_demand_edit.toPlainText().strip()
            )

    def _on_cont_chapter_mode_toggled(self, checked: bool) -> None:
        """续写面板：章节模式切换"""
        if checked:
            self._append_user_message(
                "📖 **章节续写模式已开启** — 发送消息将自动续写下一章"
            )
        else:
            self._append_user_message(
                "💬 **自由对话模式** — 可随意交流写作问题"
            )

    def _on_cont_save_settings(self) -> None:
        """续写面板：保存设定到 meta.json"""
        title = self._get_current_book_title()
        if not title:
            QMessageBox.warning(self, "提示", "请先在书架中选择一部小说。")
            return
        self._novel_manager.save_meta(
            title,
            protagonist_bio=self._cont_protagonist_edit.toPlainText().strip(),
            background_story=self._cont_background_edit.toPlainText().strip(),
            writing_demand=self._cont_demand_edit.toPlainText().strip(),
        )
        QMessageBox.information(self, "成功", f"「{title}」的设定已保存。")

    def _on_cont_load_settings(self) -> None:
        """续写面板：加载设定到编辑框"""
        title = self._get_current_book_title()
        if not title:
            QMessageBox.warning(self, "提示", "请先在书架中选择一部小说。")
            return
        meta = self._novel_manager.load_meta(title)
        self._cont_protagonist_edit.blockSignals(True)
        self._cont_protagonist_edit.setPlainText(meta.protagonist_bio)
        self._cont_protagonist_edit.blockSignals(False)
        self._cont_background_edit.blockSignals(True)
        self._cont_background_edit.setPlainText(meta.background_story)
        self._cont_background_edit.blockSignals(False)
        self._cont_demand_edit.blockSignals(True)
        self._cont_demand_edit.setPlainText(meta.writing_demand)
        self._cont_demand_edit.blockSignals(False)

    def _on_cont_generate_chapter(self) -> None:
        """续写面板：生成下一章"""
        if self._streaming:
            return

        book_title = self._get_current_book_title()
        if not book_title:
            QMessageBox.warning(self, "提示", "请先在书架中选择一部小说。")
            return

        # 优先使用缓存（分析时保存的源文本），其次重新读取文件
        source_text = getattr(self, '_cont_analysis_source', "")
        if not source_text:
            source_text = self._read_continuation_source()
            if source_text:
                self._cont_analysis_source = source_text
        # source_text 为空时仍可续写，仅 prompt 中不添加【原文内容】区块

        chapter_title = self._cont_chapter_title_edit.text().strip()
        if not chapter_title:
            chapter_title = f"续写 (第{self._novel_manager.get_next_chapter_num(book_title)}章)"
            self._cont_chapter_title_edit.setText(chapter_title)

        requirement = self._continue_requirement.toPlainText().strip()
        word_count = self._continue_word_count.value()
        plot = self._continue_plot.toPlainText().strip()
        chapter_num = self._novel_manager.get_next_chapter_num(book_title)

        self._streaming = True
        self._assistant_text_buffer = []
        self._append_user_message(f"📝 续写「{book_title}」→ 第{chapter_num}章「{chapter_title}」")

        threading.Thread(
            target=self._run_continuation,
            args=(book_title, chapter_num, chapter_title, source_text, requirement, word_count, plot),
            daemon=True,
        ).start()

    def _on_cont_novel_imported(self, title: str) -> None:
        """续写面板：导入完成后刷新书架并加载设定到 UI"""
        self._refresh_novel_bookshelf()
        self._cont_bookshelf_combo.setCurrentText(title)
        self._on_cont_book_selected(title)

    def _read_continuation_source(self) -> str:
        """读取续写源文档/文件夹内容"""
        source_file = self._continue_file_path.text().strip()
        source_folder = self._continue_folder_path.text().strip()

        if source_file:
            if not os.path.isfile(source_file):
                return ""
            for enc in ("utf-8", "gbk"):
                try:
                    with open(source_file, "r", encoding=enc) as f:
                        return f.read()
                except UnicodeDecodeError:
                    continue
            return ""
        elif source_folder:
            if not os.path.isdir(source_folder):
                return ""
            ext_map = {".txt", ".md", ".html", ".htm"}
            files = sorted(f for f in os.listdir(source_folder) if os.path.splitext(f)[1].lower() in ext_map)
            if not files:
                return ""
            parts = []
            for fname in files:
                fpath = os.path.join(source_folder, fname)
                content = ""
                for enc in ("utf-8", "gbk"):
                    try:
                        with open(fpath, "r", encoding=enc) as f:
                            content = f.read()
                        break
                    except UnicodeDecodeError:
                        continue
                if not content:
                    continue  # 跳过无法读取的文件
                parts.append(f"===== {fname} =====\n{content}")
            return "\n\n".join(parts)
        return ""

    def _on_save_novel_settings(self) -> None:
        """保存当前小说设定到 meta.json"""
        title = self._novel_title_edit.text().strip()
        if not title:
            QMessageBox.warning(self, "提示", "请先设置小说标题。")
            return
        self._novel_manager.create_book(title)
        self._novel_manager.save_meta(
            title,
            protagonist_bio=self._protagonist_edit.toPlainText().strip(),
            background_story=self._background_edit.toPlainText().strip(),
            writing_demand=self._demand_edit.toPlainText().strip(),
        )
        self._refresh_novel_bookshelf()
        self._bookshelf_combo.setCurrentText(title)
        if isinstance(self._client.strategy, NovelStrategy):
            self._client.strategy.novel_title = title
            self._client.strategy.protagonist_bio = self._protagonist_edit.toPlainText().strip()
            self._client.strategy.background_story = self._background_edit.toPlainText().strip()
            self._client.strategy.writing_demand = self._demand_edit.toPlainText().strip()
        QMessageBox.information(self, "成功", f"小说「{title}」的设定已保存。")

    def _on_load_novel_settings(self) -> None:
        """加载当前选中小说的设定到编辑框"""
        title = self._get_current_book_title()
        if not title:
            QMessageBox.warning(self, "提示", "请先在书架中选择一部小说。")
            return
        meta = self._novel_manager.load_meta(title)
        self._novel_title_edit.setText(meta.title)
        self._protagonist_edit.blockSignals(True)
        self._protagonist_edit.setPlainText(meta.protagonist_bio)
        self._protagonist_edit.blockSignals(False)
        self._background_edit.blockSignals(True)
        self._background_edit.setPlainText(meta.background_story)
        self._background_edit.blockSignals(False)
        self._demand_edit.blockSignals(True)
        self._demand_edit.setPlainText(meta.writing_demand)
        self._demand_edit.blockSignals(False)
        self._on_book_selected(title)

    # ========== 🚀 生成章节 ==========

    def _on_generate_chapter(self) -> None:
        """生成下一章 → 完整工作流"""
        if self._streaming:
            return

        title = self._novel_title_edit.text().strip()
        chapter_title = self._chapter_title_edit.text().strip()
        if not title:
            QMessageBox.warning(self, "提示", "请先设置小说标题。")
            return
        if not chapter_title:
            chapter_title = f"第{self._novel_manager.get_next_chapter_num(title)}章"
            self._chapter_title_edit.setText(chapter_title)

        self._streaming = True
        self._assistant_text_buffer = []

        # 同步 UI 值到策略对象
        if isinstance(self._client.strategy, NovelStrategy):
            self._client.strategy.chapter_title = chapter_title
            self._client.strategy.novel_title = title
            self._client.strategy.protagonist_bio = self._protagonist_edit.toPlainText().strip()
            self._client.strategy.background_story = self._background_edit.toPlainText().strip()
            self._client.strategy.writing_demand = self._demand_edit.toPlainText().strip()

        self._append_user_message(f"📖 生成第{self._novel_manager.get_next_chapter_num(title)}章：{chapter_title}")

        # 在主线程中捕获 UI 值，避免后台线程访问 QWidget
        plot_content = self._plot_edit.toPlainText().strip()
        target_words = self._chapter_word_count.value()

        threading.Thread(
            target=self._run_chapter_generation,
            args=(title, chapter_title, plot_content, target_words),
            daemon=True,
        ).start()

    def _build_chapter_prompt(self, title: str, chapter_title: str, plot_content: str = "") -> str:
        """构造章节续写的完整 User Prompt（含历史记录参考）"""
        chapter_num = self._novel_manager.get_next_chapter_num(title)

        # 智能前情提要（剧情摘要）
        client = self._client.raw_client if hasattr(self, '_client') else None
        summary = self._novel_manager.load_smart_summary(
            title,
            client=client,
            next_chapter_num=chapter_num,
            max_recent=3,
            global_user_prompt=self._client.global_user_prompt,
        )

        # 历史记录总结（前面各章的生成配置与风格参考）
        history = self._novel_manager.build_history_summary(title, exclude_chapter=chapter_num)

        # 从策略对象读取（已在主线程中同步完毕），避免后台线程访问 QWidget
        strategy = self._client.strategy
        if isinstance(strategy, NovelStrategy):
            bio = strategy.protagonist_bio
            bg = strategy.background_story
            demand = strategy.writing_demand
        else:
            bio = bg = demand = ""
        # plot_content 在 _on_generate_chapter 中捕获后传入

        # 注入世界书信息
        world_bible_text = ""
        try:
            bible = self._novel_manager.load_world_bible(title)
            if bible and (bible.characters or bible.locations or bible.rules or bible.active_plot_threads):
                from core.world_bible import format_world_bible_for_prompt
                world_bible_text = format_world_bible_for_prompt(bible)
        except Exception:
            pass

        parts = [f"【前情提要】：\n{summary}\n"]
        if world_bible_text:
            parts.append(f"【世界书（已建立设定库）】：\n{world_bible_text}\n")
        if history and history != "暂无历史记录。" and history != "暂无历史记录（排除当前章节后）。":
            parts.append(f"【历史生成记录参考（前面各章风格/配置）】：\n{history}\n")
        parts.append(f"现在请开始撰写第 {chapter_num} 章：{chapter_title}。\n")
        if bio:
            parts.append(f"【人物设定参考】：\n{bio}\n")
        if bg:
            parts.append(f"【世界观/背景参考】：\n{bg}\n")
        if demand:
            parts.append(f"【本章要求】：\n{demand}\n")
        if plot_content:
            parts.append(f"【本章已定情节（请严格据此扩展）】\n{plot_content}\n")

        global_prompt = self._client.global_user_prompt
        if global_prompt.strip():
            parts.append(f"【用户偏好提示】: \n{global_prompt}\n")

        return "\n".join(parts)

    def _run_chapter_generation(self, title: str, chapter_title: str,
                                 plot_content: str = "", target_words: int = 2000) -> None:
        """后台线程：生成章节 + 版本保存 + 摘要"""
        try:
            chapter_num = self._novel_manager.get_next_chapter_num(title)

            strategy = self._client.strategy
            main_sys = (
                "你是一位文笔细腻、想象力丰富的长篇小说作家。直接输出小说正文，"
                "绝对不要添加任何解释、前言、章节概括或作者的话。"
                f"本章字数不少于{target_words}字，要求情节饱满、细节丰富、场景描写生动。"
                "开头用悬念或场景快速切入，结尾适当留悬念。"
                "严格按照用户提供的【核心设定】和【人物背景】保持一致性。"
            )
            messages = [{"role": "system", "content": main_sys}]
            if isinstance(strategy, NovelStrategy):
                messages += strategy.build_system_messages()

            user_prompt = self._build_chapter_prompt(title, chapter_title, plot_content=plot_content)
            messages.append({"role": "user", "content": user_prompt})

            self._stream_signals.token.emit(f"\n\n📝 正在创作第 {chapter_num} 章「{chapter_title}」...\n\n")

            response = self._client.raw_client.chat.completions.create(
                model=self._client.model,
                messages=messages,
                temperature=self._client.temperature,
                top_p=self._client.top_p,
                max_tokens=self._client.max_tokens,
                frequency_penalty=self._client.frequency_penalty,
                stream=False,
            )
            content = response.choices[0].message.content or ""

            self._stream_signals.token.emit(content)
            self._stream_signals.token.emit("\n\n---\n")

            # 确定版本号
            existing_versions = self._novel_manager.get_chapter_versions(title, chapter_num)
            if existing_versions:
                version = self._novel_manager.get_next_version(title, chapter_num)
                old_active = self._novel_manager.get_active_version(title, chapter_num)
                new_chapter = False
            else:
                version = 1
                old_active = None
                new_chapter = True

            file_path, saved_version = self._novel_manager.save_chapter_version(
                title, chapter_num, chapter_title, content, version=version
            )
            self._stream_signals.token.emit(f"✅ 已保存版本 v{saved_version} → `{file_path}`\n")

            if not new_chapter and old_active is not None:
                self._stream_signals.token.emit(
                    f"\n⚡ 该章节已有旧版本 v{old_active}。请点击右侧「⚙ 章节管理」按钮选择使用哪个版本。\n"
                )

            # 保存生成历史记录（含已定情节，供重新生成时还原）
            content_preview = content.replace("\n", " ")
            self._novel_manager.save_generation_record(
                title=title,
                chapter_num=chapter_num,
                chapter_title=chapter_title,
                version=saved_version,
                prompt=user_prompt,
                model=self._client.model,
                temperature=self._client.temperature,
                top_p=self._client.top_p,
                max_tokens=self._client.max_tokens,
                frequency_penalty=self._client.frequency_penalty,
                content_preview=content_preview,
                plot=plot_content,
            )

            if new_chapter:
                self._stream_signals.token.emit("\n🔍 正在提炼剧情记忆...\n")
                summary = self._novel_manager.generate_summary(
                    self._client.raw_client, content, chapter_num, chapter_title,
                    global_user_prompt=self._client.global_user_prompt
                )
                self._novel_manager.append_summary(
                    title, chapter_num, chapter_title, summary
                )
                self._stream_signals.token.emit(f"📋 剧情摘要已同步至记忆库。\n\n")

                # 字数补充检查
                from utils.supplement import count_cn, supplement_content
                actual_words = count_cn(content)
                target_chars = target_words
                if actual_words < target_chars * 0.8 and actual_words > 0:
                    self._stream_signals.token.emit(f"\n📝 当前{actual_words}字，目标{target_chars}字，正在进行补充...\n")
                    try:
                        supplemented = supplement_content(
                            self._client.raw_client, content, target_chars, actual_words,
                            chapter_title, self._client.model, self._client.temperature,
                            global_user_prompt=self._client.global_user_prompt
                        )
                        if supplemented:
                            content = content + "\n\n" + supplemented
                            file_path, saved_version = self._novel_manager.save_chapter_version(
                                title, chapter_num, chapter_title, content, version=saved_version
                            )
                            final_words = count_cn(content)
                            self._stream_signals.token.emit(f"✅ 补充完成，总字数：{final_words}字 (v{saved_version})\n")
                    except Exception as supp_e:
                        self._stream_signals.token.emit(f"⚠️ 补充过程出错: {supp_e}\n")

            # 更新世界书
            self._stream_signals.token.emit("\n📖 正在更新世界书...\n")
            try:
                from core.world_bible import extract_and_merge_world_bible
                bible = self._novel_manager.load_world_bible(title)
                updated_bible = extract_and_merge_world_bible(
                    self._client.raw_client, content, chapter_num, bible, self._client.model,
                    global_user_prompt=self._client.global_user_prompt
                )
                self._novel_manager.save_world_bible(title, updated_bible)
                self._stream_signals.token.emit("✅ 世界书已更新。\n")
            except Exception as wb_e:
                self._stream_signals.token.emit(f"⚠️ 世界书更新跳过: {wb_e}\n")

            self._refresh_chapter_info_display(title)
            next_ch = self._novel_manager.get_next_chapter_num(title)

            self._stream_signals.token.emit(
                f"📖 下一章：第{next_ch}章（请修改章节标题后再次生成）\n"
            )

            self._stream_signals.finished.emit()
        except Exception as e:
            self._stream_signals.error.emit(f"章节生成失败: {e}")

    # ========== 📄 续写小说事件 ==========

    def _on_browse_continue_file(self) -> None:
        """选择续写源文档"""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "选择续写源文档",
            "",
            "文本文件 (*.txt *.md *.html *.htm);;所有文件 (*.*)",
        )
        if file_path:
            self._continue_file_path.setText(file_path)
            # 同时清除文件夹选择
            self._continue_folder_path.clear()

    def _on_browse_continue_folder(self) -> None:
        """选择续写源文件夹"""
        folder_path = QFileDialog.getExistingDirectory(
            self,
            "选择包含源文档的文件夹",
        )
        if folder_path:
            self._continue_folder_path.setText(folder_path)
            # 同时清除文件选择
            self._continue_file_path.clear()

    def _on_start_continuation(self) -> None:
        """开始续写：读取源文档 → 调用 API → 保存为章节"""
        if self._streaming:
            return

        # ── 收集参数 ──
        source_file = self._continue_file_path.text().strip()
        source_folder = self._continue_folder_path.text().strip()
        requirement = self._continue_requirement.toPlainText().strip()
        word_count = self._continue_word_count.value()
        plot = self._continue_plot.toPlainText().strip()

        # 确定源文档内容
        source_text = ""
        if source_file:
            if not os.path.isfile(source_file):
                QMessageBox.warning(self, "错误", f"文件不存在：{source_file}")
                return
            try:
                with open(source_file, "r", encoding="utf-8") as f:
                    source_text = f.read()
            except UnicodeDecodeError:
                try:
                    with open(source_file, "r", encoding="gbk") as f:
                        source_text = f.read()
                except Exception as e:
                    QMessageBox.warning(self, "错误", f"无法读取文件：{e}")
                    return
            except Exception as e:
                QMessageBox.warning(self, "错误", f"无法读取文件：{e}")
                return
        elif source_folder:
            if not os.path.isdir(source_folder):
                QMessageBox.warning(self, "错误", f"文件夹不存在：{source_folder}")
                return
            # 读取文件夹下所有 .txt/.md 文件，按名称排序后拼接
            ext_map = {".txt", ".md", ".html", ".htm"}
            files = sorted(
                f for f in os.listdir(source_folder)
                if os.path.splitext(f)[1].lower() in ext_map
            )
            if not files:
                QMessageBox.warning(self, "提示", f"文件夹「{source_folder}」中没有找到文本文件。")
                return
            parts = []
            for fname in files:
                fpath = os.path.join(source_folder, fname)
                try:
                    with open(fpath, "r", encoding="utf-8") as f:
                        content = f.read()
                except UnicodeDecodeError:
                    try:
                        with open(fpath, "r", encoding="gbk") as f:
                            content = f.read()
                    except Exception:
                        continue  # 跳过无法读取的文件
                except Exception:
                    continue  # 跳过无法读取的文件
                parts.append(f"===== {fname} =====\n{content}")
            source_text = "\n\n".join(parts)
        else:
            QMessageBox.warning(self, "提示", "请先选择续写源文档或文件夹。")
            return

        if not source_text.strip():
            QMessageBox.warning(self, "提示", "源文档内容为空，无法续写。")
            return

        # 检查当前选择的书是否已有章节
        book_title = self._get_current_book_title()
        if book_title and not self._check_book_empty(book_title):
            return

        # ── 段落预览弹窗 ──
        client = self._client.raw_client if hasattr(self, '_client') else None
        dlg = SectionPreviewDialog(
            self,
            source_text=source_text if not source_folder else None,
            folder_path=source_folder,
            client=client, model=self._client.model,
            global_user_prompt=self._client.global_user_prompt,
            mode="continue",
        )
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        result = dlg.get_result()
        if result:
            if result["mode"] == "file" and result.get("sections"):
                source_text = "\n\n".join(
                    f"# {t}\n{c}" for t, c in result["sections"]
                )
            elif result["mode"] == "folder" and result.get("files"):
                parts = []
                for f in result["files"]:
                    parts.append(f"===== {f['filename']} =====\n{f['full_content']}")
                source_text = "\n\n".join(parts)

        # ── 确定目标书架 ──
        book_title = self._get_current_book_title()
        if not book_title:
            # 以文件名（不含后缀）作为小说名
            if source_file:
                book_title = os.path.splitext(os.path.basename(source_file))[0]
            elif source_folder:
                book_title = os.path.basename(source_folder)
            if not book_title:
                book_title = "续写作品"
            self._novel_manager.create_book(book_title)
            # 保存当前编辑器的设定内容到新书的 meta（防止 setCurrentText 触发信号清空）
            self._novel_manager.save_meta(
                book_title,
                protagonist_bio=self._cont_protagonist_edit.toPlainText().strip(),
                background_story=self._cont_background_edit.toPlainText().strip(),
                writing_demand=self._cont_demand_edit.toPlainText().strip(),
            )
            self._refresh_novel_bookshelf()
            self._cont_bookshelf_combo.setCurrentText(book_title)

        chapter_num = self._novel_manager.get_next_chapter_num(book_title)
        chapter_title = self._cont_chapter_title_edit.text().strip()
        if not chapter_title:
            chapter_title = f"续写 (第{chapter_num}章)"

        self._streaming = True
        self._assistant_text_buffer = []

        notice = f"续写「{os.path.basename(source_file) if source_file else os.path.basename(source_folder)}」→ 第{chapter_num}章"
        self._append_user_message(notice)

        threading.Thread(
            target=self._run_continuation,
            args=(book_title, chapter_num, chapter_title, source_text, requirement, word_count, plot),
            daemon=True,
        ).start()

    def _run_continuation(
        self,
        book_title: str,
        chapter_num: int,
        chapter_title: str,
        source_text: str,
        requirement: str,
        word_count: int,
        plot: str,
        setting: str = "",
    ) -> None:
        """后台线程：执行续写（增强版：含世界书+剧情摘要+设定）"""
        try:
            # ── 构建 User Prompt（含前情提要 + 世界书 + 设定） ──
            user_parts = []
            if source_text:
                user_parts.append(f"【原文内容】\n{source_text}\n")

            # 加载前情提要（复用小说模式的智能摘要算法）
            try:
                summary = self._novel_manager.load_smart_summary(
                    book_title, self._client.raw_client,
                    next_chapter_num=chapter_num,
                    model=self._client.model,
                    global_user_prompt=self._client.global_user_prompt,
                )
                if summary and "故事刚刚开始" not in summary:
                    user_parts.append(f"【前情提要】\n{summary}\n")
            except Exception:
                pass

            # 加载世界书
            try:
                from core.world_bible import format_world_bible_for_prompt
                bible = self._novel_manager.load_world_bible(book_title)
                if bible:
                    wb_text = format_world_bible_for_prompt(bible)
                    if wb_text.strip():
                        user_parts.append(f"【世界书（已建立设定库）】\n{wb_text}\n")
            except Exception:
                pass

            # 加载小说设定（优先使用 analysis 传入的 setting，否则读 meta.json）
            bg_story = setting
            protagonist_bio = ""
            try:
                meta = self._novel_manager.load_meta(book_title)
                if not bg_story:
                    bg_story = meta.background_story
                protagonist_bio = meta.protagonist_bio
            except Exception:
                pass
            if bg_story:
                user_parts.append(f"【世界观/背景参考】\n{bg_story}\n")
            if protagonist_bio:
                user_parts.append(f"【人物设定参考】\n{protagonist_bio}\n")

            # 续写要求 + 剧情走向
            user_parts.append(f"请续写以上内容，作为第 {chapter_num} 章「{chapter_title}」。\n")
            if requirement:
                user_parts.append(f"【续写要求】\n{requirement}\n")
            if plot:
                user_parts.append(f"【续写剧情走向】\n{plot}\n")

            global_prompt = self._client.global_user_prompt
            if global_prompt.strip():
                user_parts.append(f"【用户偏好提示】: \n{global_prompt}\n")

            user_parts.append(
                f"请直接输出续写正文，不要加任何解释或前言。字数不少于{word_count}字。"
            )

            user_prompt = "\n".join(user_parts)
            messages = [{"role": "system", "content": Prompts.NOVEL_CHAPTER_WRITING}]
            if bg_story:
                messages.append({"role": "system", "content": f"【核心设定】\n{bg_story}"})
            if protagonist_bio:
                messages.append({"role": "system", "content": f"【人物背景】\n{protagonist_bio}"})
            messages.append({"role": "user", "content": user_prompt})

            self._stream_signals.token.emit(
                f"\n\n📝 正在续写第 {chapter_num} 章「{chapter_title}」...\n\n"
            )

            response = self._client.raw_client.chat.completions.create(
                model=self._client.model,
                messages=messages,
                temperature=self._client.temperature,
                top_p=self._client.top_p,
                max_tokens=self._client.max_tokens,
                frequency_penalty=self._client.frequency_penalty,
                stream=False,
            )
            content = response.choices[0].message.content or ""

            self._stream_signals.token.emit(content)
            self._stream_signals.token.emit("\n\n---\n")

            # 保存为章节
            file_path, saved_version = self._novel_manager.save_chapter_version(
                book_title, chapter_num, chapter_title, content
            )
            self._stream_signals.token.emit(
                f"✅ 续写完成，已保存版本 v{saved_version} → `{file_path}`\n"
            )

            # 保存生成历史（含续写要求与剧情走向，供重新生成时还原）
            self._novel_manager.save_generation_record(
                title=book_title,
                chapter_num=chapter_num,
                chapter_title=chapter_title,
                version=saved_version,
                prompt=user_prompt,
                model=self._client.model,
                temperature=self._client.temperature,
                top_p=self._client.top_p,
                max_tokens=self._client.max_tokens,
                frequency_penalty=self._client.frequency_penalty,
                content_preview=content.replace("\n", " "),
                requirement=requirement,
                plot=plot,
            )

            # 提炼摘要
            self._stream_signals.token.emit("\n🔍 正在提炼剧情记忆...\n")
            summary = self._novel_manager.generate_summary(
                self._client.raw_client, content, chapter_num, chapter_title,
                global_user_prompt=self._client.global_user_prompt
            )
            self._novel_manager.append_summary(
                book_title, chapter_num, chapter_title, summary
            )
            self._stream_signals.token.emit("📋 剧情摘要已同步至记忆库。\n")

            # 字数补充检查
            from utils.supplement import count_cn, supplement_content
            actual_words = count_cn(content)
            if actual_words < word_count * 0.8 and actual_words > 0:
                self._stream_signals.token.emit(f"\n📝 当前{actual_words}字，目标{word_count}字，正在进行补充...\n")
                try:
                    supplemented = supplement_content(
                        self._client.raw_client, content, word_count, actual_words,
                        chapter_title, self._client.model, self._client.temperature,
                        global_user_prompt=self._client.global_user_prompt
                    )
                    if supplemented:
                        content = content + "\n\n" + supplemented
                        file_path, saved_version = self._novel_manager.save_chapter_version(
                            book_title, chapter_num, chapter_title, content, version=saved_version
                        )
                        final_words = count_cn(content)
                        self._stream_signals.token.emit(f"✅ 补充完成，总字数：{final_words}字 (v{saved_version})\n")
                except Exception as supp_e:
                    self._stream_signals.token.emit(f"⚠️ 补充过程出错: {supp_e}\n")

            # 更新世界书
            self._stream_signals.token.emit("📖 正在更新世界书...\n")
            try:
                from core.world_bible import extract_and_merge_world_bible
                bible = self._novel_manager.load_world_bible(book_title)
                updated_bible = extract_and_merge_world_bible(
                    self._client.raw_client, content, chapter_num, bible, self._client.model,
                    global_user_prompt=self._client.global_user_prompt
                )
                self._novel_manager.save_world_bible(book_title, updated_bible)
                self._stream_signals.token.emit("✅ 世界书已更新。\n")
            except Exception as wb_e:
                self._stream_signals.token.emit(f"⚠️ 世界书更新跳过: {wb_e}\n")

            self._stream_signals.refresh_chapter_info.emit(book_title)
            self._stream_signals.token.emit(
                f"\n📖 下一章：第{self._novel_manager.get_next_chapter_num(book_title)}章\n"
            )
            self._stream_signals.finished.emit()
        except Exception as e:
            self._stream_signals.error.emit(f"续写失败: {e}")

    # ========== 发送消息 ==========

    def _on_send(self) -> None:
        """发送按钮点击处理"""
        if self._streaming:
            return

        # 如果当前是小说模式且开启了章节续写 → 触发章节生成
        if (
            isinstance(self._client.strategy, NovelStrategy)
            and self._client.strategy.chapter_mode
        ):
            self._on_generate_chapter()
            return

        # 如果当前是续写模式且章节模式已勾选 → 触发续写章节生成
        if (
            isinstance(self._client.strategy, ContinuationStrategy)
            and self._cont_chapter_mode_check.isChecked()
        ):
            self._on_cont_generate_chapter()
            return

        user_input = self._input_box.toPlainText().strip()
        if not user_input:
            return

        self._input_box.clear()
        self._append_user_message(user_input)

        self._streaming = True
        self._assistant_text_buffer = []

        threading.Thread(
            target=self._run_stream,
            args=(user_input,),
            daemon=True,
        ).start()

    def _run_stream(self, user_input: str) -> None:
        """后台线程：调用流式 API"""
        try:
            for token in self._client.chat_stream(user_input):
                self._stream_signals.token.emit(token)
            self._stream_signals.finished.emit()
        except Exception as e:
            self._stream_signals.error.emit(str(e))

    def _on_stream_token(self, token: str) -> None:
        """主线程：接收一个 token"""
        self._assistant_text_buffer.append(token)
        full_text = "".join(self._assistant_text_buffer)
        self._render_assistant_stream(full_text)
        # 实时显示已接收字符数
        char_count = len(full_text)
        self._stream_count_label.setText(f"⏳ 已接收 {char_count} 字符")
        if not self._stream_count_label.isVisible():
            self._stream_count_label.setVisible(True)

    def _on_stream_finished(self) -> None:
        """主线程：流式完成"""
        self._streaming = False
        full_text = "".join(self._assistant_text_buffer)
        self._render_assistant_message(full_text)
        self._stream_count_label.setVisible(False)

    def _on_stream_error(self, error_msg: str) -> None:
        """主线程：流式出错"""
        self._streaming = False
        self._stream_count_label.setVisible(False)
        QMessageBox.critical(self, "API 错误", f"调用失败：{error_msg}")

    # ========== 渲染 ==========

    def _append_user_message(self, text: str) -> None:
        """追加用户消息到显示区域"""
        escaped = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        escaped = escaped.replace("\n", "<br>")
        js_safe = self._escape_for_js(escaped)
        script = f"""
            (function() {{
                var div = document.createElement('div');
                div.className = 'user-msg';
                div.innerHTML = '<strong>🧑 你：</strong><br>' + `{js_safe}`;
                document.body.appendChild(div);
                window.scrollTo(0, document.body.scrollHeight);
            }})();
        """
        self._display.page().runJavaScript(script)

    def _render_assistant_stream(self, text: str) -> None:
        """流式渲染 Markdown"""
        html_body = md_lib.markdown(
            text,
            extensions=["fenced_code", "tables", "codehilite", "nl2br", "sane_lists"],
        )
        escaped_body = self._escape_for_js(html_body)

        script = f"""
            (function() {{
                var container = document.getElementById('stream-container');
                if (!container) {{
                    container = document.createElement('div');
                    container.id = 'stream-container';
                    container.className = 'assistant-msg';
                    document.body.appendChild(container);
                }}
                container.innerHTML = '<strong>🤖 助手：</strong><br>' + `{escaped_body}`;
                window.scrollTo(0, document.body.scrollHeight);
            }})();
        """
        self._display.page().runJavaScript(script)

    def _render_assistant_message(self, text: str) -> None:
        """最终渲染"""
        html_body = md_lib.markdown(
            text,
            extensions=["fenced_code", "tables", "codehilite", "nl2br", "sane_lists"],
        )
        escaped_body = self._escape_for_js(html_body)

        script = f"""
            (function() {{
                var old = document.getElementById('stream-container');
                if (old) {{
                    var finalDiv = document.createElement('div');
                    finalDiv.className = 'assistant-msg';
                    finalDiv.innerHTML = '<strong>🤖 助手：</strong><br>' + `{escaped_body}`;
                    old.parentNode.replaceChild(finalDiv, old);
                }}
                window.scrollTo(0, document.body.scrollHeight);
            }})();
        """
        self._display.page().runJavaScript(script)

    @staticmethod
    def _escape_for_js(s: str) -> str:
        return (
            s.replace("\\", "\\\\")
            .replace("`", "\\`")
            .replace("$", "\\$")
        )

    # ========== 📋 章节信息更新 ==========

    def _refresh_chapter_info_display(self, title: str) -> None:
        """刷新章节信息显示（同时更新小说面板和续写面板）"""
        chapters = self._novel_manager.list_chapters(title)
        info_text = ""
        if not chapters:
            info_text = f"暂无章节，下一章编号: 第{self._novel_manager.get_next_chapter_num(title)}章"
        else:
            lines = [f"已有 {len(chapters)} 章，下一章: 第{self._novel_manager.get_next_chapter_num(title)}章"]
            for ch in chapters:
                active = ch.get("active_version", 1)
                count = ch.get("version_count", 1)
                if count > 1:
                    lines.append(f"  · 第{ch['num']}章「{ch['title']}」v{active}/{count}个版本")
                else:
                    lines.append(f"  · 第{ch['num']}章「{ch['title']}」")
            info_text = "\n".join(lines)
        self._chapter_info_label.setText(info_text)
        self._cont_chapter_info_label.setText(info_text)

    # ========== 📖 世界书对话框 ==========

    def _on_world_bible(self) -> None:
        """打开世界书编辑对话框"""
        title = self._get_current_book_title()
        if not title:
            QMessageBox.warning(self, "提示", "请先选择或创建一本小说。")
            return
        bible = self._novel_manager.load_world_bible(title)
        dlg = WorldBibleDialog(self, bible)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._novel_manager.save_world_bible(title, dlg.get_bible())
            QMessageBox.information(self, "提示", "世界书已保存。")

    # ========== 📄 分段摘要（导入新小说） ==========

    def _on_split_summarize(self) -> None:
        """
        从源文档创建新小说：AI语义分段 → 提取世界观 → 写入世界书/meta → 自动加载UI
        仅用于新小说创建（已有章节会警告）。
        """
        from strategies.novel_strategy import NovelStrategy
        mode_ok = isinstance(self._client.strategy, NovelStrategy) if hasattr(self, '_client') else False
        if not mode_ok or not hasattr(self, '_client') or self._client is None:
            QMessageBox.warning(self, "提示", "分段摘要导入仅支持小说模式。请先切换到小说模式。")
            return

        # 检查是否为新小说（无章节或用户确认覆盖）
        title = self._novel_title_edit.text().strip()
        if title:
            next_ch = self._novel_manager.get_next_chapter_num(title)
            if next_ch > 1:
                reply = QMessageBox.question(
                    self, "确认",
                    f"「{title}」已有章节内容。\n"
                    "分段摘要应用于从源文档创建新小说。\n"
                    "继续将覆盖小说设定但保留章节内容。是否继续？",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                )
                if reply != QMessageBox.StandardButton.Yes:
                    return

        client = self._client.raw_client
        if client is None:
            QMessageBox.warning(self, "错误", "客户端未初始化。")
            return

        # 选择源文档
        file_path, _ = QFileDialog.getOpenFileName(
            self, "选择源文档（设定/大纲/草稿）", "",
            "文本文件 (*.txt *.md);;所有文件 (*.*)",
        )
        if not file_path:
            return

        # 自动推断小说标题
        if not title:
            title = os.path.splitext(os.path.basename(file_path))[0]
            self._novel_title_edit.setText(title)

        self._streaming = True
        self._assistant_text_buffer = []
        self._append_user_message(f"📄 从文档导入小说：{os.path.basename(file_path)}")

        def _run():
            try:
                from utils.summarize import segment_by_ai, extract_world_bible_from_segments, generate_novel_settings_from_world_bible
                from core.world_bible import WorldBible, CharacterEntry, LocationEntry, TimelineEntry, PlotThread

                model = self._client.model

                # Phase 1: 读取文件 + AI 语义分段
                self._stream_signals.token.emit(
                    f"\n\n⏳ 第一步：AI 语义分段…\n"
                )
                with open(file_path, "r", encoding="utf-8") as f:
                    text = f.read()
                _chars = len(text)
                _words = len(text.replace('\n', '').replace('\r', '').replace(' ', '').replace('　', ''))
                self._stream_signals.token.emit(f"  读到 {_words} 个字，{_chars} 个字符\n")

                _global_prompt = self._client.global_user_prompt
                segments = segment_by_ai(client, text, model, global_user_prompt=_global_prompt)
                self._stream_signals.token.emit(
                    f"  ✅ AI 识别出 {len(segments)} 个逻辑段落\n\n"
                )
                for seg_title, seg_content in segments:
                    preview = seg_content[:80].replace("\n", " ")
                    self._stream_signals.token.emit(
                        f"  📌 **{seg_title}** — {preview}…\n"
                    )

                # Phase 2: 逐段提取世界观
                self._stream_signals.token.emit(f"\n⏳ 第二步：逐段提取世界观信息…\n")

                def _progress(cur, total):
                    if cur == 1 or cur % max(1, total // 5) == 0 or cur == total:
                        self._stream_signals.token.emit(f"  提取进度: {cur}/{total}\n")

                world_data = extract_world_bible_from_segments(
                    client, segments, model, progress_callback=_progress,
                    global_user_prompt=_global_prompt,
                )

                # 汇报提取结果
                wb_count = len(world_data.get("key_worldbuilding", []))
                fs_count = len(world_data.get("global_foreshadowing", []))
                self._stream_signals.token.emit(
                    f"\n  📊 提取结果:\n"
                    f"    👥 角色 {len(world_data.get('characters', []))} 个\n"
                    f"    🏙️ 地点 {len(world_data.get('locations', []))} 个\n"
                    f"    📜 规则 {len(world_data.get('rules', []))} 条\n"
                    f"    ⏱️ 事件 {len(world_data.get('timeline', []))} 个\n"
                    f"    🔗 剧情线 {len(world_data.get('plot_threads', []))} 条\n"
                    + (f"    📖 关键设定 {wb_count} 条 | 🔮 伏笔 {fs_count} 条\n" if wb_count or fs_count else "")
                )

                # Phase 3: 创建小说目录 + 保存世界书
                self._stream_signals.token.emit(f"\n⏳ 第三步：创建小说「{title}」并保存数据…\n")
                self._novel_manager.create_book(title)

                bible = WorldBible(
                    characters=[CharacterEntry(**c) for c in world_data.get("characters", [])],
                    locations=[LocationEntry(**l) for l in world_data.get("locations", [])],
                    rules=list(world_data.get("rules", [])),
                    timeline=[TimelineEntry(**t) for t in world_data.get("timeline", [])],
                    active_plot_threads=[PlotThread(**p) for p in world_data.get("plot_threads", [])],
                    last_updated_chapter=0,
                    key_worldbuilding_passages=list(world_data.get("key_worldbuilding", [])),
                    global_foreshadowing=list(world_data.get("global_foreshadowing", [])),
                    global_key_dialogues=list(world_data.get("global_key_dialogues", [])),
                )
                self._novel_manager.save_world_bible(title, bible)
                self._stream_signals.token.emit(f"  ✅ 世界书已保存\n")

                # Phase 4: 生成小说设定（背景/主角/写作要求）
                self._stream_signals.token.emit(f"⏳ 第四步：生成小说设定…\n")
                settings = generate_novel_settings_from_world_bible(client, world_data, model, global_user_prompt=_global_prompt)

                self._novel_manager.save_meta(
                    title,
                    protagonist_bio=settings.get("protagonist_bio", ""),
                    background_story=settings.get("background_story", ""),
                    writing_demand=settings.get("writing_demand", ""),
                )
                self._stream_signals.token.emit(
                    f"  ✅ 设定已保存至 meta.json\n"
                )

                # Phase 5: 触发主线程加载 UI
                self._stream_signals.token.emit(
                    f"\n{'='*50}\n"
                    f"✅ 全部完成！「{title}」创建成功\n"
                    f"  • {len(segments)} 个语义段落已分段\n"
                    f"  • 世界书已建立（{len(world_data.get('characters', []))}角色 + "
                    f"{len(world_data.get('locations', []))}地点 + "
                    f"{len(world_data.get('rules', []))}规则）\n"
                    f"  • 小说设定已生成并加载\n"
                    f"  • 现在可以直接生成章节了！\n"
                    f"{'='*50}\n"
                )
                self._stream_signals.novel_imported.emit(title)
                self._stream_signals.finished.emit()

            except Exception as e:
                import traceback
                self._stream_signals.token.emit(f"\n❌ 错误: {e}\n")
                self._stream_signals.token.emit(f"\n```\n{traceback.format_exc()}\n```\n")
                self._stream_signals.error.emit(f"分段摘要失败: {e}")

        threading.Thread(target=_run, daemon=True).start()

    def _on_novel_imported(self, title: str) -> None:
        """主线程：导入完成后刷新书架并加载设定到 UI"""
        self._refresh_novel_bookshelf()
        self._bookshelf_combo.setCurrentText(title)
        self._on_book_selected(title)

    def _check_book_empty(self, title: str) -> bool:
        """检查目标书是否完全空（无章节、无设定、无世界书），非空时弹警告并返回 False"""
        chapters = self._novel_manager.list_chapters(title)
        if chapters:
            QMessageBox.warning(
                self, "提示",
                f"「{title}」已有 {len(chapters)} 个章节，\n"
                f"请先创建一本新小说，或选择一个空书架。"
            )
            return False

        meta = self._novel_manager.load_meta(title)
        if meta.protagonist_bio or meta.background_story or meta.writing_demand:
            QMessageBox.warning(
                self, "提示",
                f"「{title}」已有设定信息，\n"
                f"请先创建一本新小说，或选择一个空书架。"
            )
            return False

        bible = self._novel_manager.load_world_bible(title)
        if (bible.characters or bible.locations or bible.rules
                or bible.active_plot_threads or bible.key_worldbuilding_passages
                or bible.global_foreshadowing or bible.global_key_dialogues):
            QMessageBox.warning(
                self, "提示",
                f"「{title}」已有世界书信息，\n"
                f"请先创建一本新小说，或选择一个空书架。"
            )
            return False

        return True

    # ========== 🔍 续写分析流程 ==========

    def _on_analyze_continuation(self) -> None:
        """
        新版分析：读取源文档 → 段落预览弹窗 → 结构化提取世界观
        → 创建小说 → 保存世界书 → 保存设定 → 自动加载 UI
        """
        if self._streaming:
            return

        source_text = self._read_continuation_source()
        if not source_text:
            QMessageBox.warning(self, "提示", "请先选择续写源文档或文件夹。")
            return

        client = self._client.raw_client if hasattr(self, '_client') else None
        if client is None:
            QMessageBox.warning(self, "错误", "客户端未初始化。")
            return

        # 自动推断小说标题（从文件名）
        title = self._get_current_book_title()
        source_file = self._continue_file_path.text().strip()
        source_folder = self._continue_folder_path.text().strip()
        if not title:
            if source_file:
                title = os.path.splitext(os.path.basename(source_file))[0]
            elif source_folder:
                title = os.path.basename(source_folder)
            else:
                title = "续写作品"

        if not self._check_book_empty(title):
            return

        # ── 段落预览弹窗 ──
        dlg = SectionPreviewDialog(
            self,
            source_text=source_text if not source_folder else None,
            folder_path=source_folder,
            client=client, model=self._client.model,
            global_user_prompt=self._client.global_user_prompt,
            mode="analyze",
        )
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        result = dlg.get_result()
        if not result:
            return

        # 根据弹窗结果决定处理路径
        if result["mode"] == "folder" and result.get("files"):
            # 文件夹模式：批量导入（使用确认后的文件列表）
            self._streaming = True
            self._assistant_text_buffer = []
            self._append_user_message(f"📂 批量导入章节 → {title}")
            self._cont_analysis_source = source_text
            self._cont_analysis_source_path = source_folder
            threading.Thread(
                target=self._run_batch_folder_import,
                args=(title, source_folder, self._client.model, client),
                kwargs={"files_list": result["files"]},
                daemon=True,
            ).start()
            return

        # 文件模式：使用确认后的段落直接进入分析
        sections = result.get("sections", [])
        if not sections:
            QMessageBox.warning(self, "提示", "段落列表为空，无法分析。")
            return

        model = self._client.model
        self._streaming = True
        self._assistant_text_buffer = []
        self._append_user_message(f"🔍 分析源文档并导入小说：{title}")

        self._cont_analysis_source = source_text
        self._cont_analysis_source_path = source_file or source_folder or ""

        self._start_analysis_with_sections(title, source_text, sections, client)

    def _start_analysis_with_sections(self, title: str, source_text: str, sections: list, client) -> None:
        """后台线程：使用已确认的段落直接进行世界观提取（跳过 AI 分段）"""
        def _run():
            try:
                from utils.summarize import extract_world_bible_from_segments, generate_novel_settings_from_world_bible
                from core.world_bible import WorldBible, CharacterEntry, LocationEntry, TimelineEntry, PlotThread

                si = self._stream_signals
                _global_prompt = self._client.global_user_prompt

                si.token.emit(f"\n✅ 已确认 {len(sections)} 个段落\n")
                si.token.emit(f"\n⏳ 第一步：逐段提取世界观信息…\n")
                world_data = extract_world_bible_from_segments(client, sections, self._client.model, global_user_prompt=_global_prompt)

                chars = len(world_data.get("characters", []))
                locs = len(world_data.get("locations", []))
                rules = len(world_data.get("rules", []))
                wb_count = len(world_data.get("key_worldbuilding", []))
                fs_count = len(world_data.get("global_foreshadowing", []))
                si.token.emit(
                    f"  ✅ 提取到: {chars}角色 / {locs}地点 / {rules}规则"
                    + (f" / {wb_count}关键设定 / {fs_count}伏笔" if wb_count or fs_count else "")
                    + "\n"
                )

                si.token.emit(f"\n⏳ 第二步：创建小说并保存数据…\n")
                self._novel_manager.create_book(title)

                bible = WorldBible(
                    characters=[CharacterEntry(**c) for c in world_data.get("characters", [])],
                    locations=[LocationEntry(**l) for l in world_data.get("locations", [])],
                    rules=list(world_data.get("rules", [])),
                    timeline=[TimelineEntry(**t) for t in world_data.get("timeline", [])],
                    active_plot_threads=[PlotThread(**p) for p in world_data.get("plot_threads", [])],
                    last_updated_chapter=0,
                    key_worldbuilding_passages=list(world_data.get("key_worldbuilding", [])),
                    global_foreshadowing=list(world_data.get("global_foreshadowing", [])),
                    global_key_dialogues=list(world_data.get("global_key_dialogues", [])),
                )
                self._novel_manager.save_world_bible(title, bible)

                si.token.emit(f"⏳ 第三步：生成小说设定…\n")
                settings = generate_novel_settings_from_world_bible(client, world_data, self._client.model, global_user_prompt=_global_prompt)
                self._novel_manager.save_meta(
                    title,
                    protagonist_bio=settings.get("protagonist_bio", ""),
                    background_story=settings.get("background_story", ""),
                    writing_demand=settings.get("writing_demand", ""),
                )

                si.token.emit(f"  ✅ 设定已保存\n")
                si.token.emit(
                    f"\n{'='*50}\n"
                    f"✅ 分析完成！「{title}」创建成功\n"
                    f"  • {len(sections)} 个语义段落\n"
                    f"  • 世界书 {chars}角色 + {locs}地点 + {rules}规则\n"
                    f"  • 小说设定已生成并加载到面板\n"
                    f"  • 现在可以点击「🚀 生成下一章」开始续写\n"
                    f"{'='*50}\n"
                )
                si.finished.emit()

                self._cont_analysis_world_data = world_data
                self._cont_analysis_settings = settings
                self._stream_signals.novel_imported.emit(title)
                self._stream_signals.analysis_done.emit(
                    str(world_data), str(settings), title,
                )

            except Exception as e:
                import traceback
                self._stream_signals.token.emit(f"\n❌ 分析失败: {e}\n")
                self._stream_signals.token.emit(f"\n```\n{traceback.format_exc()}\n```\n")
                self._stream_signals.error.emit(f"分析失败: {e}")

        threading.Thread(target=_run, daemon=True).start()

    # ========== 📂 批量导入章节（文件夹模式） ==========

    def _has_numbered_files(self, folder_path: str) -> bool:
        """检测文件夹是否包含数字命名的文本文件（如 1.txt, 2.txt...）"""
        ext_map = {".txt", ".md"}
        for fname in os.listdir(folder_path):
            ext = os.path.splitext(fname)[1].lower()
            if ext in ext_map:
                stem = os.path.splitext(fname)[0]
                if re.search(r'\d+', stem):
                    return True
        return False

    def _run_batch_folder_import(self, title: str, folder_path: str, model: str, client,
                                  files_list: list | None = None) -> None:
        """
        后台线程：逐章批量导入文件夹中的数字命名文件

        将每个文件视为一个章节，按数字顺序依次处理：
        1. 读取文件内容（如传入了 files_list 则直接使用）
        2. 带上已有世界观上下文，调用 extract_and_merge_world_bible 提取并合并
        3. 生成章节摘要并追加到 plot_summary
        4. 保存章节文件
        5. 所有章节处理完成后生成小说设定

        Args:
            files_list: 可选，SectionPreviewDialog 确认后的文件列表。
                        包含 chapter_num, filename, full_content, sections 等字段。
                        传入后跳过文件夹扫描和文件读取。
        """
        try:
            from core.world_bible import WorldBible, extract_and_merge_world_bible
            from utils.summarize import generate_novel_settings_from_world_bible

            si = self._stream_signals
            _global_prompt = self._client.global_user_prompt

            # 构建统一章节列表 [(chapter_num, fname, content), ...]
            chapter_files: list[tuple[int, str, str]] = []

            if files_list:
                # 使用弹窗确认后的文件列表（跳过扫描和读取）
                for f in files_list:
                    chapter_files.append((f["chapter_num"], f["filename"], f["full_content"]))
                chapter_files.sort(key=lambda x: x[0])
                total = len(chapter_files)
                si.token.emit(f"📂 使用已确认的 {total} 个文件，开始逐章导入…\n\n")
            else:
                # 扫描文件夹，提取数字命名文件并排序
                ext_map = {".txt", ".md"}
                raw_files = []
                for fname in os.listdir(folder_path):
                    ext = os.path.splitext(fname)[1].lower()
                    if ext not in ext_map:
                        continue
                    stem = os.path.splitext(fname)[0]
                    nums = re.findall(r'\d+', stem)
                    if nums:
                        raw_files.append((int(nums[0]), fname))

                if not raw_files:
                    si.token.emit("❌ 文件夹中没有找到数字命名的文本文件（如 1.txt, 2.txt...）\n")
                    self._stream_signals.error.emit("文件夹中未找到数字命名的文件")
                    return

                raw_files.sort(key=lambda x: x[0])
                total = len(raw_files)
                si.token.emit(f"📂 检测到 {total} 个章节文件，开始逐章导入…\n\n")

                for chapter_num, fname in raw_files:
                    fpath = os.path.join(folder_path, fname)
                    content = ""
                    for enc in ("utf-8", "gbk"):
                        try:
                            with open(fpath, "r", encoding=enc) as f:
                                content = f.read()
                            break
                        except UnicodeDecodeError:
                            continue
                    if not content:
                        continue
                    chapter_files.append((chapter_num, fname, content))

            if not chapter_files:
                si.token.emit("❌ 没有可处理的章节文件。\n")
                self._stream_signals.error.emit("没有可处理的章节文件")
                return

            # 创建小说
            self._novel_manager.create_book(title)
            world_bible = WorldBible()

            for idx, (chapter_num, fname, content) in enumerate(chapter_files, 1):
                chapter_title = os.path.splitext(fname)[0]
                si.token.emit(f"  📖 [{idx}/{total}] 第{chapter_num}章「{chapter_title}」…\n")

                # 加载前文摘要（每章重新加载，因为 plot_summary 在不断增加）
                story_context = ""
                try:
                    summary = self._novel_manager.load_smart_summary(
                        title, client, next_chapter_num=chapter_num,
                        model=model, global_user_prompt=_global_prompt,
                    )
                    if summary and "故事刚刚开始" not in summary:
                        story_context = summary
                except Exception:
                    pass

                # 加载 meta 中的设定
                meta = self._novel_manager.load_meta(title)

                # 提取并合并世界书（带上全局上下文）
                world_bible = extract_and_merge_world_bible(
                    client, content, chapter_num, world_bible,
                    model, global_user_prompt=_global_prompt,
                    story_context=story_context,
                    background_story=meta.background_story,
                    protagonist_bio=meta.protagonist_bio,
                    writing_demand=meta.writing_demand,
                )
                self._novel_manager.save_world_bible(title, world_bible)

                # 生成章节摘要
                summary_text = self._novel_manager.generate_summary(
                    client, content, chapter_num, chapter_title,
                    model=model, global_user_prompt=_global_prompt,
                )
                self._novel_manager.append_summary(title, chapter_num, chapter_title, summary_text)

                # 保存章节文件（自动管理版本）
                self._novel_manager.save_chapter_version(
                    title, chapter_num, chapter_title, content,
                )

                si.token.emit(f"    ✅ 世界书已更新 | 摘要已保存\n")

            # 所有章节处理完成 → 从世界书生成小说设定
            si.token.emit(f"\n⏳ 正在从世界书生成小说设定……\n")
            from dataclasses import asdict
            world_data_for_settings = {
                "characters": [asdict(c) for c in world_bible.characters],
                "locations": [asdict(l) for l in world_bible.locations],
                "rules": list(world_bible.rules),
                "plot_threads": [asdict(p) for p in world_bible.active_plot_threads],
                "timeline": [asdict(t) for t in world_bible.timeline],
            }
            settings = generate_novel_settings_from_world_bible(
                client, world_data_for_settings, model,
                global_user_prompt=_global_prompt,
            )
            self._novel_manager.save_meta(
                title,
                protagonist_bio=settings.get("protagonist_bio", ""),
                background_story=settings.get("background_story", ""),
                writing_demand=settings.get("writing_demand", ""),
            )

            si.token.emit(
                f"\n{'='*50}\n"
                f"✅ 批量导入完成！「{title}」共导入 {total} 章\n"
                f"  • 世界书累积: {len(world_bible.characters)}角色 / {len(world_bible.locations)}地点 / {len(world_bible.rules)}规则\n"
                f"  • 小说设定已从世界书生成\n"
                f"{'='*50}\n"
            )

            # 触发 UI 刷新
            self._stream_signals.novel_imported.emit(title)

        except Exception as e:
            import traceback
            self._stream_signals.token.emit(f"\n❌ 批量导入失败: {e}\n")
            self._stream_signals.token.emit(f"\n```\n{traceback.format_exc()}\n```\n")
            self._stream_signals.error.emit(f"批量导入失败: {e}")
        finally:
            self._streaming = False

    def _show_analysis_dialog(self, world_data_str: str, settings_str: str, title: str) -> None:
        """在主线程显示分析结果对话框"""
        self._streaming = False
        world_data = getattr(self, '_cont_analysis_world_data', {})
        settings = getattr(self, '_cont_analysis_settings', {})

        dlg = ContinuationAnalysisDialog(
            self, world_data, settings,
            on_suggest=self._on_cont_suggest,
            on_specify=self._on_cont_specify,
        )
        dlg.exec()

    def _on_cont_suggest(self, setting: str, plot_outline: str, word_count: int,
                         world_data: dict | None = None) -> None:
        """AI 建议发展方向 → 用户选择 → 续写"""
        client = self._client.raw_client if hasattr(self, '_client') else None
        if client is None:
            return

        self._streaming = True
        self._assistant_text_buffer = []
        self._append_user_message("🎲 AI 建议发展方向")

        def _run():
            try:
                self._stream_signals.token.emit("\n\n🎲 AI 正在分析发展方向...\n\n")
                directions = suggest_directions(client, setting, plot_outline,
                                                self._client.model, world_data,
                                                global_user_prompt=self._client.global_user_prompt)
                self._stream_signals.finished.emit()
                self._stream_signals.directions_ready.emit(directions, setting, plot_outline, word_count)
            except Exception as e:
                self._stream_signals.error.emit(f"方向建议失败: {e}")

        threading.Thread(target=_run, daemon=True).start()

    def _show_direction_selector(self, directions: list, setting: str, plot_outline: str, word_count: int):
        """在主线程显示方向选择对话框"""
        self._streaming = False
        dlg = DirectionSelectionDialog(self, directions)
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.selected_direction:
            self._do_continuation_with_context(setting, word_count, plot=dlg.selected_direction)

    def _on_cont_specify(self, setting: str, plot_outline: str, word_count: int) -> None:
        """用户指定剧情 → 续写"""
        # 合并分析剧情节点的上下文 + 用户手动输入的剧情
        manual_plot = self._continue_plot.toPlainText().strip()
        merged_plot = plot_outline
        if manual_plot:
            merged_plot = (merged_plot + "\n\n" + manual_plot) if merged_plot else manual_plot
        self._do_continuation_with_context(setting, word_count, plot=merged_plot)

    def _do_continuation_with_context(self, setting: str, word_count: int = 0, plot: str = "") -> None:
        """带分析上下文的续写执行"""
        source_text = getattr(self, '_cont_analysis_source', "")
        if not source_text:
            source_text = self._read_continuation_source()
            if source_text:
                self._cont_analysis_source = source_text
        # source_text 为空时仍可续写，仅 prompt 中不添加【原文内容】区块

        book_title = self._get_current_book_title()
        if not book_title:
            QMessageBox.warning(self, "错误", "请先选择或创建一本小说。")
            return

        chapter_num = self._novel_manager.get_next_chapter_num(book_title)
        chapter_title = self._cont_chapter_title_edit.text().strip()
        if not chapter_title:
            chapter_title = f"续写 (第{chapter_num}章)"
        requirement = self._continue_requirement.toPlainText().strip()
        if not plot:
            plot = self._continue_plot.toPlainText().strip()
        word_count = word_count or self._continue_word_count.value()

        self._streaming = True
        self._assistant_text_buffer = []
        self._append_user_message(f"📝 续写第{chapter_num}章：{chapter_title}")

        threading.Thread(
            target=self._run_continuation,
            args=(book_title, chapter_num, chapter_title, source_text, requirement, word_count, plot, setting),
            daemon=True,
        ).start()

    # ========== 📋 左侧面板续写辅助按钮（替代弹窗中的功能） ==========

    def _build_cont_plot_context(self) -> str:
        """从分析数据构建剧情上下文"""
        world_data = getattr(self, '_cont_analysis_world_data', None)
        if not world_data:
            return ""
        parts = []

        # 活跃剧情线（详细）
        threads = world_data.get("plot_threads", [])
        if threads:
            active = [p for p in threads if p.get("status") == "active"]
            if active:
                parts.append("当前活跃剧情线：")
                for p in active[:4]:
                    desc = p.get('description', '')[:100]
                    chars = p.get("involved_characters", [])
                    c_str = f" [角色：{', '.join(chars[:3])}]" if chars else ""
                    parts.append(f"- {p['name']}: {desc}{c_str}")

        # 已解决/休眠的剧情线（仍需留意）
        resolved = [p for p in threads if p.get("status") != "active"]
        if resolved:
            parts.append("待回收剧情线：")
            for p in resolved[:2]:
                parts.append(f"- {p['name']} ({p.get('status', '')})")

        # 最近事件
        timeline = world_data.get("timeline", [])
        if timeline:
            recent = timeline[-5:]
            parts.append("最近事件：")
            for t in recent:
                event = t.get('event', '')[:80]
                sig = t.get('significance', '')[:40]
                parts.append(f"- {event}" + (f" ({sig})" if sig else ""))

        return "\n".join(parts)

    def _on_cont_panel_suggest(self) -> None:
        """左侧面板按钮：AI 建议发展方向"""
        settings = getattr(self, '_cont_analysis_settings', None)
        if not settings:
            QMessageBox.warning(self, "提示", "请先分析源文档以获取故事上下文。")
            return
        setting = settings.get("background_story", "")
        plot_context = self._build_cont_plot_context()
        word_count = self._continue_word_count.value()
        world_data = getattr(self, '_cont_analysis_world_data', None)
        self._on_cont_suggest(setting, plot_context, word_count, world_data)

    def _on_cont_panel_specify(self) -> None:
        """左侧面板按钮：我指定剧情"""
        settings = getattr(self, '_cont_analysis_settings', None)
        if not settings:
            QMessageBox.warning(self, "提示", "请先分析源文档以获取故事上下文。")
            return
        setting = settings.get("background_story", "")
        plot_context = self._build_cont_plot_context()
        word_count = self._continue_word_count.value()
        self._on_cont_specify(setting, plot_context, word_count)

    # ========== ⚙ 章节管理对话框 ==========

    def _on_manage_chapters(self) -> None:
        """打开章节版本管理对话框"""
        title = self._get_current_book_title()
        if not title:
            QMessageBox.warning(self, "提示", "请先在书架中选择一部小说。")
            return

        from PyQt6.QtWidgets import QDialog, QListWidget, QListWidgetItem

        class ChapterManagerDialog(QDialog):
            _regenerate_done_signal = pyqtSignal(int, int, str)
            _regenerate_error_signal = pyqtSignal(str)

            def __init__(self, parent, novel_mgr, book_title, client):
                super().__init__(parent)
                self._novel_mgr = novel_mgr
                self._book_title = book_title
                self._client = client
                self._generating = False
                self.setWindowTitle(f"章节管理 - {book_title}")
                self.resize(500, 400)
                self.setModal(True)

                self._regenerate_done_signal.connect(self._on_regenerate_done)
                self._regenerate_error_signal.connect(self._on_regenerate_error)

                self._build_ui()
                self._load_chapters()

            def _build_ui(self):
                layout = QVBoxLayout(self)

                self._chapter_list = QListWidget()
                self._chapter_list.setStyleSheet("""
                    QListWidget { background-color: #2d2d2d; color: #e0e0e0; border: 1px solid #444; }
                    QListWidget::item { padding: 6px 10px; border-bottom: 1px solid #3a3a3a; }
                    QListWidget::item:selected { background-color: #264f78; }
                """)
                self._chapter_list.itemDoubleClicked.connect(self._on_preview)
                layout.addWidget(self._chapter_list)

                btn_row = QHBoxLayout()

                preview_btn = QPushButton("👁 预览")
                preview_btn.clicked.connect(self._on_preview)
                btn_row.addWidget(preview_btn)

                set_active_btn = QPushButton("⭐ 设为活跃（计入剧情）")
                set_active_btn.clicked.connect(self._on_set_active)
                btn_row.addWidget(set_active_btn)

                regenerate_btn = QPushButton("🔁 重新生成")
                regenerate_btn.clicked.connect(self._on_regenerate)
                btn_row.addWidget(regenerate_btn)

                delete_btn = QPushButton("🗑 删除此版本")
                delete_btn.setStyleSheet("QPushButton { background-color: #8b0000; }")
                delete_btn.clicked.connect(self._on_delete_version)
                btn_row.addWidget(delete_btn)

                self._close_btn = QPushButton("关闭")
                self._close_btn.clicked.connect(self.accept)
                btn_row.addWidget(self._close_btn)

                layout.addLayout(btn_row)

            def _load_chapters(self):
                self._chapter_list.clear()
                chapters = self._novel_mgr.list_chapters(self._book_title)
                for ch in chapters:
                    versions = ch.get("versions", [])
                    active_v = ch.get("active_version", 1)
                    for vinfo in versions:
                        v = vinfo["v"]
                        title_text = vinfo.get("title", "")
                        marker = "⭐" if v == active_v else "  "
                        display = (
                            f"{marker} 第{ch['num']}章「{title_text}」 v{v} 【活跃版】"
                            if v == active_v
                            else f"{marker} 第{ch['num']}章「{title_text}」 v{v}"
                        )
                        item = QListWidgetItem(display)
                        item.setData(Qt.ItemDataRole.UserRole, {
                            "chapter_num": ch["num"],
                            "version": v,
                            "is_active": v == active_v,
                            "title": title_text,
                        })
                        if v == active_v:
                            item.setForeground(Qt.GlobalColor.cyan)
                        self._chapter_list.addItem(item)

                if self._chapter_list.count() == 0:
                    item = QListWidgetItem("（暂无章节）")
                    self._chapter_list.addItem(item)

            def _get_selected_data(self) -> dict | None:
                item = self._chapter_list.currentItem()
                if not item:
                    QMessageBox.warning(self, "提示", "请先选择一个章节版本。")
                    return None
                data = item.data(Qt.ItemDataRole.UserRole)
                return data if data else None

            def _on_preview(self):
                data = self._get_selected_data()
                if not data:
                    return
                content = self._novel_mgr.read_chapter_version(
                    self._book_title, data["chapter_num"], data["version"]
                )
                if not content:
                    QMessageBox.information(self, "预览", "（内容为空）")
                    return
                preview = content
                dialog = QDialog(self)
                dialog.setWindowTitle(f"第{data['chapter_num']}章 v{data['version']} 预览")
                dialog.resize(600, 500)
                dl = QVBoxLayout(dialog)
                edit = QTextEdit()
                edit.setReadOnly(True)
                edit.setPlainText(preview)
                dl.addWidget(edit)
                close_btn = QPushButton("关闭")
                close_btn.clicked.connect(dialog.accept)
                dl.addWidget(close_btn)
                dialog.exec()

            def _on_set_active(self):
                data = self._get_selected_data()
                if not data:
                    return
                if data["is_active"]:
                    QMessageBox.information(
                        self, "提示",
                        f"第{data['chapter_num']}章 v{data['version']} 已经是活跃版本。"
                    )
                    return
                self._novel_mgr.set_active_version(
                    self._book_title, data["chapter_num"], data["version"]
                )
                reply = QMessageBox.question(
                    self, "重建剧情记忆",
                    "已切换活跃版本。是否需要根据所有活跃章节重新生成剧情摘要？\n"
                    "（推荐：选「是」以确保后续章节连贯）",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                )
                if reply == QMessageBox.StandardButton.Yes and hasattr(self.parent(), "_client"):
                    try:
                        self._novel_mgr.rebuild_summary_from_active(
                            self._client.raw_client, self._book_title,
                            global_user_prompt=self._client.global_user_prompt
                        )
                        QMessageBox.information(self, "成功", "剧情摘要已重新生成。")
                    except Exception as e:
                        QMessageBox.warning(self, "摘要生成失败", str(e))

                QMessageBox.information(
                    self, "成功",
                    f"第{data['chapter_num']}章 v{data['version']} 已设为活跃版本。"
                )
                self._load_chapters()
                parent = self.parent()
                if hasattr(parent, "_refresh_chapter_info_display"):
                    parent._refresh_chapter_info_display(self._book_title)

            def _on_regenerate(self):
                data = self._get_selected_data()
                if not data:
                    return

                reply = QMessageBox.question(
                    self, "确认重新生成",
                    f"将使用您当前在左侧面板中填写的主角设定、世界观和写作要求，\n"
                    f"重新创作第 {data['chapter_num']} 章「{data.get('title', '')}」。\n"
                    f"生成结果会保存为新的版本（v+1），现有版本不会被覆盖。\n\n"
                    f"继续吗？",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                )
                if reply != QMessageBox.StandardButton.Yes:
                    return

                self._generating = True
                self._close_btn.setText("⏳ 生成中...")
                self._close_btn.setEnabled(False)

                # 在主线程捕获 UI 值
                parent = self.parent()
                _bg = parent._background_edit.toPlainText().strip()
                _bio = parent._protagonist_edit.toPlainText().strip()
                _demand = parent._demand_edit.toPlainText().strip()
                _global_prompt = self._client.global_user_prompt

                threading.Thread(target=self._do_regenerate, args=(data, _bg, _bio, _demand, _global_prompt), daemon=True).start()

            def _do_regenerate(self, data: dict, bg: str, bio: str, demand: str, global_prompt: str = "") -> None:
                try:
                    parent = self.parent()
                    chapter_num = data["chapter_num"]
                    chapter_title = data.get("title", f"第{chapter_num}章")

                    # 从历史生成记录加载续写要求和剧情走向
                    req = ""
                    plot = ""
                    record = self._novel_mgr.load_generation_record(
                        self._book_title, chapter_num, data["version"]
                    )
                    if record:
                        # requirement/plot 为空字符串时视为未保存
                        req = record.get("requirement", "") or ""
                        plot = record.get("plot", "") or ""

                    messages = [
                        {"role": "system", "content": "你是一位文笔细腻、想象力丰富的小说家。直接输出重写后的小说正文，不要加任何解释、前言或后记。保持与原章节一致的风格和质量水准，严格按照用户提供的【核心设定】和【人物背景】。"},
                    ]
                    if bg:
                        messages.append({"role": "system", "content": f"【核心设定】：\n{bg}"})
                    if bio:
                        messages.append({"role": "system", "content": f"【人物背景】：\n{bio}"})
                    if demand:
                        messages.append({"role": "system", "content": f"【写作要求】：\n{demand}"})

                    # 加载世界书
                    try:
                        bible = self._novel_mgr.load_world_bible(self._book_title)
                        if bible:
                            from core.world_bible import format_world_bible_for_prompt
                            wb_text = format_world_bible_for_prompt(bible)
                            if wb_text.strip():
                                messages.append({"role": "system", "content": f"【世界书（已建立设定库）】\n{wb_text}"})
                    except Exception:
                        pass

                    summary = self._novel_mgr.load_smart_summary(
                        self._book_title, client=self._client.raw_client,
                        next_chapter_num=chapter_num, max_recent=10,
                        global_user_prompt=self._client.global_user_prompt,
                    )

                    old_content = self._novel_mgr.read_chapter_version(
                        self._book_title, chapter_num, data["version"]
                    )

                    user_parts = [f"请创作小说的第 {chapter_num} 章，标题为「{chapter_title}」。\n"]
                    if summary and summary != "故事刚刚开始。":
                        user_parts.append(f"【前情提要】\n{summary}\n")
                    if req:
                        user_parts.append(f"【原文续写要求】\n{req}\n")
                    if plot:
                        user_parts.append(f"【原文续写剧情走向】\n{plot}\n")
                    if old_content:
                        preview = old_content.strip()
                        user_parts.append(
                            f"【参考：旧版本开头（你不需要完全照搬，仅用于保持风格一致性）】\n{preview}\n"
                        )
                    if global_prompt.strip():
                        user_parts.append(f"【用户偏好提示】: \n{global_prompt}\n")
                    user_parts.append(f"请直接输出第 {chapter_num} 章正文：")
                    messages.append({"role": "user", "content": "\n".join(user_parts)})

                    response = self._client.raw_client.chat.completions.create(
                        model=self._client.model,
                        messages=messages,
                        temperature=self._client.temperature,
                        top_p=self._client.top_p,
                        max_tokens=self._client.max_tokens,
                        frequency_penalty=self._client.frequency_penalty,
                        stream=False,
                    )
                    content = response.choices[0].message.content or ""

                    new_version = self._novel_mgr.get_next_version(self._book_title, chapter_num)
                    file_path, saved_version = self._novel_mgr.save_chapter_version(
                        self._book_title, chapter_num, chapter_title, content,
                        version=new_version,
                    )

                    # 更新世界书
                    try:
                        from core.world_bible import extract_and_merge_world_bible
                        bible = self._novel_mgr.load_world_bible(self._book_title)
                        updated_bible = extract_and_merge_world_bible(
                            self._client.raw_client, content, chapter_num, bible,
                            self._client.model,
                            global_user_prompt=self._client.global_user_prompt,
                        )
                        self._novel_mgr.save_world_bible(self._book_title, updated_bible)
                    except Exception:
                        pass

                    self._regenerate_done_signal.emit(chapter_num, saved_version, file_path)

                except Exception as e:
                    self._regenerate_error_signal.emit(str(e))

            def _on_regenerate_done(self, chapter_num: int, saved_version: int, file_path: str) -> None:
                QMessageBox.information(
                    self, "成功",
                    f"第 {chapter_num} 章已重新生成，保存为 v{saved_version}\n"
                    f"文件：{file_path}"
                )
                self._generating = False
                self._close_btn.setText("关闭")
                self._close_btn.setEnabled(True)
                self._load_chapters()
                parent = self.parent()
                if hasattr(parent, "_refresh_chapter_info_display"):
                    parent._refresh_chapter_info_display(self._book_title)

            def _on_regenerate_error(self, error_str: str) -> None:
                QMessageBox.critical(self, "重新生成失败", f"API 调用出错：{error_str}")
                self._generating = False
                self._close_btn.setText("关闭")
                self._close_btn.setEnabled(True)

            def _on_delete_version(self):
                data = self._get_selected_data()
                if not data:
                    return
                if data["is_active"]:
                    QMessageBox.warning(
                        self, "警告",
                        "不能删除活跃版本。请先选择其他版本设为活跃。"
                    )
                    return
                reply = QMessageBox.question(
                    self, "确认删除",
                    f"确定要删除第{data['chapter_num']}章 v{data['version']}？\n此操作不可恢复！",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                )
                if reply == QMessageBox.StandardButton.Yes:
                    self._novel_mgr.delete_chapter_version(
                        self._book_title, data["chapter_num"], data["version"]
                    )
                    QMessageBox.information(self, "成功", "已删除。")
                    self._load_chapters()
                    parent = self.parent()
                    if hasattr(parent, "_refresh_chapter_info_display"):
                        parent._refresh_chapter_info_display(self._book_title)

        dialog = ChapterManagerDialog(self, self._novel_manager, title, self._client)
        dialog.exec()

    # ========== 📤 导出功能 ==========

    def _get_export_format(self, combo: QComboBox) -> str:
        return combo.currentData() or "txt"

    def _prompt_save_path(self, default_name: str, fmt: str) -> str | None:
        """弹出保存文件对话框，返回选择的路径或 None"""
        fmt_map = {"txt": "纯文本文件 (*.txt)", "md": "Markdown 文件 (*.md)", "html": "HTML 文件 (*.html)", "docx": "Word 文档 (*.docx)"}
        filter_str = fmt_map.get(fmt, f"*.{fmt}")
        path, _ = QFileDialog.getSaveFileName(
            self, "导出文件", default_name, filter_str
        )
        return path if path else None

    def _on_export_chapter(self) -> None:
        """导出当前小说的当前章节"""
        title = self._bookshelf_combo.currentText().strip()
        if not title:
            QMessageBox.warning(self, "提示", "请先选择一本小说。")
            return
        chapters = self._novel_manager.list_chapters(title)
        if not chapters:
            QMessageBox.warning(self, "提示", f"小说「{title}」没有任何章节。")
            return
        # 找最新一章
        ch = chapters[-1]
        fmt = self._get_export_format(self._export_format_combo)
        default_name = f"第{ch['num']}章_{ch['title']}.{fmt}"
        output_path = self._prompt_save_path(default_name, fmt)
        if not output_path:
            return
        try:
            result = export_chapter(self._novel_manager, title, ch["num"], fmt, output_path)
            QMessageBox.information(self, "导出成功", f"章节已导出到：\n{result}")
        except Exception as e:
            QMessageBox.critical(self, "导出失败", f"导出出错：{e}")

    def _on_export_book(self) -> None:
        """导出整本小说"""
        title = self._bookshelf_combo.currentText().strip()
        if not title:
            QMessageBox.warning(self, "提示", "请先选择一本小说。")
            return
        fmt = self._get_export_format(self._export_format_combo)
        default_name = f"{title}_全集.{fmt}"
        output_path = self._prompt_save_path(default_name, fmt)
        if not output_path:
            return
        try:
            result = export_book(self._novel_manager, title, fmt, output_path)
            QMessageBox.information(self, "导出成功", f"全书已导出到：\n{result}")
        except Exception as e:
            QMessageBox.critical(self, "导出失败", f"导出出错：{e}")

    def _on_export_cont_chapter(self) -> None:
        """续写面板：导出当前章节"""
        title = self._get_current_book_title()
        if not title:
            QMessageBox.warning(self, "提示", "请先选择一本小说。")
            return
        chapters = self._novel_manager.list_chapters(title)
        if not chapters:
            QMessageBox.warning(self, "提示", f"小说「{title}」没有任何章节。")
            return
        ch = chapters[-1]
        fmt = self._get_export_format(self._cont_export_format_combo)
        default_name = f"第{ch['num']}章_{ch['title']}.{fmt}"
        output_path = self._prompt_save_path(default_name, fmt)
        if not output_path:
            return
        try:
            result = export_chapter(self._novel_manager, title, ch["num"], fmt, output_path)
            QMessageBox.information(self, "导出成功", f"章节已导出到：\n{result}")
        except Exception as e:
            QMessageBox.critical(self, "导出失败", f"导出出错：{e}")

    def _on_export_cont_book(self) -> None:
        """续写面板：导出整本小说"""
        title = self._get_current_book_title()
        if not title:
            QMessageBox.warning(self, "提示", "请先选择一本小说。")
            return
        fmt = self._get_export_format(self._cont_export_format_combo)
        default_name = f"{title}_全集.{fmt}"
        output_path = self._prompt_save_path(default_name, fmt)
        if not output_path:
            return
        try:
            result = export_book(self._novel_manager, title, fmt, output_path)
            QMessageBox.information(self, "导出成功", f"全书已导出到：\n{result}")
        except Exception as e:
            QMessageBox.critical(self, "导出失败", f"导出出错：{e}")

    def _on_export_conversation(self) -> None:
        """导出选中的对话历史"""
        conversation_id = None
        idx = self._history_combo.currentIndex()
        if idx >= 0:
            conversation_id = self._history_combo.itemData(idx)
        if not conversation_id:
            QMessageBox.warning(self, "提示", "请先选择一个已保存的对话。")
            return

        title_text = self._history_combo.itemText(idx)
        fmt = self._get_export_format(self._hist_export_format_combo)
        default_name = f"{title_text.split('(')[0].strip()}.{fmt}"
        output_path = self._prompt_save_path(default_name, fmt)
        if not output_path:
            return
        try:
            result = export_conversation(self._conversation_manager, conversation_id, fmt, output_path)
            QMessageBox.information(self, "导出成功", f"对话已导出到：\n{result}")
        except Exception as e:
            QMessageBox.critical(self, "导出失败", f"导出出错：{e}")

    # ========== 💬 对话历史管理 ==========

    def _refresh_history_list(self) -> None:
        """刷新对话历史下拉列表"""
        conversations = self._conversation_manager.list_conversations()
        current = self._history_combo.currentText()
        self._history_combo.blockSignals(True)
        self._history_combo.clear()
        if conversations:
            for c in conversations:
                mode_tag = f"[{c.strategy}] " if c.strategy else ""
                display = f"{mode_tag}{c.title} ({c.message_count}条, {c.updated_at[:16]})"
                self._history_combo.addItem(display, userData=c.conversation_id)
            # 恢复之前选中项
            for i in range(self._history_combo.count()):
                if self._history_combo.itemText(i) == current:
                    self._history_combo.setCurrentIndex(i)
                    break
            self._history_status_label.setText(
                f"共 {len(conversations)} 个已保存对话"
            )
        else:
            self._history_combo.addItem("（暂无已保存对话）")
            self._history_status_label.setText("暂无已保存对话")
        self._history_combo.blockSignals(False)
        # 显示当前选中项的预览
        self._on_history_selection_changed(self._history_combo.currentIndex())

    def _get_selected_history_id(self) -> str | None:
        """获取当前选中的对话历史 ID"""
        idx = self._history_combo.currentIndex()
        if idx < 0:
            return None
        data = self._history_combo.currentData()
        if data is None:
            return None
        return str(data)

    def _on_save_conversation(self) -> None:
        """保存当前对话到历史记录"""
        messages = self._client.export_messages()
        # 过滤掉 system prompt，只统计用户和助手的消息
        user_assistant = [m for m in messages if m.get("role") in ("user", "assistant")]
        if not user_assistant:
            QMessageBox.warning(self, "提示", "当前没有对话内容，无法保存。")
            return

        # 弹出对话框获取标题
        title, ok = QInputDialog.getText(
            self,
            "保存对话",
            "请输入对话标题：",
            text=self._current_conversation_title or ""
        )
        if not ok or not title.strip():
            return

        title = title.strip()
        if self._current_conversation_id:
            # 更新已有对话
            conversation_id = self._current_conversation_id
        else:
            # 新建对话
            conversation_id = self._conversation_manager.generate_id(title)

        # 保存当前策略名称，用于跨模式加载时自动切换
        strategy_name = self._client.strategy.get_name()

        # 保存策略专属设置
        char_desc = ""
        story_bg = ""
        reply_mode = ""
        if isinstance(self._client.strategy, RolePlayStrategy):
            char_desc = self._client.strategy.character_description
            story_bg = self._client.strategy.story_background
            reply_mode = self._client.strategy.reply_mode

        file_path = self._conversation_manager.save_conversation(
            conversation_id=conversation_id,
            title=title,
            model=self._client.model,
            messages=messages,
            character_description=char_desc,
            story_background=story_bg,
            strategy=strategy_name,
            reply_mode=reply_mode,
        )
        self._current_conversation_id = conversation_id
        self._current_conversation_title = title
        self._refresh_history_list()
        # 选中刚保存的对话
        for i in range(self._history_combo.count()):
            if self._history_combo.itemData(i) == conversation_id:
                self._history_combo.setCurrentIndex(i)
                break
        QMessageBox.information(
            self, "保存成功",
            f"对话「{title}」已保存（{len(user_assistant)} 条消息）\n{file_path}"
        )

    def _on_load_conversation(self) -> None:
        """加载选中的对话历史（最多加载最近50条消息）"""
        conversation_id = self._get_selected_history_id()
        if not conversation_id:
            QMessageBox.warning(self, "提示", "请先选择一个已保存的对话。")
            return

        record = self._conversation_manager.load_conversation(conversation_id)
        if not record:
            QMessageBox.warning(self, "错误", f"对话「{conversation_id}」加载失败，文件可能已损坏。")
            return

        all_messages = record.get("messages", [])
        if not all_messages:
            QMessageBox.warning(self, "提示", "该对话记录中没有任何消息。")
            return

        # 只取最近 50 条消息（如果超过），保留 system prompt 为首条
        messages = all_messages[-50:] if len(all_messages) > 50 else all_messages

        # ── 自动切换策略/模式 ──
        saved_strategy = record.get("strategy", "") or ""
        current_strategy = self._client.strategy.get_name()
        # 旧文件兼容：无 strategy 字段但存在角色数据时，推断为角色扮演
        if not saved_strategy:
            saved_char_desc = record.get("character_description", "") or ""
            saved_story_bg = record.get("story_background", "") or ""
            if saved_char_desc or saved_story_bg:
                saved_strategy = "角色扮演"
        if saved_strategy and saved_strategy != current_strategy:
            # 切换到对话保存时的模式，避免策略特定设置丢失
            strategy_cls = STRATEGY_OPTIONS.get(saved_strategy)
            if strategy_cls:
                self._loading_conversation = True
                self._mode_combo.setCurrentText(saved_strategy)
                self._loading_conversation = False

        # 导入消息到客户端（switch_strategy 已清空对话，需重新导入）
        self._client.import_messages(messages)
        self._current_conversation_id = conversation_id
        self._current_conversation_title = record.get("title", "")

        # 恢复角色扮演的角色描述、故事背景和回复方式
        saved_char_desc = record.get("character_description", "") or ""
        saved_story_bg = record.get("story_background", "") or ""
        saved_reply_mode = record.get("reply_mode", "") or ""
        if saved_char_desc or saved_story_bg or saved_reply_mode:
            self._role_char_edit.blockSignals(True)
            self._role_bg_edit.blockSignals(True)
            self._role_char_edit.setPlainText(saved_char_desc)
            self._role_bg_edit.setPlainText(saved_story_bg)
            self._role_char_edit.blockSignals(False)
            self._role_bg_edit.blockSignals(False)
            if isinstance(self._client.strategy, RolePlayStrategy):
                self._client.strategy.character_description = saved_char_desc
                self._client.strategy.story_background = saved_story_bg
                if saved_reply_mode:
                    self._client.strategy.reply_mode = saved_reply_mode
                    is_narrator = saved_reply_mode == RolePlayStrategy.REPLY_MODE_NARRATOR
                    self._radio_narrator.setChecked(is_narrator)
                    self._radio_character.setChecked(not is_narrator)
                self._client.update_system_prompt()

        # 同步模型设置
        saved_model = record.get("model", "")
        if saved_model and saved_model in MODEL_OPTIONS:
            self._client.switch_model(saved_model)
            self._model_combo.setCurrentText(saved_model)
            self._sync_sliders_to_client()
            self._update_status()

        # 重新渲染完整对话
        self._render_full_conversation(messages)
        self._refresh_history_list()
        QMessageBox.information(
            self, "加载成功",
            f"已加载对话「{record.get('title', '')}」（{len(messages)} 条消息）"
        )

    def _on_delete_conversation(self) -> None:
        """删除选中的对话历史"""
        conversation_id = self._get_selected_history_id()
        if not conversation_id:
            QMessageBox.warning(self, "提示", "请先选择一个已保存的对话。")
            return

        # 获取标题用于提示
        idx = self._history_combo.currentIndex()
        title_text = self._history_combo.itemText(idx)

        reply = QMessageBox.question(
            self,
            "确认删除",
            f"确定要删除对话「{title_text}」吗？\n此操作不可恢复！",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._conversation_manager.delete_conversation(conversation_id)
            if self._current_conversation_id == conversation_id:
                self._current_conversation_id = None
                self._current_conversation_title = ""
            self._refresh_history_list()
            QMessageBox.information(self, "成功", "对话已删除。")

    def _on_history_selection_changed(self, index: int) -> None:
        """对话历史选中项变化时显示预览"""
        if index < 0:
            return
        conversation_id = self._history_combo.currentData()
        if not conversation_id:
            return
        preview = self._conversation_manager.get_preview(conversation_id)
        self._history_status_label.setText(f"📝 预览：{preview}")

    def _render_full_conversation(self, messages: list[dict]) -> None:
        """拼接完整 HTML 后一次性渲染，避免 setHtml + runJavaScript 时序问题"""
        body_parts = []
        display_messages = messages[-50:] if len(messages) > 50 else messages
        for msg in display_messages:
            role = msg.get("role", "")
            content = msg.get("content", "")
            if role == "system":
                escaped = content.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br>")
                body_parts.append(f'<div class="system-msg"><em>[系统提示]</em><br>{escaped}</div>')
            elif role == "user":
                escaped = content.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br>")
                body_parts.append(f'<div class="user-msg"><strong>🧑 你：</strong><br>{escaped}</div>')
            elif role == "assistant":
                html_body = md_lib.markdown(
                    content,
                    extensions=["fenced_code", "tables", "codehilite", "nl2br", "sane_lists"],
                )
                body_parts.append(f'<div class="assistant-msg"><strong>🤖 助手：</strong><br>{html_body}</div>')

        full_html = f"<html><head>{HTML_STYLE}</head><body>{''.join(body_parts)}</body></html>"
        self._display.setHtml(full_html)

    # ========== 启动入口 ==========

def run_gui() -> None:
    """启动 GUI 应用"""
    app = QApplication(sys.argv)
    icon_path = os.path.join(os.path.dirname(__file__), "icon.svg")
    if os.path.exists(icon_path):
        icon = QIcon(icon_path)
        app.setWindowIcon(icon)
    window = DeepSeekChatGUI()
    window.showMaximized()
    sys.exit(app.exec())


if __name__ == "__main__":
    run_gui()
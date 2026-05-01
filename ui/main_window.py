"""
PyQt6 图形界面主窗口模块
- 启动时要求输入 API Key
- 实时 Markdown 渲染（通过 QWebEngineView）
- 模式切换、模型切换、温度/ top_p/ max_tokens/ frequency_penalty 调节
- 流式输出对话
- 小说写作模式：书架管理、章节控制、参数设定、自动摘要
"""

import sys
import threading

from PyQt6.QtCore import Qt, pyqtSignal, QObject
from PyQt6.QtWidgets import (
    QApplication,
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
    QSpinBox,
    QLineEdit,
    QCheckBox,
    QFrame,
    QRadioButton,
    QButtonGroup,
)
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
    CodeAssistantStrategy,
)


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


# ========== 模式配置 ==========

STRATEGY_OPTIONS = {
    "角色扮演": RolePlayStrategy,
    "小说写作": NovelStrategy,
    "代码助手": CodeAssistantStrategy,
}

MODEL_OPTIONS = [
    Config.MODEL_V4_FLASH,
    Config.MODEL_V4_PRO,
]


# ========== HTML / CSS 模板（深色主题） ==========

HTML_STYLE = """
<style>
  body {
    font-family: "Microsoft YaHei", "Segoe UI", Arial, sans-serif;
    font-size: 14px;
    line-height: 1.75;
    color: #e0e0e0;
    background-color: #1e1e1e;
    padding: 16px;
    margin: 0;
  }
  pre {
    background-color: #2d2d2d;
    border-radius: 6px;
    padding: 12px 16px;
    overflow-x: auto;
    font-family: "Consolas", "Courier New", monospace;
    font-size: 13px;
    line-height: 1.5;
    border: 1px solid #3a3a3a;
  }
  code {
    background-color: #3a3a3a;
    border-radius: 4px;
    padding: 2px 6px;
    font-family: "Consolas", "Courier New", monospace;
    font-size: 13px;
    color: #dcdcaa;
  }
  pre code {
    background-color: transparent;
    padding: 0;
    color: #d4d4d4;
  }
  blockquote {
    border-left: 4px solid #569cd6;
    margin-left: 0;
    padding-left: 16px;
    color: #9cdcfe;
    background-color: rgba(86, 156, 214, 0.1);
    border-radius: 0 6px 6px 0;
    padding: 8px 16px;
  }
  table {
    border-collapse: collapse;
    margin: 12px 0;
    width: 100%;
  }
  th, td {
    border: 1px solid #444;
    padding: 8px 12px;
    text-align: left;
  }
  th {
    background-color: #333;
    font-weight: bold;
  }
  a {
    color: #569cd6;
    text-decoration: none;
  }
  a:hover { text-decoration: underline; }
  h1, h2, h3, h4, h5, h6 {
    color: #569cd6;
    margin-top: 1.2em;
    margin-bottom: 0.6em;
  }
  hr {
    border: none;
    border-top: 1px solid #444;
    margin: 16px 0;
  }
  p { margin: 0.5em 0; }
  ul, ol { padding-left: 24px; }
  .user-msg {
    background-color: #264f78;
    border-radius: 10px;
    padding: 10px 14px;
    margin: 8px 0;
    color: #cee4ff;
    border: 1px solid #1a3a5c;
  }
  .assistant-msg {
    margin: 8px 0;
  }
  .system-msg {
    color: #6a9955;
    font-style: italic;
    margin: 8px 0;
  }
</style>
"""

# 初始页面模板
INITIAL_HTML = f"""
<html><head>{HTML_STYLE}</head><body>
<h2>🚀 DeepSeek 多功能聊天客户端</h2>
<p>请在左侧选择模式和模型，然后开始对话。</p>
<p><strong>当前可用模式：</strong></p>
<ul>
  <li><strong>角色扮演</strong> — 模拟特定人物/身份的对话风格</li>
  <li><strong>小说写作</strong> — 创意写作、情节构思、文笔润色（支持书架管理 + 章节续写）</li>
  <li><strong>代码助手</strong> — 编程帮助、Debug、代码审查</li>
</ul>
<p><strong>可用模型：</strong></p>
<ul>
  <li><strong>deepseek-v4-flash</strong> — v4 闪电版</li>
  <li><strong>deepseek-v4-pro</strong> — v4 专业版</li>
</ul>
<p style="color:#6a9955;">提示：若尚未配置 API Key，程序启动时会弹出输入框。</p>
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

        # 小说管理器
        self._novel_manager = NovelManager()
        # 对话历史管理器
        self._conversation_manager = ConversationManager()
        self._current_conversation_id: str | None = None
        self._current_conversation_title: str = ""

        # 累积的文本（用于流式追加）
        self._assistant_text_buffer: list[str] = []
        self._streaming = False

        # 先请求 API Key
        api_key = self._request_api_key()
        if not api_key:
            sys.exit(0)

        Config.API_KEY = api_key

        self._init_client()
        self._init_ui()
        self._apply_dark_theme()
        self._refresh_novel_bookshelf()

    # ========== API Key ==========

    def _request_api_key(self) -> str | None:
        """启动时弹出对话框要求输入 API Key"""
        existing = Config.API_KEY
        if existing and existing != "your_deepseek_api_key_here":
            return existing

        key, ok = QInputDialog.getText(
            None,
            "DeepSeek API Key",
            "请输入您的 DeepSeek API Key：\n"
            "（可在 https://platform.deepseek.com 获取）\n"
            "也可将 Key 写入 .env 文件后重启，跳过此步骤。",
            text=existing if existing != "your_deepseek_api_key_here" else "",
        )
        if ok and key.strip():
            return key.strip()
        return None

    # ========== 初始化 ==========

    def _init_client(self) -> None:
        """创建初始聊天客户端（默认角色扮演模式）"""
        strategy = RolePlayStrategy()
        self._client = DeepSeekChatClient(strategy=strategy, model=strategy.recommended_model)

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

        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([320, 880])

        self.setCentralWidget(splitter)

        self._display.setHtml(INITIAL_HTML)
        self._toggle_novel_panel(False)      # 初始隐藏小说面板
        self._toggle_role_play_panel(True)   # 初始显示角色扮演面板
        self._refresh_history_list()

    def _build_left_panel(self) -> QWidget:
        """构建左侧控制面板（含小说专属区域）"""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMaximumWidth(360)
        scroll.setMinimumWidth(280)

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setSpacing(6)
        layout.setContentsMargins(8, 8, 8, 8)

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
        btn_group = QGroupBox("操作")
        btn_layout = QVBoxLayout(btn_group)
        btn_layout.setContentsMargins(8, 4, 8, 4)
        btn_layout.setSpacing(4)

        clear_btn = QPushButton("🗑️ 清除对话")
        clear_btn.clicked.connect(self._on_clear)
        btn_layout.addWidget(clear_btn)

        layout.addWidget(btn_group)

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

        layout.addWidget(self._history_group)

        # ── 状态信息 ──
        status_group = QGroupBox("📋 状态")
        status_layout = QVBoxLayout(status_group)
        status_layout.setContentsMargins(8, 4, 8, 4)
        self._status_label = QLabel("当前模式: 角色扮演\n模型: deepseek-v4-flash")
        self._status_label.setWordWrap(True)
        status_layout.addWidget(self._status_label)
        layout.addWidget(status_group)

        # ── 🎭 角色扮演面板（默认显示，切换模式时隐藏）──
        self._role_play_panel = self._build_role_play_panel()
        layout.addWidget(self._role_play_panel)

        # ── 📚 小说写作面板（默认隐藏）──
        self._novel_panel = self._build_novel_panel()
        layout.addWidget(self._novel_panel)

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
        apply_btn.setMinimumHeight(32)
        apply_btn.clicked.connect(self._on_apply_role_settings)
        layout.addWidget(apply_btn)

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
        create_book_btn.setMaximumWidth(60)
        create_book_btn.clicked.connect(self._on_create_book)
        bookshelf_row.addWidget(create_book_btn)

        delete_book_btn = QPushButton("🗑 删除")
        delete_book_btn.setMaximumWidth(60)
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

        # ── 生成章节按钮 ──
        generate_btn = QPushButton("🚀 生成下一章")
        generate_btn.setMinimumHeight(36)
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
        self._display.setMinimumHeight(300)
        layout.addWidget(self._display, stretch=1)

        # 底部输入区
        input_frame = QFrame()
        input_frame.setFrameShape(QFrame.Shape.StyledPanel)
        input_layout = QHBoxLayout(input_frame)
        input_layout.setContentsMargins(8, 6, 8, 6)

        self._input_box = InputTextEdit()
        self._input_box.setPlaceholderText("在此输入消息，按 Ctrl+Enter 发送...")
        self._input_box.setMaximumHeight(100)
        self._input_box.setMinimumHeight(60)
        self._input_box.send_requested.connect(self._on_send)
        input_layout.addWidget(self._input_box, stretch=1)

        send_btn = QPushButton("发送")
        send_btn.setMinimumHeight(60)
        send_btn.setMinimumWidth(70)
        send_btn.clicked.connect(self._on_send)
        input_layout.addWidget(send_btn)

        layout.addWidget(input_frame)

        return widget

    # ========== 主题 ==========

    def _apply_dark_theme(self) -> None:
        """应用深色主题样式"""
        self.setStyleSheet("""
            QMainWindow {
                background-color: #1e1e1e;
            }
            QGroupBox {
                color: #c0c0c0;
                font-weight: bold;
                border: 1px solid #3a3a3a;
                border-radius: 6px;
                margin-top: 10px;
                padding-top: 10px;
                background-color: #252526;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 4px;
                color: #569cd6;
            }
            QComboBox {
                background-color: #333;
                color: #e0e0e0;
                border: 1px solid #555;
                border-radius: 4px;
                padding: 4px 8px;
                min-height: 22px;
            }
            QComboBox:hover { border-color: #569cd6; }
            QComboBox QAbstractItemView {
                background-color: #333;
                color: #e0e0e0;
                selection-background-color: #264f78;
            }
            QPushButton {
                background-color: #0e639c;
                color: white;
                border: none;
                border-radius: 4px;
                padding: 6px 14px;
                font-weight: bold;
                min-height: 22px;
            }
            QPushButton:hover { background-color: #1177bb; }
            QPushButton:pressed { background-color: #094771; }
            QSlider::groove:horizontal {
                background: #3a3a3a;
                height: 4px;
                border-radius: 2px;
            }
            QSlider::handle:horizontal {
                background: #569cd6;
                width: 12px;
                height: 12px;
                margin: -4px 0;
                border-radius: 6px;
            }
            QSlider::sub-page:horizontal {
                background: #0e639c;
                border-radius: 2px;
            }
            QLabel {
                color: #c0c0c0;
            }
            QTextEdit {
                background-color: #2d2d2d;
                color: #e0e0e0;
                border: 1px solid #444;
                border-radius: 4px;
                padding: 6px;
            }
            QScrollArea {
                background-color: #252526;
                border: none;
            }
            QSpinBox {
                background-color: #333;
                color: #e0e0e0;
                border: 1px solid #555;
                border-radius: 4px;
                padding: 2px 6px;
                min-height: 22px;
            }
            QSpinBox:hover { border-color: #569cd6; }
            QLineEdit {
                background-color: #2d2d2d;
                color: #e0e0e0;
                border: 1px solid #444;
                border-radius: 4px;
                padding: 4px 8px;
                min-height: 22px;
            }
            QLineEdit:hover { border-color: #569cd6; }
            QCheckBox {
                color: #c0c0c0;
            }
            QCheckBox::indicator {
                width: 16px;
                height: 16px;
                background-color: #333;
                border: 1px solid #555;
                border-radius: 3px;
            }
            QCheckBox::indicator:checked {
                background-color: #0e639c;
                border-color: #569cd6;
            }
        """)

    # ========== 信号处理 ==========

    def _on_mode_changed(self, text: str) -> None:
        """模式下拉框变化"""
        strategy_cls = STRATEGY_OPTIONS.get(text)
        if strategy_cls is None:
            return

        strategy = strategy_cls()
        self._client.switch_strategy(strategy)
        self._model_combo.setCurrentText(self._client.model)
        self._sync_sliders_to_client()
        self._update_status()

        # 切换专属面板可见性
        is_novel = isinstance(strategy, NovelStrategy)
        is_role_play = isinstance(strategy, RolePlayStrategy)
        self._toggle_novel_panel(is_novel)
        self._toggle_role_play_panel(is_role_play)

        if is_novel:
            self._refresh_novel_bookshelf()
            self._on_book_selected(self._bookshelf_combo.currentText())
            self._display.setHtml(md_to_html(strategy.get_welcome_message()))
        else:
            self._display.setHtml(INITIAL_HTML)

    def _on_model_changed(self, model: str) -> None:
        self._client.switch_model(model)
        self._update_status()

    def _on_temp_changed(self, value: int) -> None:
        temp = value / 100.0
        self._client.set_temperature(temp)
        self._temp_value.setText(f"{temp:.2f}")
        self._update_status()

    def _on_top_p_changed(self, value: int) -> None:
        top_p = value / 100.0
        self._client.set_top_p(top_p)
        self._top_p_value.setText(f"{top_p:.2f}")
        self._update_status()

    def _on_fp_changed(self, value: int) -> None:
        fp = value / 100.0
        self._client.set_frequency_penalty(fp)
        self._fp_value.setText(f"{fp:.2f}")
        self._update_status()

    def _on_mt_changed(self, value: int) -> None:
        self._client.set_max_tokens(value)
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

    def _toggle_role_play_panel(self, visible: bool) -> None:
        self._role_play_panel.setVisible(visible)

    def _toggle_novel_panel(self, visible: bool) -> None:
        self._novel_panel.setVisible(visible)

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
        if isinstance(self._client.strategy, RolePlayStrategy):
            self._client.strategy.reply_mode = (
                RolePlayStrategy.REPLY_MODE_NARRATOR
                if button_id == 1
                else RolePlayStrategy.REPLY_MODE_CHARACTER
            )

    def _on_apply_role_settings(self) -> None:
        """将角色描述、故事背景、回复方式写入 system prompt，并重置对话上下文"""
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
        self._client.clear_context(keep_system=True)
        char = self._client.strategy.character_description.strip()
        bg = self._client.strategy.story_background.strip()
        is_narrator = self._client.strategy.reply_mode == RolePlayStrategy.REPLY_MODE_NARRATOR
        mode_text = "旁白描述（第三人称）" if is_narrator else "角色回答（第一人称）"
        notice_parts = [f"🎭 **角色设定已应用，对话已重置。**\n", f"**回复方式：** {mode_text}\n"]
        if char:
            notice_parts.append(f"**角色描述：** {char[:80]}{'…' if len(char) > 80 else ''}\n")
        if bg:
            notice_parts.append(f"**故事背景：** {bg[:80]}{'…' if len(bg) > 80 else ''}\n")
        if not char and not bg:
            notice_parts.append("（未填写角色描述或故事背景，使用默认角色扮演模式）\n")
        notice_parts.append("\n现在可以开始对话了。")
        self._display.setHtml(md_to_html("".join(notice_parts)))

    # ========== 📚 小说面板事件 ==========

    def _refresh_novel_bookshelf(self) -> None:
        """刷新书架下拉列表"""
        books = self._novel_manager.list_books()
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
        """获取当前书架选中项，若为占位符则返回 None"""
        text = self._bookshelf_combo.currentText()
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

    def _on_protagonist_changed(self) -> None:
        if isinstance(self._client.strategy, NovelStrategy):
            self._client.strategy.protagonist_bio = self._protagonist_edit.toPlainText().strip()

    def _on_background_changed(self) -> None:
        if isinstance(self._client.strategy, NovelStrategy):
            self._client.strategy.background_story = self._background_edit.toPlainText().strip()

    def _on_demand_changed(self) -> None:
        if isinstance(self._client.strategy, NovelStrategy):
            self._client.strategy.writing_demand = self._demand_edit.toPlainText().strip()

    def _on_chapter_mode_toggled(self, checked: bool) -> None:
        if isinstance(self._client.strategy, NovelStrategy):
            self._client.strategy.chapter_mode = checked
            self._client.clear_context()
            self._update_status()
            if checked:
                self._display.setHtml(md_to_html(
                    "📖 **章节续写模式已开启**\n"
                    "点击「生成下一章」按钮或直接发送消息，将自动根据设定生成新章节。\n"
                ))
            else:
                self._display.setHtml(md_to_html(
                    "💬 **自由对话模式** — 可随意交流写作问题。"
                ))

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

        self._append_user_message(f"📖 生成第{self._novel_manager.get_next_chapter_num(title)}章：{chapter_title}")

        threading.Thread(
            target=self._run_chapter_generation,
            args=(title, chapter_title),
            daemon=True,
        ).start()

    def _build_chapter_prompt(self, title: str, chapter_title: str) -> str:
        """构造章节续写的完整 User Prompt（含历史记录参考）"""
        chapter_num = self._novel_manager.get_next_chapter_num(title)

        # 智能前情提要（剧情摘要）
        client = self._client.raw_client if hasattr(self, '_client') else None
        summary = self._novel_manager.load_smart_summary(
            title,
            client=client,
            next_chapter_num=chapter_num,
            max_recent=3,
        )

        # 历史记录总结（前面各章的生成配置与风格参考）
        history = self._novel_manager.build_history_summary(title, exclude_chapter=chapter_num)

        bio = self._protagonist_edit.toPlainText().strip()
        bg = self._background_edit.toPlainText().strip()
        demand = self._demand_edit.toPlainText().strip()

        # 用户填入的本章节情节内容
        plot_content = self._plot_edit.toPlainText().strip()

        parts = [f"【前情提要】：\n{summary}\n"]
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

        return "\n".join(parts)

    def _run_chapter_generation(self, title: str, chapter_title: str) -> None:
        """后台线程：生成章节 + 版本保存 + 摘要"""
        try:
            chapter_num = self._novel_manager.get_next_chapter_num(title)

            target_words = max(2000, self._client.max_tokens // 2)
            messages = [
                {"role": "system", "content": f"你是一位文笔细腻、想象力丰富的小说家。请直接输出小说正文，不要加任何解释或前言。本章字数不少于{target_words}字，情节饱满，细节丰富，场景描写生动。"},
            ]

            bg = self._background_edit.toPlainText().strip()
            bio = self._protagonist_edit.toPlainText().strip()
            if bg:
                messages.append({"role": "system", "content": f"【核心设定】：\n{bg}"})
            if bio:
                messages.append({"role": "system", "content": f"【人物背景】：\n{bio}"})

            user_prompt = self._build_chapter_prompt(title, chapter_title)
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

            # 保存生成历史记录
            content_preview = content[:500].replace("\n", " ")
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
            )

            if new_chapter:
                self._stream_signals.token.emit("\n🔍 正在提炼剧情记忆...\n")
                summary = self._novel_manager.generate_summary(
                    self._client.raw_client, content, chapter_num, chapter_title
                )
                self._novel_manager.append_summary(
                    title, chapter_num, chapter_title, summary
                )
                self._stream_signals.token.emit(f"📋 剧情摘要已同步至记忆库。\n\n")

            self._refresh_chapter_info_display(title)
            next_ch = self._novel_manager.get_next_chapter_num(title)

            self._stream_signals.token.emit(
                f"📖 下一章：第{next_ch}章（请修改章节标题后再次生成）\n"
            )

            self._stream_signals.finished.emit()
        except Exception as e:
            self._stream_signals.error.emit(f"章节生成失败: {e}")

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

    def _on_stream_finished(self) -> None:
        """主线程：流式完成"""
        self._streaming = False
        full_text = "".join(self._assistant_text_buffer)
        self._render_assistant_message(full_text)

    def _on_stream_error(self, error_msg: str) -> None:
        """主线程：流式出错"""
        self._streaming = False
        QMessageBox.critical(self, "API 错误", f"调用失败：{error_msg}")

    # ========== 渲染 ==========

    def _append_user_message(self, text: str) -> None:
        """追加用户消息到显示区域"""
        escaped = text.replace("&", "&").replace("<", "<").replace(">", ">")
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
        """刷新章节信息显示"""
        chapters = self._novel_manager.list_chapters(title)
        if not chapters:
            self._chapter_info_label.setText(
                f"暂无章节，下一章编号: 第{self._novel_manager.get_next_chapter_num(title)}章"
            )
            return
        lines = [f"已有 {len(chapters)} 章，下一章: 第{self._novel_manager.get_next_chapter_num(title)}章"]
        for ch in chapters:
            active = ch.get("active_version", 1)
            count = ch.get("version_count", 1)
            if count > 1:
                lines.append(f"  · 第{ch['num']}章「{ch['title']}」v{active}/{count}个版本")
            else:
                lines.append(f"  · 第{ch['num']}章「{ch['title']}」")
        self._chapter_info_label.setText("\n".join(lines))

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
                preview = content[:2000]
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
                            self._client.raw_client, self._book_title
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

                threading.Thread(target=self._do_regenerate, args=(data,), daemon=True).start()

            def _do_regenerate(self, data: dict) -> None:
                try:
                    parent = self.parent()
                    chapter_num = data["chapter_num"]
                    chapter_title = data.get("title", f"第{chapter_num}章")

                    bg = parent._background_edit.toPlainText().strip()
                    bio = parent._protagonist_edit.toPlainText().strip()
                    demand = parent._demand_edit.toPlainText().strip()

                    messages = [
                        {"role": "system", "content": "你是一位文笔细腻、想象力丰富的小说家。请直接输出小说正文，不要加任何解释或前言。"},
                    ]
                    if bg:
                        messages.append({"role": "system", "content": f"【核心设定】：\n{bg}"})
                    if bio:
                        messages.append({"role": "system", "content": f"【人物背景】：\n{bio}"})
                    if demand:
                        messages.append({"role": "system", "content": f"【写作要求】：\n{demand}"})

                    summary = self._novel_mgr.load_smart_summary(
                        self._book_title, client=self._client.raw_client,
                        next_chapter_num=chapter_num, max_recent=10,
                    )

                    old_content = self._novel_mgr.read_chapter_version(
                        self._book_title, chapter_num, data["version"]
                    )

                    user_parts = [f"请创作小说的第 {chapter_num} 章，标题为「{chapter_title}」。\n"]
                    if summary and summary != "故事刚刚开始。":
                        user_parts.append(f"【前情提要】\n{summary}\n")
                    if old_content:
                        preview = old_content[:600].strip()
                        user_parts.append(
                            f"【参考：旧版本开头（你不需要完全照搬，仅用于保持风格一致性）】\n{preview}\n"
                        )
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

    # ========== 💬 对话历史管理 ==========

    def _refresh_history_list(self) -> None:
        """刷新对话历史下拉列表"""
        conversations = self._conversation_manager.list_conversations()
        current = self._history_combo.currentText()
        self._history_combo.blockSignals(True)
        self._history_combo.clear()
        if conversations:
            for c in conversations:
                display = f"{c.title} ({c.message_count}条消息, {c.updated_at[:16]})"
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

        file_path = self._conversation_manager.save_conversation(
            conversation_id=conversation_id,
            title=title,
            model=self._client.model,
            messages=messages,
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

        # 导入消息到客户端
        self._client.import_messages(messages)
        self._current_conversation_id = conversation_id
        self._current_conversation_title = record.get("title", "")

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

    def _render_full_conversation(self, messages: list[dict]) -> None:
        """根据消息列表重新渲染完整对话到显示区域（最多显示最近50条）"""
        self._display.setHtml("")
        self._display.setHtml("<html><head>" + HTML_STYLE + "</head><body></body></html>")

        # 只渲染最近 50 条消息
        display_messages = messages[-50:] if len(messages) > 50 else messages
        for msg in display_messages:
            role = msg.get("role", "")
            content = msg.get("content", "")
            if role == "system":
                # system 消息以特殊样式显示
                escaped = content.replace("&", "&").replace("<", "<").replace(">", ">")
                escaped = escaped.replace("\n", "<br>")
                js_safe = self._escape_for_js(escaped)
                script = f"""
                    (function() {{
                        var div = document.createElement('div');
                        div.className = 'system-msg';
                        div.innerHTML = '<em>[系统提示]</em><br>' + `{js_safe}`;
                        document.body.appendChild(div);
                    }})();
                """
                self._display.page().runJavaScript(script)
            elif role == "user":
                self._append_user_message(content)
            elif role == "assistant":
                html_body = md_lib.markdown(
                    content,
                    extensions=["fenced_code", "tables", "codehilite", "nl2br", "sane_lists"],
                )
                escaped_body = self._escape_for_js(html_body)
                script = f"""
                    (function() {{
                        var div = document.createElement('div');
                        div.className = 'assistant-msg';
                        div.innerHTML = '<strong>🤖 助手：</strong><br>' + `{escaped_body}`;
                        document.body.appendChild(div);
                    }})();
                """
                self._display.page().runJavaScript(script)

        # 滚动到底部
        self._display.page().runJavaScript("window.scrollTo(0, document.body.scrollHeight);")

    # ========== 启动入口 ==========

def run_gui() -> None:
    """启动 GUI 应用"""
    app = QApplication(sys.argv)
    window = DeepSeekChatGUI()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    run_gui()
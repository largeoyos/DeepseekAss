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
import time
import copy
from dataclasses import asdict

from PyQt6.QtCore import Qt, pyqtSignal, QObject, QTimer
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
    QListWidget,
    QListWidgetItem,
    QAbstractItemView,
)
from PyQt6.QtGui import QColor, QIcon
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebEngineCore import QWebEngineSettings
import markdown as md_lib

from config import Config
from core.chat_client import DeepSeekChatClient
from core.novel_manager import NovelManager
from core.conversation_manager import ConversationManager
from core.character_book import (
    CharacterBookManager,
    CharacterProfile,
    dict_to_timeline,
    timeline_to_dict,
    character_book_to_dict,
    dict_to_character_book,
    find_profile,
    extract_character_book_changes,
)
from core.chat_domain import (
    ChatMessage,
    ChatSessionState,
    ScenePresetManager,
    SenderProfileManager,
    TurnPolicy,
    apply_memory_change_set,
    fork_branch,
    legacy_messages_to_structured,
    new_id,
    now_text,
    parse_structured_reply,
    revert_memory_change_set,
    state_from_dict,
    state_to_dict,
    structured_to_legacy_messages,
)
from core.settings_manager import SettingsManager
from core.token_log_manager import TokenLogManager
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
from ui.character_book_dialog import CharacterBookDialog, CharacterProfileDialog
from ui.chat_control_dialog import ChatControlDialog
from ui.presets import PRESETS, CUSTOM_LABEL
from ui.settings_dialog import SettingsDialog
from ui.token_log_dialog import TokenLogDialog
from ui.chapter_tree_dialog import ChapterTreeDialog
from ui.continuation_dialogs import (
    analyze_source_text, suggest_directions,
    ContinuationAnalysisDialog, DirectionSelectionDialog,
    SectionPreviewDialog,
)
from utils.genre_styles import (
    GENRE_DISPLAY_NAMES, TONE_DISPLAY_NAMES,
    get_genre_by_display, get_genre_by_key,
    get_tone_by_display, get_tone_by_key,
    get_genre_display, get_tone_display,
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
    character_book_sync_status = pyqtSignal(str)   # 后台人物书同步状态
    character_book_sync_status = pyqtSignal(str)   # 后台人物书同步状态


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
  .reply-jump-nav { position: fixed; top: 14px; right: 14px; z-index: 1000; width: 176px; max-height: calc(100vh - 28px); overflow: hidden; background: #181826; border: 1px solid rgba(255,255,255,.12); border-radius: 10px; box-shadow: 0 8px 28px rgba(0,0,0,.32); }
  .reply-jump-nav summary { padding: 9px 12px; cursor: pointer; color: #d7e7ff; font-size: 13px; font-weight: 700; user-select: none; }
  .reply-jump-list { max-height: calc(100vh - 74px); overflow-y: auto; padding: 0 7px 8px; }
  .reply-jump-item { display: block; width: 100%; margin: 3px 0; padding: 7px 9px; border: 0; border-radius: 7px; background: transparent; color: #aebdd3; text-align: left; font: inherit; font-size: 12px; cursor: pointer; }
  .reply-jump-item:hover { background: #264f78; color: #fff; }
  .assistant-msg { scroll-margin-top: 18px; }
</style>
"""

LIGHT_HTML_STYLE = """
<style>
  * { scrollbar-width: thin; scrollbar-color: #b7c1d3 #edf1f7; }
  ::-webkit-scrollbar { width: 8px; height: 8px; }
  ::-webkit-scrollbar-track { background: #edf1f7; }
  ::-webkit-scrollbar-thumb { background: #b7c1d3; border-radius: 4px; }
  body {
    font-family: "Microsoft YaHei", "Segoe UI", -apple-system, Arial, sans-serif;
    font-size: 14.5px;
    line-height: 1.8;
    color: #202635;
    background: linear-gradient(135deg, #f4f6fb 0%, #ffffff 55%, #eef3ff 100%);
    padding: 20px;
    margin: 0;
  }
  .user-msg, .assistant-msg, .system-msg { animation: fadeIn 0.25s ease-out; }
  @keyframes fadeIn {
    from { opacity: 0; transform: translateY(6px); }
    to { opacity: 1; transform: translateY(0); }
  }
  .user-msg {
    background: linear-gradient(135deg, #dbe7ff 0%, #c6dcff 100%);
    border-radius: 12px 12px 4px 12px;
    padding: 12px 18px;
    margin: 10px 0 10px 20%;
    color: #123a8a;
    border: 1px solid #9fbcff;
    font-size: 14px;
    line-height: 1.7;
  }
  .assistant-msg {
    margin: 10px 20% 10px 0;
    padding: 12px 18px;
    background: rgba(255, 255, 255, 0.9);
    border-radius: 12px 12px 12px 4px;
    border: 1px solid #dce2ef;
    box-shadow: 0 2px 8px rgba(15, 23, 42, 0.06);
    font-size: 14px;
    line-height: 1.8;
  }
  .system-msg {
    color: #287044;
    font-style: italic;
    margin: 10px auto;
    text-align: center;
    font-size: 13px;
    padding: 6px 12px;
    background: rgba(40, 112, 68, 0.08);
    border-radius: 8px;
    max-width: 80%;
  }
  pre {
    background: #f0f4fb !important;
    border-radius: 8px;
    padding: 14px 18px;
    overflow-x: auto;
    border: 1px solid #dce2ef;
  }
  code {
    background: #eef3ff;
    border-radius: 4px;
    padding: 2px 7px;
    color: #1d4ed8;
  }
  pre code { background: transparent; padding: 0; color: #202635; }
  blockquote {
    border-left: 3px solid #2563eb;
    margin: 10px 0;
    padding: 8px 18px;
    color: #475569;
    background: linear-gradient(90deg, rgba(37, 99, 235, 0.08) 0%, transparent 100%);
    border-radius: 0 6px 6px 0;
  }
  table { border-collapse: collapse; margin: 14px 0; width: 100%; border-radius: 8px; overflow: hidden; }
  th, td { border: 1px solid #dce2ef; padding: 10px 14px; text-align: left; }
  th { background: #eef3ff; color: #1d4ed8; font-weight: 600; }
  td { background: rgba(255, 255, 255, 0.65); }
  tr:nth-child(even) td { background: rgba(244, 246, 251, 0.9); }
  a { color: #2563eb; text-decoration: none; }
  h1, h2, h3, h4, h5, h6 { color: #1d4ed8; margin-top: 1.3em; margin-bottom: 0.5em; font-weight: 600; }
  h1 { font-size: 1.6em; border-bottom: 1px solid #dbe7ff; padding-bottom: 8px; }
  h2 { font-size: 1.35em; }
  h3 { font-size: 1.2em; }
  hr { border: none; height: 1px; background: #dce2ef; margin: 20px 0; }
  p { margin: 0.6em 0; }
  ul, ol { padding-left: 26px; }
  li { margin: 4px 0; }
  img { max-width: 100%; border-radius: 8px; margin: 8px 0; }
  .reply-jump-nav { position: fixed; top: 14px; right: 14px; z-index: 1000; width: 176px; max-height: calc(100vh - 28px); overflow: hidden; background: #ffffff; border: 1px solid #d7dfed; border-radius: 10px; box-shadow: 0 8px 28px rgba(30,50,90,.16); }
  .reply-jump-nav summary { padding: 9px 12px; cursor: pointer; color: #244a8f; font-size: 13px; font-weight: 700; user-select: none; }
  .reply-jump-list { max-height: calc(100vh - 74px); overflow-y: auto; padding: 0 7px 8px; }
  .reply-jump-item { display: block; width: 100%; margin: 3px 0; padding: 7px 9px; border: 0; border-radius: 7px; background: transparent; color: #526078; text-align: left; font: inherit; font-size: 12px; cursor: pointer; }
  .reply-jump-item:hover { background: #dbe7ff; color: #123a8a; }
  .assistant-msg { scroll-margin-top: 18px; }
</style>
"""

CURRENT_HTML_STYLE = HTML_STYLE

REPLY_JUMP_NAV_SCRIPT = """
function rebuildReplyJumpNav() {
    var replies = Array.from(document.querySelectorAll('.assistant-msg'));
    var nav = document.getElementById('reply-jump-nav');
    if (!replies.length) {
        if (nav) nav.remove();
        return;
    }
    if (!nav) {
        nav = document.createElement('details');
        nav.id = 'reply-jump-nav';
        nav.className = 'reply-jump-nav';
        nav.open = true;
        nav.innerHTML = '<summary>回复目录</summary><div class="reply-jump-list"></div>';
        document.body.appendChild(nav);
    }
    var list = nav.querySelector('.reply-jump-list');
    list.innerHTML = '';
    replies.forEach(function(reply, index) {
        var button = document.createElement('button');
        button.type = 'button';
        button.className = 'reply-jump-item';
        button.textContent = '回复 ' + (index + 1);
        button.onclick = function() {
            reply.scrollIntoView({behavior: 'smooth', block: 'start'});
        };
        list.appendChild(button);
    });
}
"""


def initial_html() -> str:
    """按当前主题生成欢迎页 HTML。"""
    is_light = CURRENT_HTML_STYLE == LIGHT_HTML_STYLE
    muted = "#64748b" if is_light else "#888"
    card_bg = "rgba(255,255,255,0.80)" if is_light else "rgba(255,255,255,0.03)"
    card_border = "#dce2ef" if is_light else "rgba(255,255,255,0.06)"
    tip_color = "#287044" if is_light else "#6a9955"
    tip_bg = "rgba(40,112,68,0.08)" if is_light else "rgba(106,153,85,0.08)"
    return f"""
<html><head>{CURRENT_HTML_STYLE}</head><body>
<div style="text-align:center; padding: 40px 20px;">
  <div style="font-size: 48px; margin-bottom: 16px;">🚀</div>
  <h1 style="border:none; font-size: 1.8em;">DeepSeek 多功能聊天客户端</h1>
  <p style="color: {muted}; font-size: 14px; margin-bottom: 32px;">请在最左侧栏选择模式，然后在控制面板调整模型和参数</p>

  <div style="display:inline-block; text-align:left; max-width:520px; background:{card_bg}; border-radius:12px; padding:24px 32px; border:1px solid {card_border};">
    <h3 style="margin-top:0; font-size:15px;">当前可用模式</h3>
    <table style="box-shadow:none;">
      <tr><td style="border:none; padding:8px 0;"><strong>🎭 角色扮演</strong></td><td style="border:none; padding:8px 0; color:{muted};">模拟特定人物/身份的对话风格</td></tr>
      <tr><td style="border:none; padding:8px 0;"><strong>📚 小说写作</strong></td><td style="border:none; padding:8px 0; color:{muted};">创意写作、情节构思、文笔润色（支持书架管理 + 章节续写）</td></tr>
      <tr><td style="border:none; padding:8px 0;"><strong>📄 续写小说</strong></td><td style="border:none; padding:8px 0; color:{muted};">导入已有文本并延续故事</td></tr>
    </table>

    <h3 style="margin-top:20px; font-size:15px;">可用模型</h3>
    <table style="box-shadow:none;">
      <tr><td style="border:none; padding:6px 0;"><code>deepseek-v4-flash</code></td><td style="border:none; padding:6px 0; color:{muted};">v4 闪电版</td></tr>
      <tr><td style="border:none; padding:6px 0;"><code>deepseek-v4-pro</code></td><td style="border:none; padding:6px 0; color:{muted};">v4 专业版</td></tr>
    </table>

    <p style="color:{tip_color}; font-size: 13px; margin-top: 24px; text-align:center; background:{tip_bg}; border-radius:6px; padding:8px;">设置和 Token 日志在最左侧栏底部</p>
  </div>
</div>
</body></html>
"""


INITIAL_HTML = initial_html()


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
    return f"<html><head>{CURRENT_HTML_STYLE}</head><body>{md_body}</body></html>"


class _UsageLoggingCompletionsProxy:
    def __init__(self, completions, owner, operation: str):
        self._completions = completions
        self._owner = owner
        self._operation = operation

    def create(self, *args, **kwargs):
        response = self._completions.create(*args, **kwargs)
        messages = kwargs.get("messages") or []
        prompt = "\n\n".join(
            str(m.get("content", "")) for m in messages if m.get("role") == "user"
        )
        choices = getattr(response, "choices", []) or []
        content = ""
        if choices:
            message = getattr(choices[0], "message", None)
            content = getattr(message, "content", "") or ""
        usage = getattr(response, "usage", None)
        self._owner._log_token_usage(
            operation=self._operation,
            direction="send",
            content=prompt,
            usage=usage,
            model=kwargs.get("model"),
        )
        self._owner._log_token_usage(
            operation=self._operation,
            direction="receive",
            content=content,
            usage=usage,
            model=kwargs.get("model"),
        )
        return response

    def __getattr__(self, name):
        return getattr(self._completions, name)


class _UsageLoggingChatProxy:
    def __init__(self, chat, owner, operation: str):
        self._chat = chat
        self.completions = _UsageLoggingCompletionsProxy(chat.completions, owner, operation)

    def __getattr__(self, name):
        return getattr(self._chat, name)


class _UsageLoggingClientProxy:
    def __init__(self, client, owner, operation: str):
        self._client = client
        self.chat = _UsageLoggingChatProxy(client.chat, owner, operation)

    def __getattr__(self, name):
        return getattr(self._client, name)


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
        self._stream_signals.novel_imported.connect(self._on_cont_novel_imported)
        self._stream_signals.refresh_chapter_info.connect(self._refresh_chapter_info_display)
        self._stream_signals.character_book_sync_status.connect(
            self._on_character_book_sync_status
        )
        self._stream_signals.character_book_sync_status.connect(
            self._on_character_book_sync_status
        )

        # 认证与加密
        self._auth = None
        self._enc_key: bytes | None = None
        self._username: str = ""

        # 累积的文本（用于流式追加）
        self._assistant_text_buffer: list[str] = []
        self._assistant_char_count = 0
        self._stream_render_timer = QTimer(self)
        self._stream_render_timer.setSingleShot(True)
        self._stream_render_timer.setInterval(80)
        self._stream_render_timer.timeout.connect(self._flush_stream_render)
        self._streaming = False
        self._streaming_start_time = 0.0
        # 章节渲染完成标志（防止 JS 异步渲染前触发下一章）
        self._chapter_finalized = True
        # 正在加载对话（阻止模式切换时覆盖显示）
        self._loading_conversation = False
        # 参数预设守卫：预设驱动滑块时阻止 handler 切回"自定义"
        self._preset_applying = False
        self._settings_applying = False
        # 模式切换守卫：记录上次有效模式，用于 streaming 时回退
        self._last_mode: str = ""

        # Step 1: 登录
        self._login_and_init()

    # ========== 登录 ==========

    def _login_and_init(self) -> None:
        """登录流程：认证 → 解密数据 → 初始化客户端"""
        from ui.login_dialog import LoginDialog
        dlg = LoginDialog(self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            sys.exit(0)

        self._username = dlg.username
        self._enc_key = dlg.enc_key

        from core.auth_manager import AuthManager
        self._auth = AuthManager()

        # 初始化管理器（用户独立路径 + 加密）
        user_dir = self._auth.get_user_dir(self._username)
        os.makedirs(os.path.join(user_dir, "conversations"), exist_ok=True)
        os.makedirs(os.path.join(user_dir, "bookshelf"), exist_ok=True)

        self._user_dir = user_dir
        self._novel_manager = NovelManager(
            bookshelf_root=os.path.join(user_dir, "bookshelf"),
            crypto=self._auth, enc_key=self._enc_key,
        )
        self._conversation_manager = ConversationManager(
            root_dir=os.path.join(user_dir, "conversations"),
            crypto=self._auth, enc_key=self._enc_key,
        )
        self._character_book_manager = CharacterBookManager(
            root_dir=user_dir,
            crypto=self._auth,
            enc_key=self._enc_key,
        )
        self._sender_profile_manager = SenderProfileManager(
            root_dir=user_dir,
            crypto=self._auth,
            enc_key=self._enc_key,
        )
        self._scene_preset_manager = ScenePresetManager(
            root_dir=user_dir,
            crypto=self._auth,
            enc_key=self._enc_key,
        )
        self._settings_manager = SettingsManager(
            root_dir=user_dir,
            crypto=self._auth,
            enc_key=self._enc_key,
        )
        self._token_log_manager = TokenLogManager(
            root_dir=user_dir,
            crypto=self._auth,
            enc_key=self._enc_key,
        )
        self._settings = self._settings_manager.load()
        self._presets = self._settings.get("presets", PRESETS).copy()
        self._model_options = self._build_model_options()
        self._current_conversation_id: str | None = None
        self._current_conversation_title: str = ""
        self._current_chat_type: str = "private"
        self._participant_character_ids: list[str] = []
        self._primary_character_id: str = ""
        self._chat_timeline = []
        self._last_chat_user_input: str = ""
        self._sender_name: str = "你"
        self._sender_profile: str = ""
        self._required_responder_ids: list[str] = []
        self._conversation_dirty: bool = False
        self._chat_state = ChatSessionState()
        self._chat_state.active_branch()
        self._sender_profiles = self._sender_profile_manager.load()
        self._scene_presets = self._scene_preset_manager.load()
        self._last_structured_assistant_messages: list[ChatMessage] = []
        self._character_book_sync_lock = threading.Lock()

        # Step 2: 获取 API Key（加密存储或弹窗输入）
        api_key, base_url = self._load_encrypted_config()
        if not api_key:
            api_key = self._get_api_key_with_retry()
            if not api_key:
                sys.exit(0)
            # 首次输入 → 加密保存
            self._save_encrypted_config(api_key, Config.BASE_URL)

        if base_url:
            Config.BASE_URL = base_url
        Config.API_KEY = api_key

        # Step 3: 初始化客户端
        self._init_client()
        loaded_prompt = self._load_global_user_prompt()
        if loaded_prompt:
            self._client.global_user_prompt = loaded_prompt  # type: ignore[union-attr]

        # Step 3.5: 检测旧目录数据并提示迁移
        self._try_migrate_old_data()

        # Step 4: 构建 UI
        self._init_ui()
        self._apply_settings_to_controls()
        self._apply_theme()
        self._refresh_novel_bookshelf()

    # ========== 加密配置 ==========

    def _encrypted_config_path(self) -> str:
        """用户加密配置路径"""
        return os.path.join(self._auth.get_user_dir(self._username), "config.enc")

    def _load_encrypted_config(self) -> tuple[str, str]:
        """从加密存储加载 API Key 和 Base URL"""
        if not self._enc_key:
            return Config.API_KEY, Config.BASE_URL
        data = self._auth.decrypt_json(self._enc_key, self._encrypted_config_path())
        if data:
            return data.get("api_key", ""), data.get("base_url", Config.BASE_URL)
        return "", Config.BASE_URL

    def _save_encrypted_config(self, api_key: str, base_url: str) -> None:
        """加密保存 API Key 和 Base URL"""
        if self._enc_key:
            self._auth.encrypt_json(self._enc_key, self._encrypted_config_path(), {
                "api_key": api_key,
                "base_url": base_url,
            })

    def _user_prefs_path(self) -> str:
        """用户加密偏好文件路径"""
        return os.path.join(self._auth.get_user_dir(self._username), "user_prefs.enc")

    # ========== 旧数据迁移 ==========

    def _try_migrate_old_data(self) -> None:
        """检测旧目录有无明文数据，提示用户加密导入"""
        from core.novel_manager import BOOKSHELF_DIR, NovelManager
        from core.conversation_manager import CONVERSATIONS_DIR, ConversationManager

        old_bookshelf = BOOKSHELF_DIR
        old_conversations = CONVERSATIONS_DIR

        has_books = (
            os.path.isdir(old_bookshelf)
            and any(
                os.path.isdir(os.path.join(old_bookshelf, d))
                for d in os.listdir(old_bookshelf)
            )
        )
        has_convs = (
            os.path.isdir(old_conversations)
            and any(f.endswith(".json") for f in os.listdir(old_conversations))
        )
        if not has_books and not has_convs:
            return

        # 不重复导入（仅当用户数据已完整包含旧数据时跳过）
        user_bookshelf = os.path.join(self._auth.get_user_dir(self._username), "bookshelf")
        user_convs = os.path.join(self._auth.get_user_dir(self._username), "conversations")

        old_book_set = {
            d for d in os.listdir(old_bookshelf)
            if os.path.isdir(os.path.join(old_bookshelf, d))
        }
        user_book_set = {
            d for d in os.listdir(user_bookshelf)
            if os.path.isdir(os.path.join(user_bookshelf, d))
        } if os.path.isdir(user_bookshelf) else set()
        old_conv_count = len(
            [f for f in os.listdir(old_conversations) if f.endswith(".json")]
        ) if os.path.isdir(old_conversations) else 0
        user_conv_count = len(
            [f for f in os.listdir(user_convs) if f.endswith((".json", ".json.enc"))]
        ) if os.path.isdir(user_convs) else 0

        # 旧数据全部已迁移 → 跳过
        if old_book_set.issubset(user_book_set) and old_conv_count == user_conv_count:
            return

        # 仅迁移缺失的部分
        missing_books = old_book_set - user_book_set
        missing_convs = old_conv_count > user_conv_count

        # 弹窗确认
        parts = []
        if missing_books:
            parts.append(f"📚 {len(missing_books)} 部小说")
        if missing_convs:
            parts.append(f"💬 {old_conv_count} 个对话")

        reply = QMessageBox.question(
            self, "发现旧数据",
            f"检测到旧目录中存在明文数据：\n{'、'.join(parts)}\n\n"
            f"是否将这些数据加密导入到用户「{self._username}」的目录？\n"
            "（旧数据不会被删除，仅复制加密到新位置）",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        # ---- 准备未加密管理器读取旧数据 ----
        old_nm = NovelManager(bookshelf_root=old_bookshelf)
        old_cm = ConversationManager(root_dir=old_conversations)
        migrated_books = 0
        migrated_convs = 0

        # ---- 迁移对话 ----
        if missing_convs:
            for meta in old_cm.list_conversations():
                try:
                    data = old_cm.load_conversation(meta.conversation_id)
                    if not data:
                        continue
                    self._conversation_manager.save_conversation(
                        conversation_id=data.get("conversation_id", meta.conversation_id),
                        title=data.get("title", ""),
                        model=data.get("model", ""),
                        messages=data.get("messages", []),
                        character_description=data.get("character_description", ""),
                        story_background=data.get("story_background", ""),
                        strategy=data.get("strategy", ""),
                        reply_mode=data.get("reply_mode", ""),
                        chat_type=data.get("chat_type", ""),
                        participant_character_ids=data.get("participant_character_ids", []),
                        primary_character_id=data.get("primary_character_id", ""),
                        timeline_id=data.get("timeline_id", ""),
                        timeline=data.get("timeline", []),
                        character_book_snapshot=data.get("character_book_snapshot", {}),
                        sender_name=data.get("sender_name", ""),
                        sender_profile=data.get("sender_profile", ""),
                        required_responder_ids=data.get("required_responder_ids", []),
                        structured_messages=data.get("structured_messages", []),
                        branches=data.get("branches", []),
                        active_branch_id=data.get("active_branch_id", "main"),
                        sender_profile_id=data.get("sender_profile_id", ""),
                        scene_state=data.get("scene_state", {}),
                        turn_policy=data.get("turn_policy", {}),
                        memory_change_sets=data.get("memory_change_sets", []),
                        narrator_enabled=data.get("narrator_enabled", False),
                        schema_version=data.get("schema_version", 1),
                    )
                    migrated_convs += 1
                except Exception as e:
                    print(f"[迁移] 对话迁移失败: {e}")

        # ---- 迁移书架 ----
        if missing_books:
            for book_title in sorted(missing_books):
                try:
                    self._novel_manager.create_book(book_title)
                    old_meta = old_nm.load_meta(book_title)

                    # 迁移元信息
                    self._novel_manager.save_meta(
                        book_title,
                        author=old_meta.author,
                        protagonist_bio=old_meta.protagonist_bio,
                        background_story=old_meta.background_story,
                        writing_demand=old_meta.writing_demand,
                        author_plan=getattr(old_meta, "author_plan", ""),
                        xp_mode=old_meta.xp_mode,
                        created_at=old_meta.created_at,
                        updated_at=old_meta.updated_at,
                        total_chapters=old_meta.total_chapters,
                        chapter_titles=old_meta.chapter_titles,
                        chapter_versions=old_meta.chapter_versions,
                        compressed_early_summary=old_meta.compressed_early_summary,
                    )

                    # 迁移章节内容
                    chapters = old_nm.list_chapters(book_title)
                    for ch in chapters:
                        for v_info in ch.get("versions", []):
                            try:
                                content = old_nm.read_chapter_version(
                                    book_title, ch["num"], v_info["v"]
                                )
                                if content:
                                    self._novel_manager.save_chapter_version(
                                        book_title, ch["num"], v_info["title"],
                                        content, version=v_info["v"],
                                    )
                            except Exception as e:
                                print(f"[迁移] 章节 {book_title} ch{ch['num']} v{v_info['v']} 失败: {e}")

                        # 恢复活跃版本
                        active_v = ch.get("active_version")
                        if active_v:
                            self._novel_manager.set_active_version(
                                book_title, ch["num"], active_v
                            )

                    # 迁移摘要
                    summary = old_nm.load_summary(book_title)
                    if summary and summary != "故事刚刚开始。":
                        self._novel_manager._write_encrypted_text(
                            self._novel_manager._summary_path(book_title), summary
                        )

                    # 迁移世界书
                    try:
                        wb = old_nm.load_world_bible(book_title)
                        if wb is not None:
                            self._novel_manager.save_world_bible(book_title, wb)
                    except Exception:
                        pass

                    # 迁移生成历史
                    for rec in old_nm.load_generation_history(book_title):
                        try:
                            self._novel_manager.save_generation_record(
                                title=book_title,
                                chapter_num=rec.get("chapter_num", 0),
                                chapter_title=rec.get("chapter_title", ""),
                                version=rec.get("version", 1),
                                prompt=rec.get("prompt", ""),
                                model=rec.get("model", ""),
                                temperature=rec.get("temperature", 0.7),
                                top_p=rec.get("top_p", 1.0),
                                max_tokens=rec.get("max_tokens", 4096),
                                frequency_penalty=rec.get("frequency_penalty", 0.0),
                                content_preview=rec.get("content_preview", ""),
                                requirement=rec.get("requirement", ""),
                                plot=rec.get("plot", ""),
                                supervision_report=rec.get("supervision_report"),
                            )
                        except Exception as e:
                            print(f"[迁移] 历史记录迁移失败: {e}")

                    migrated_books += 1
                except Exception as e:
                    print(f"[迁移] 小说「{book_title}」迁移失败: {e}")

        QMessageBox.information(
            self, "导入完成",
            f"数据迁移完成！\n"
            f"已导入 {migrated_books} 部小说、{migrated_convs} 个对话到用户「{self._username}」。\n\n"
            "旧目录中的明文数据未被删除，如需移除请手动删除。"
        )

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

    def _on_change_api_key(self) -> None:
        """弹出对话框修改 API Key，验证后加密保存并更新客户端"""
        key, ok = QInputDialog.getText(
            self,
            "修改 API Key",
            "请输入新的 DeepSeek API Key：\n"
            "（可在 https://platform.deepseek.com 获取）\n\n"
            f"当前 Key: {Config.API_KEY[:12]}...{Config.API_KEY[-4:] if len(Config.API_KEY) > 16 else ''}",
            text=Config.API_KEY,
        )
        if not ok or not key.strip():
            return

        key = key.strip()
        if key == Config.API_KEY:
            QMessageBox.information(self, "提示", "API Key 未变更。")
            return

        # 验证新 Key
        if not self._verify_api_key(key):
            QMessageBox.critical(
                self, "验证失败",
                "新的 API Key 验证失败，请检查后重试。\n"
                "常见问题：\n"
                "  - Key 已过期或未生效\n"
                "  - 网络连接异常\n"
                "  - Base URL 配置错误"
            )
            return

        # 加密保存
        old_key = Config.API_KEY
        Config.API_KEY = key
        self._save_encrypted_config(key, Config.BASE_URL)

        # 更新客户端
        self._client.raw_client.api_key = key

        QMessageBox.information(
            self, "修改成功",
            "API Key 已更新并加密保存，下次启动自动加载。"
        )

    # ========== 初始化 ==========

    def _init_client(self) -> None:
        """创建初始聊天客户端（默认角色扮演模式）"""
        strategy = RolePlayStrategy()
        self._client = DeepSeekChatClient(strategy=strategy, model=strategy.recommended_model)

    def _build_model_options(self) -> list[str]:
        settings = getattr(self, "_settings", {}) or {}
        models: list[str] = []
        for model in MODEL_OPTIONS:
            if model not in models:
                models.append(model)
        for key in ("favorite_models", "custom_models"):
            for model in settings.get(key, []) or []:
                if model and model not in models:
                    models.append(model)
        last_model = settings.get("last_model")
        if last_model and last_model not in models:
            models.append(last_model)
        return models

    def _reload_user_settings(self) -> None:
        self._settings = self._settings_manager.load()
        self._presets = self._settings.get("presets", PRESETS).copy()
        self._model_options = self._build_model_options()

    def _apply_settings_to_controls(self) -> None:
        self._reload_user_settings()
        if hasattr(self, "_model_combo"):
            self._settings_applying = True
            current_model = self._client.model
            self._model_combo.blockSignals(True)
            self._model_combo.clear()
            self._model_combo.addItems(self._model_options)
            target_model = self._settings.get("last_model") or current_model
            if target_model in self._model_options:
                self._model_combo.setCurrentText(target_model)
                self._client.switch_model(target_model)
            self._model_combo.blockSignals(False)
            self._settings_applying = False
        if hasattr(self, "_preset_combo"):
            self._settings_applying = True
            current_preset = self._settings.get("current_preset", "狂野")
            self._preset_combo.blockSignals(True)
            self._preset_combo.clear()
            self._preset_combo.addItems([CUSTOM_LABEL, *self._presets.keys()])
            if current_preset in self._presets:
                self._preset_combo.setCurrentText(current_preset)
            else:
                self._preset_combo.setCurrentText(CUSTOM_LABEL)
            self._preset_combo.blockSignals(False)
            self._on_preset_changed(self._preset_combo.currentText())
            self._settings_applying = False
        if hasattr(self, "_display"):
            self._apply_theme()
            has_messages = any(
                msg.get("role") in ("user", "assistant")
                for msg in self._client.export_messages()
            )
            if not has_messages:
                self._display.setHtml(INITIAL_HTML)
        self._update_status()

    def _save_runtime_settings(self) -> None:
        if getattr(self, "_settings_applying", False):
            return
        settings = self._settings_manager.load()
        settings["last_model"] = self._client.model
        settings["current_preset"] = self._preset_combo.currentText() if hasattr(self, "_preset_combo") else settings.get("current_preset", "")
        settings["presets"] = self._presets
        self._settings_manager.save(settings)
        self._settings = settings

    def _load_global_user_prompt(self) -> str:
        """从加密存储加载全局提示词"""
        if self._enc_key:
            data = self._auth.decrypt_json(self._enc_key, self._user_prefs_path())
            if data:
                return data.get("global_user_prompt", "")
        # 兜底：从明文文件加载（旧版本兼容）
        try:
            if os.path.exists(_USER_PREFS_FILE):
                with open(_USER_PREFS_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return data.get("global_user_prompt", "")
        except Exception:
            pass
        return ""

    def _save_global_user_prompt(self, prompt: str) -> None:
        """加密保存全局提示词"""
        if self._enc_key:
            data = self._auth.decrypt_json(self._enc_key, self._user_prefs_path()) or {}
            data["global_user_prompt"] = prompt
            self._auth.encrypt_json(self._enc_key, self._user_prefs_path(), data)
            return
        # 兜底：明文保存
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

        central = QWidget()
        root_layout = QHBoxLayout(central)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        root_layout.addWidget(self._build_navigation_sidebar())

        main_area = QWidget()
        main_layout = QVBoxLayout(main_area)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        main_layout.addWidget(self._build_top_toolbar())

        # 中央分割器
        splitter = QSplitter(Qt.Orientation.Horizontal)

        left_panel = self._build_left_panel()
        splitter.addWidget(left_panel)

        right_panel = self._build_right_panel()
        splitter.addWidget(right_panel)

        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([450, 750])

        main_layout.addWidget(splitter, stretch=1)
        root_layout.addWidget(main_area, stretch=1)
        self.setCentralWidget(central)

        self._display.setHtml(INITIAL_HTML)
        self._mode_stack.setCurrentIndex(0)  # 默认显示角色扮演面板
        self._sync_mode_sidebar()
        self._refresh_history_list()

    def _build_navigation_sidebar(self) -> QWidget:
        """构建最左侧导航栏：模式、设置、Token 日志。"""
        sidebar = QFrame()
        sidebar.setObjectName("appSidebar")
        sidebar.setFixedWidth(76)

        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(8, 10, 8, 10)
        layout.setSpacing(8)

        brand = QLabel("DS")
        brand.setObjectName("sidebarBrand")
        brand.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(brand)

        layout.addSpacing(8)
        self._mode_nav_group = QButtonGroup(sidebar)
        self._mode_nav_group.setExclusive(True)
        self._mode_nav_buttons: dict[str, QPushButton] = {}

        nav_items = [
            ("角色扮演", "🎭", "聊天"),
            ("小说写作", "📚", "写作"),
            ("续写小说", "📄", "续写"),
        ]
        for mode_name, icon, label in nav_items:
            btn = QPushButton(f"{icon}\n{label}")
            btn.setObjectName("navButton")
            btn.setCheckable(True)
            btn.setToolTip(mode_name)
            btn.setFixedSize(58, 54)
            btn.clicked.connect(lambda _checked=False, m=mode_name: self._set_mode_from_sidebar(m))
            self._mode_nav_group.addButton(btn)
            self._mode_nav_buttons[mode_name] = btn
            layout.addWidget(btn)

        layout.addStretch()

        token_btn = QPushButton("📊\nToken")
        token_btn.setObjectName("navButton")
        token_btn.setToolTip("Token 消耗日志")
        token_btn.setFixedSize(58, 54)
        token_btn.clicked.connect(self._open_token_log_dialog)
        layout.addWidget(token_btn)

        settings_btn = QPushButton("⚙️\n设置")
        settings_btn.setObjectName("navButton")
        settings_btn.setToolTip("设置中心")
        settings_btn.setFixedSize(58, 54)
        settings_btn.clicked.connect(self._open_settings_dialog)
        layout.addWidget(settings_btn)

        return sidebar

    def _set_mode_from_sidebar(self, mode_name: str) -> None:
        if not hasattr(self, "_mode_combo"):
            return
        if self._mode_combo.currentText() == mode_name:
            self._sync_mode_sidebar()
            return
        self._mode_combo.setCurrentText(mode_name)
        self._sync_mode_sidebar()

    def _sync_mode_sidebar(self) -> None:
        if not hasattr(self, "_mode_nav_buttons") or not hasattr(self, "_mode_combo"):
            return
        current = self._mode_combo.currentText()
        for mode_name, btn in self._mode_nav_buttons.items():
            btn.setChecked(mode_name == current)

    def _build_top_toolbar(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("topToolbar")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(10, 6, 10, 6)
        self._top_status_label = QLabel("模式: 角色扮演 | 书籍: - | 状态: 就绪")
        self._top_status_label.setObjectName("topStatusLabel")
        layout.addWidget(self._top_status_label, stretch=1)
        return bar

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
            if self._client:
                self._client.cancel()
        event.accept()

    def _build_left_panel(self) -> QWidget:
        """构建左侧控制面板（含小说专属区域）"""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMinimumWidth(280)

        container = QWidget()
        container.setObjectName("leftPanelContainer")
        layout = QVBoxLayout(container)
        layout.setSpacing(4)
        layout.setContentsMargins(6, 6, 6, 6)

        # 模式切换入口在最左侧导航栏；保留隐藏下拉框作为既有切换逻辑的状态桥。
        self._mode_combo = QComboBox(container)
        self._mode_combo.addItems(list(STRATEGY_OPTIONS.keys()))
        self._mode_combo.currentTextChanged.connect(self._on_mode_changed)
        self._last_mode = self._mode_combo.currentText() or list(STRATEGY_OPTIONS.keys())[0]
        self._mode_combo.hide()

        # ── 模型选择 ──
        model_group = QGroupBox("🧠 模型选择")
        model_layout = QVBoxLayout(model_group)
        model_layout.setContentsMargins(8, 4, 8, 4)
        self._model_combo = QComboBox()
        self._model_combo.addItems(self._model_options)
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
        self._preset_combo.addItems([CUSTOM_LABEL, *self._presets.keys()])
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
                border: 1px solid rgba(255, 255, 255, 0.15);
                border-radius: 6px;
                padding: 7px 16px;
                font-weight: bold;
            }
            QPushButton:hover {
                background: #8b3a3a;
                border: 1px solid rgba(255, 255, 255, 0.3);
            }
            QPushButton:pressed {
                background: #5b1a1a;
            }
        """)
        clear_btn.clicked.connect(self._on_clear)
        btn_layout.addWidget(clear_btn)

        api_key_btn = QPushButton("⚙ 设置中心")
        api_key_btn.setStyleSheet("""
            QPushButton {
                background: #2a4a6b;
                color: white;
                border: 1px solid rgba(255, 255, 255, 0.15);
                border-radius: 6px;
                padding: 7px 16px;
                font-weight: bold;
            }
            QPushButton:hover {
                background: #3a6a8b;
                border: 1px solid rgba(255, 255, 255, 0.3);
            }
            QPushButton:pressed {
                background: #1a3a5b;
            }
        """)
        api_key_btn.clicked.connect(self._open_settings_dialog)
        btn_layout.addWidget(api_key_btn)

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
        panel = QGroupBox("🎭 角色档案 / 会话")
        layout = QVBoxLayout(panel)
        layout.setSpacing(4)
        layout.setContentsMargins(8, 4, 8, 4)

        layout.addWidget(QLabel("👥 选择角色（私聊选 1 个，群聊可选多个）"))
        self._character_list = QListWidget()
        self._character_list.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
        self._character_list.setMinimumHeight(120)
        layout.addWidget(self._character_list)

        char_btn_row = QHBoxLayout()
        new_char_btn = QPushButton("新建")
        edit_char_btn = QPushButton("编辑")
        book_btn = QPushButton("人物书")
        new_char_btn.clicked.connect(self._on_new_character_profile)
        edit_char_btn.clicked.connect(self._on_edit_character_profile)
        book_btn.clicked.connect(self._on_character_book)
        char_btn_row.addWidget(new_char_btn)
        char_btn_row.addWidget(edit_char_btn)
        char_btn_row.addWidget(book_btn)
        layout.addLayout(char_btn_row)

        chat_btn_row = QHBoxLayout()
        private_btn = QPushButton("新建私聊")
        group_btn = QPushButton("新建群聊")
        timeline_btn = QPushButton("时间线")
        control_btn = QPushButton("控制中心")
        private_btn.clicked.connect(self._on_new_private_chat)
        group_btn.clicked.connect(self._on_new_group_chat)
        timeline_btn.clicked.connect(self._on_chat_timeline)
        control_btn.clicked.connect(self._on_chat_control_center)
        chat_btn_row.addWidget(private_btn)
        chat_btn_row.addWidget(group_btn)
        chat_btn_row.addWidget(timeline_btn)
        chat_btn_row.addWidget(control_btn)
        layout.addLayout(chat_btn_row)

        self._role_session_label = QLabel("当前会话：未绑定角色")
        self._role_session_label.setWordWrap(True)
        self._role_session_label.setStyleSheet("color: #9cdcfe; font-size: 12px;")
        layout.addWidget(self._role_session_label)

        layout.addWidget(QLabel("🧑 聊天发送者"))
        self._sender_name_edit = QLineEdit()
        self._sender_name_edit.setPlaceholderText("发送者称呼，例如：林舟")
        self._sender_name_edit.setText(self._sender_name)
        self._sender_name_edit.textChanged.connect(self._on_sender_profile_changed)
        layout.addWidget(self._sender_name_edit)
        self._sender_profile_edit = QTextEdit()
        self._sender_profile_edit.setPlaceholderText("身份、性格、外貌、与角色的关系、已知信息等")
        self._sender_profile_edit.setMaximumHeight(80)
        self._sender_profile_edit.textChanged.connect(self._on_sender_profile_changed)
        layout.addWidget(self._sender_profile_edit)

        layout.addWidget(QLabel("✅ 群聊本轮必须回复"))
        self._required_responder_list = QListWidget()
        self._required_responder_list.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
        self._required_responder_list.setMaximumHeight(100)
        self._required_responder_list.itemSelectionChanged.connect(self._on_required_responders_changed)
        layout.addWidget(self._required_responder_list)

        # Hidden legacy editors: old conversation loading still writes into these fields.
        self._role_char_edit = QTextEdit()
        self._role_bg_edit = QTextEdit()
        self._role_char_edit.hide()
        self._role_bg_edit.hide()

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
                border: 1px solid rgba(255, 255, 255, 0.15);
                border-radius: 6px;
                padding: 8px 16px;
                font-weight: bold;
                font-size: 13px;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #2a8b4c, stop:1 #3a9b6c);
                border: 1px solid rgba(255, 255, 255, 0.3);
            }
            QPushButton:pressed {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #0a5b2c, stop:1 #1a7b4c);
            }
        """)
        apply_btn.clicked.connect(self._on_apply_role_settings)
        layout.addWidget(apply_btn)

        self._refresh_character_list()
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

        rename_book_btn = QPushButton("✏️ 重命名")
        rename_book_btn.setMinimumWidth(70)
        rename_book_btn.clicked.connect(self._on_rename_book)
        bookshelf_row.addWidget(rename_book_btn)

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

        # ── 题材与风格基调 ──
        style_group = QGroupBox("🎨 题材与风格")
        style_layout = QVBoxLayout(style_group)
        style_layout.setContentsMargins(4, 4, 4, 4)
        style_layout.setSpacing(4)

        genre_row = QHBoxLayout()
        genre_row.setContentsMargins(0, 0, 0, 0)
        genre_row.addWidget(QLabel("题材"))
        self._novel_genre_combo = QComboBox()
        self._novel_genre_combo.addItems(GENRE_DISPLAY_NAMES)
        self._novel_genre_combo.currentTextChanged.connect(self._on_novel_genre_changed)
        genre_row.addWidget(self._novel_genre_combo, stretch=1)
        style_layout.addLayout(genre_row)

        tone_row = QHBoxLayout()
        tone_row.setContentsMargins(0, 0, 0, 0)
        tone_row.addWidget(QLabel("风格"))
        self._novel_tone_combo = QComboBox()
        self._novel_tone_combo.addItems(TONE_DISPLAY_NAMES)
        self._novel_tone_combo.currentTextChanged.connect(self._on_novel_tone_changed)
        tone_row.addWidget(self._novel_tone_combo, stretch=1)
        style_layout.addLayout(tone_row)

        layout.addWidget(style_group)

        self._xp_mode_check = QCheckBox("XP 模式（成人向创作提示词）")
        self._xp_mode_check.setToolTip("开启后，章节生成、摘要、世界书和发展建议都会偏向成人向关系张力与性癖递进。")
        self._xp_mode_check.setStyleSheet("color: #aaa; font-size: 12px;")
        self._xp_mode_check.toggled.connect(self._on_xp_mode_changed)
        layout.addWidget(self._xp_mode_check)

        # ── 主角设定 ──
        protag_label = QLabel("👤 主角设定")
        layout.addWidget(protag_label)
        self._protagonist_edit = QTextEdit()
        self._protagonist_edit.setPlaceholderText("描述主角背景、性格、外貌...")
        self._protagonist_edit.setMaximumHeight(80)
        self._protagonist_edit.setMinimumHeight(60)
        layout.addWidget(self._protagonist_edit)

        # ── 世界观/背景 ──
        bg_label = QLabel("🌍 世界观 / 背景故事")
        layout.addWidget(bg_label)
        self._background_edit = QTextEdit()
        self._background_edit.setPlaceholderText("描述世界观、时代背景、核心设定...")
        self._background_edit.setMaximumHeight(80)
        self._background_edit.setMinimumHeight(60)
        layout.addWidget(self._background_edit)

        # ── 写作要求 ──
        demand_label = QLabel("✍️ 写作要求")
        layout.addWidget(demand_label)
        self._demand_edit = QTextEdit()
        self._demand_edit.setPlaceholderText("本章具体写作要求（风格、节奏、必须包含的元素...）")
        self._demand_edit.setMaximumHeight(60)
        self._demand_edit.setMinimumHeight(48)
        layout.addWidget(self._demand_edit)

        plan_label = QLabel("🧭 作者规划（不视为已发生事实）")
        layout.addWidget(plan_label)
        self._author_plan_edit = QTextEdit()
        self._author_plan_edit.setPlaceholderText("主线目标、阶段目标、人物弧光、本卷主题、节奏要求、禁写事项...")
        self._author_plan_edit.setMaximumHeight(80)
        self._author_plan_edit.setMinimumHeight(56)
        layout.addWidget(self._author_plan_edit)

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
        self._chapter_word_count.setValue(40000)
        self._chapter_word_count.setSingleStep(500)
        self._chapter_word_count.setSuffix(" 字")
        word_row.addWidget(word_label)
        word_row.addWidget(self._chapter_word_count, stretch=1)
        layout.addLayout(word_row)

        # ── 生成章节按钮 ──
        self._generate_btn = QPushButton("🚀 生成下一章")
        self._generate_btn.setMinimumHeight(40)
        self._generate_btn.setStyleSheet("""
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #7a4a9c, stop:1 #9a6abc);
                color: white;
                border: 1px solid rgba(255, 255, 255, 0.15);
                border-radius: 8px;
                padding: 8px 16px;
                font-weight: bold;
                font-size: 14px;
                letter-spacing: 1px;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #8a5aac, stop:1 #aa7acc);
                border: 1px solid rgba(255, 255, 255, 0.3);
            }
            QPushButton:pressed {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #6a3a8c, stop:1 #8a5aac);
            }
            QPushButton:disabled {
                background: #444;
                color: #888;
                border: 1px solid #555;
            }
        """)
        self._generate_btn.clicked.connect(self._on_generate_chapter)
        layout.addWidget(self._generate_btn)

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
        manage_chapters_btn = QPushButton("🌳 章节树管理")
        manage_chapters_btn.setMinimumHeight(32)
        manage_chapters_btn.clicked.connect(self._on_manage_chapters)
        layout.addWidget(manage_chapters_btn)

        self._chapter_tree_status = QLabel("活跃路径：未加载")
        self._chapter_tree_status.setWordWrap(True)
        self._chapter_tree_status.setStyleSheet("color: #9cdcfe; font-size: 12px;")
        layout.addWidget(self._chapter_tree_status)

        # ── 世界书按钮 ──
        tool_row = QHBoxLayout()
        world_bible_btn = QPushButton("📖 世界书")
        world_bible_btn.setToolTip("查看/编辑已建立的世界观设定库")
        world_bible_btn.clicked.connect(self._on_world_bible)
        tool_row.addWidget(world_bible_btn)
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
            QPushButton { background: #2d5a8b; color: white; border: 1px solid rgba(255,255,255,0.15);
                          border-radius: 6px; padding: 6px 12px; font-weight: bold; }
            QPushButton:hover { background: #3d7abb; }
        """)
        analyze_cont_btn.clicked.connect(self._on_analyze_continuation)
        source_btn_row.addWidget(analyze_cont_btn)

        quick_cont_btn = QPushButton("⚡ 直接续写")
        quick_cont_btn.setMinimumHeight(32)
        quick_cont_btn.setStyleSheet("""
            QPushButton { background: #b85a2c; color: white; border: 1px solid rgba(255,255,255,0.15);
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

        cont_rename_book_btn = QPushButton("✏️ 重命名")
        cont_rename_book_btn.setMinimumWidth(70)
        cont_rename_book_btn.clicked.connect(self._on_cont_rename_book)
        bookshelf_row.addWidget(cont_rename_book_btn)

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

        # ── 题材与风格基调 ──
        cont_style_group = QGroupBox("🎨 题材与风格")
        cont_style_layout = QVBoxLayout(cont_style_group)
        cont_style_layout.setContentsMargins(4, 4, 4, 4)
        cont_style_layout.setSpacing(4)

        cont_genre_row = QHBoxLayout()
        cont_genre_row.setContentsMargins(0, 0, 0, 0)
        cont_genre_row.addWidget(QLabel("题材"))
        self._cont_genre_combo = QComboBox()
        self._cont_genre_combo.addItems(GENRE_DISPLAY_NAMES)
        self._cont_genre_combo.currentTextChanged.connect(self._on_cont_genre_changed)
        cont_genre_row.addWidget(self._cont_genre_combo, stretch=1)
        cont_style_layout.addLayout(cont_genre_row)

        cont_tone_row = QHBoxLayout()
        cont_tone_row.setContentsMargins(0, 0, 0, 0)
        cont_tone_row.addWidget(QLabel("风格"))
        self._cont_tone_combo = QComboBox()
        self._cont_tone_combo.addItems(TONE_DISPLAY_NAMES)
        self._cont_tone_combo.currentTextChanged.connect(self._on_cont_tone_changed)
        cont_tone_row.addWidget(self._cont_tone_combo, stretch=1)
        cont_style_layout.addLayout(cont_tone_row)

        layout.addWidget(cont_style_group)

        self._cont_xp_mode_check = QCheckBox("XP 模式（成人向创作提示词）")
        self._cont_xp_mode_check.setToolTip("开启后，续写、导入概括、世界书和发展建议都会偏向成人向关系张力与性癖递进。")
        self._cont_xp_mode_check.setStyleSheet("color: #aaa; font-size: 12px;")
        self._cont_xp_mode_check.toggled.connect(self._on_cont_xp_mode_changed)
        layout.addWidget(self._cont_xp_mode_check)

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
        layout.addWidget(self._cont_protagonist_edit)

        bg_label = QLabel("🌍 世界观 / 背景故事")
        layout.addWidget(bg_label)
        self._cont_background_edit = QTextEdit()
        self._cont_background_edit.setPlaceholderText("描述世界观、时代背景、核心设定...")
        self._cont_background_edit.setMaximumHeight(80)
        self._cont_background_edit.setMinimumHeight(60)
        layout.addWidget(self._cont_background_edit)

        demand_label = QLabel("✍️ 写作要求")
        layout.addWidget(demand_label)
        self._cont_demand_edit = QTextEdit()
        self._cont_demand_edit.setPlaceholderText("本章具体写作要求（风格、节奏、必须包含的元素...）")
        self._cont_demand_edit.setMaximumHeight(60)
        self._cont_demand_edit.setMinimumHeight(48)
        layout.addWidget(self._cont_demand_edit)

        cont_plan_label = QLabel("🧭 作者规划（不视为已发生事实）")
        layout.addWidget(cont_plan_label)
        self._cont_author_plan_edit = QTextEdit()
        self._cont_author_plan_edit.setPlaceholderText("主线目标、阶段目标、人物弧光、本卷主题、节奏要求、禁写事项...")
        self._cont_author_plan_edit.setMaximumHeight(80)
        self._cont_author_plan_edit.setMinimumHeight(56)
        layout.addWidget(self._cont_author_plan_edit)

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
        self._continue_word_count.setValue(40000)
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
            QPushButton { background: #2d6b2d; color: white; border: 1px solid rgba(255,255,255,0.15);
                          border-radius: 6px; padding: 6px 12px; font-weight: bold; }
            QPushButton:hover { background: #3d8b3d; }
        """)
        cont_suggest_btn.clicked.connect(self._on_cont_panel_suggest)
        plot_helper_row.addWidget(cont_suggest_btn)

        cont_specify_btn = QPushButton("📝 我指定剧情")
        cont_specify_btn.setStyleSheet("""
            QPushButton { background: #6b4d2d; color: white; border: 1px solid rgba(255,255,255,0.15);
                          border-radius: 6px; padding: 6px 12px; font-weight: bold; }
            QPushButton:hover { background: #8b6d3d; }
        """)
        cont_specify_btn.clicked.connect(self._on_cont_panel_specify)
        plot_helper_row.addWidget(cont_specify_btn)
        layout.addLayout(plot_helper_row)

        # ── 生成下一章按钮 ──
        self._cont_generate_btn = QPushButton("🚀 生成下一章")
        self._cont_generate_btn.setMinimumHeight(40)
        self._cont_generate_btn.setStyleSheet("""
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #7a4a9c, stop:1 #9a6abc);
                color: white;
                border: 1px solid rgba(255, 255, 255, 0.15);
                border-radius: 8px;
                padding: 8px 16px;
                font-weight: bold;
                font-size: 14px;
                letter-spacing: 1px;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #8a5aac, stop:1 #aa7acc);
                border: 1px solid rgba(255, 255, 255, 0.3);
            }
            QPushButton:pressed {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #6a3a8c, stop:1 #8a5aac);
            }
            QPushButton:disabled {
                background: #444;
                color: #888;
                border: 1px solid #555;
            }
        """)
        self._cont_generate_btn.clicked.connect(self._on_cont_generate_chapter)
        layout.addWidget(self._cont_generate_btn)

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
        input_frame.setObjectName("inputFrame")
        input_frame.setFrameShape(QFrame.Shape.StyledPanel)
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
                border: 1px solid rgba(255, 255, 255, 0.2);
                border-radius: 8px;
                padding: 8px 20px;
                font-size: 14px;
                font-weight: bold;
                letter-spacing: 2px;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #1177bb, stop:1 #5aafe8);
                border: 1px solid rgba(255, 255, 255, 0.35);
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

        # ── 停止按钮 ──
        self._stop_btn = QPushButton("⏹")
        self._stop_btn.setMinimumHeight(64)
        self._stop_btn.setMinimumWidth(80)
        self._stop_btn.setVisible(False)
        self._stop_btn.setStyleSheet("""
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #8b0000, stop:1 #cc3333);
                color: white;
                border: 1px solid rgba(255, 255, 255, 0.2);
                border-radius: 8px;
                padding: 8px 20px;
                font-size: 16px;
                font-weight: bold;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #aa0000, stop:1 #ee4444);
                border: 1px solid rgba(255, 255, 255, 0.35);
            }
            QPushButton:pressed {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #660000, stop:1 #991111);
                padding-top: 10px;
                padding-bottom: 6px;
            }
        """)
        self._stop_btn.clicked.connect(self._on_stop)
        input_layout.addWidget(self._stop_btn)

        layout.addWidget(input_frame)

        return widget

    # ========== 主题 ==========

    def _apply_theme(self) -> None:
        """根据用户设置应用暗色/亮色主题。"""
        global CURRENT_HTML_STYLE, INITIAL_HTML
        theme = (getattr(self, "_settings", {}) or {}).get("theme", "dark")
        if theme == "light":
            CURRENT_HTML_STYLE = LIGHT_HTML_STYLE
            self._apply_light_theme()
            if hasattr(self, "_display"):
                self._display.page().setBackgroundColor(QColor("#f4f6fb"))
        else:
            CURRENT_HTML_STYLE = HTML_STYLE
            self._apply_dark_theme()
            if hasattr(self, "_display"):
                self._display.page().setBackgroundColor(QColor("#1e1e2e"))
        INITIAL_HTML = initial_html()

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
                border: 1px solid rgba(255, 255, 255, 0.15);
                border-radius: 6px;
                padding: 7px 16px;
                font-weight: 500;
                font-size: 12.5px;
                min-height: 24px;
            }
            QPushButton:hover {
                background: #1177bb;
                border: 1px solid rgba(255, 255, 255, 0.3);
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
            QFrame#inputFrame {
                background: rgba(26, 26, 46, 0.95);
                border: none;
                border-top: 1px solid rgba(255, 255, 255, 0.06);
            }
            QGraphicsView, QListWidget, QTableWidget, QTreeWidget {
                background: #222238;
                color: #d4d4d4;
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 6px;
                selection-background-color: #264f78;
                selection-color: #ffffff;
            }
            QListWidget::item, QTableWidget::item, QTreeWidget::item {
                color: #d4d4d4;
                padding: 4px;
            }
            QListWidget::item:selected, QTableWidget::item:selected, QTreeWidget::item:selected {
                background: #264f78;
                color: #ffffff;
            }
            QHeaderView::section {
                background: #2a2a3e;
                color: #d4d4d4;
                border: 1px solid rgba(255, 255, 255, 0.08);
                padding: 6px;
            }
            QTabWidget::pane {
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 6px;
                background: #1e1e2e;
            }
            QTabBar::tab {
                background: #25253a;
                color: #b0b0c0;
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-bottom: none;
                padding: 7px 12px;
                margin-right: 2px;
                border-top-left-radius: 6px;
                border-top-right-radius: 6px;
            }
            QTabBar::tab:selected {
                background: #1e3a5f;
                color: #ffffff;
                border-color: #569cd6;
            }

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
        self.setStyleSheet(self.styleSheet() + """
            QFrame#appSidebar {
                background: #11111d;
                border-right: 1px solid rgba(255, 255, 255, 0.08);
            }
            QLabel#sidebarBrand {
                color: #f2f6ff;
                font-size: 18px;
                font-weight: 700;
                background: #0e639c;
                border-radius: 8px;
                min-height: 42px;
            }
            QPushButton#navButton {
                background: transparent;
                color: #b0b0c0;
                border: 1px solid transparent;
                border-radius: 8px;
                padding: 4px 2px;
                font-size: 12px;
                min-height: 0;
            }
            QPushButton#navButton:hover {
                background: #25253a;
                color: #ffffff;
                border-color: rgba(86, 156, 214, 0.35);
            }
            QPushButton#navButton:checked {
                background: #1e3a5f;
                color: #ffffff;
                border-color: #569cd6;
            }
            QFrame#topToolbar {
                background: #181826;
                border-bottom: 1px solid rgba(255, 255, 255, 0.08);
            }
            QLabel#topStatusLabel {
                color: #d4d4d4;
                font-weight: 600;
            }
        """)

    def _apply_light_theme(self) -> None:
        """应用亮色主题样式。"""
        self.setStyleSheet("""
            QMainWindow {
                background-color: #f4f6fb;
            }
            QWidget {
                background-color: #f4f6fb;
                color: #202635;
                font-family: "Microsoft YaHei", "Segoe UI", sans-serif;
            }
            QFrame#appSidebar {
                background: #ffffff;
                border-right: 1px solid #d9deea;
            }
            QLabel#sidebarBrand {
                color: #ffffff;
                font-size: 18px;
                font-weight: 700;
                background: #2563eb;
                border-radius: 8px;
                min-height: 42px;
            }
            QPushButton#navButton {
                background: transparent;
                color: #526070;
                border: 1px solid transparent;
                border-radius: 8px;
                padding: 4px 2px;
                font-size: 12px;
                min-height: 0;
            }
            QPushButton#navButton:hover {
                background: #eef3ff;
                color: #1f3b7a;
                border-color: #bfd0ff;
            }
            QPushButton#navButton:checked {
                background: #dbe7ff;
                color: #123a8a;
                border-color: #7aa2ff;
            }
            QFrame#topToolbar {
                background: #ffffff;
                border-bottom: 1px solid #d9deea;
            }
            QLabel#topStatusLabel {
                color: #1e293b;
                font-weight: 600;
            }
            QScrollArea {
                background: transparent;
                border: none;
            }
            QScrollArea > QWidget > QWidget {
                background: transparent;
            }
            QFrame#inputFrame {
                background: #ffffff;
                border: none;
                border-top: 1px solid #d9deea;
            }
            QGraphicsView, QListWidget, QTableWidget, QTreeWidget {
                background: #ffffff;
                color: #202635;
                border: 1px solid #cfd7e6;
                border-radius: 6px;
                selection-background-color: #dbe7ff;
                selection-color: #123a8a;
            }
            QListWidget::item, QTableWidget::item, QTreeWidget::item {
                color: #202635;
                padding: 4px;
            }
            QListWidget::item:selected, QTableWidget::item:selected, QTreeWidget::item:selected {
                background: #dbe7ff;
                color: #123a8a;
            }
            QHeaderView::section {
                background: #eef3ff;
                color: #1e293b;
                border: 1px solid #cfd7e6;
                padding: 6px;
            }
            QTabWidget::pane {
                border: 1px solid #cfd7e6;
                border-radius: 6px;
                background: #ffffff;
            }
            QTabBar::tab {
                background: #edf1f7;
                color: #526070;
                border: 1px solid #cfd7e6;
                border-bottom: none;
                padding: 7px 12px;
                margin-right: 2px;
                border-top-left-radius: 6px;
                border-top-right-radius: 6px;
            }
            QTabBar::tab:selected {
                background: #dbe7ff;
                color: #123a8a;
                border-color: #7aa2ff;
            }
            QGroupBox {
                color: #202635;
                font-weight: 600;
                font-size: 13px;
                border: 1px solid #dce2ef;
                border-radius: 10px;
                margin-top: 12px;
                padding: 16px 12px 12px 12px;
                background: #ffffff;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 8px;
                color: #2563eb;
                background: transparent;
            }
            QComboBox, QSpinBox, QLineEdit, QTextEdit {
                background: #ffffff;
                color: #202635;
                border: 1px solid #cfd7e6;
                border-radius: 6px;
                padding: 6px 10px;
                font-size: 13px;
                selection-background-color: #bfd7ff;
            }
            QTextEdit {
                padding: 6px 8px;
            }
            QComboBox:hover, QSpinBox:hover, QLineEdit:hover, QTextEdit:hover {
                border-color: #7aa2ff;
            }
            QComboBox:focus, QSpinBox:focus, QLineEdit:focus, QTextEdit:focus {
                border-color: #2563eb;
            }
            QComboBox QAbstractItemView {
                background: #ffffff;
                color: #202635;
                selection-background-color: #dbe7ff;
                selection-color: #123a8a;
                border: 1px solid #cfd7e6;
                outline: none;
            }
            QLabel {
                color: #526070;
                font-size: 12.5px;
                background: transparent;
            }
            QPushButton {
                background: #2563eb;
                color: #ffffff;
                border: 1px solid #1d4ed8;
                border-radius: 6px;
                padding: 7px 16px;
                font-weight: 500;
                font-size: 12.5px;
                min-height: 24px;
            }
            QPushButton:hover {
                background: #1d4ed8;
            }
            QPushButton:pressed {
                background: #1e40af;
                padding-top: 9px;
                padding-bottom: 5px;
            }
            QPushButton:disabled {
                background: #e2e8f0;
                color: #94a3b8;
                border-color: #d4dbe8;
            }
            QSlider::groove:horizontal {
                background: #d7deea;
                height: 4px;
                border-radius: 2px;
            }
            QSlider::handle:horizontal {
                background: #2563eb;
                width: 16px;
                height: 16px;
                margin: -6px 0;
                border-radius: 8px;
            }
            QSlider::sub-page:horizontal {
                background: #7aa2ff;
                border-radius: 2px;
            }
            QCheckBox, QRadioButton {
                color: #526070;
                font-size: 12.5px;
                spacing: 8px;
                background: transparent;
            }
            QCheckBox::indicator, QRadioButton::indicator {
                width: 18px;
                height: 18px;
                background: #ffffff;
                border: 1px solid #aeb9ca;
            }
            QCheckBox::indicator {
                border-radius: 4px;
            }
            QRadioButton::indicator {
                border-radius: 10px;
            }
            QCheckBox::indicator:checked, QRadioButton::indicator:checked {
                background: #2563eb;
                border-color: #2563eb;
            }
            QSplitter::handle {
                background: #d9deea;
                width: 2px;
                margin: 4px 0;
            }
            QSplitter::handle:hover {
                background: #7aa2ff;
            }
            QScrollBar:vertical {
                background: #edf1f7;
                width: 8px;
                border: none;
            }
            QScrollBar::handle:vertical {
                background: #b7c1d3;
                border-radius: 4px;
                min-height: 30px;
            }
            QScrollBar::handle:vertical:hover {
                background: #7aa2ff;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0;
            }
            QPushButton#navButton {
                background: transparent;
                color: #526070;
                border: 1px solid transparent;
                border-radius: 8px;
                padding: 4px 2px;
                font-size: 12px;
                min-height: 0;
            }
            QPushButton#navButton:hover {
                background: #eef3ff;
                color: #1f3b7a;
                border-color: #bfd0ff;
            }
            QPushButton#navButton:checked {
                background: #dbe7ff;
                color: #123a8a;
                border-color: #7aa2ff;
            }
        """)

    # ========== 信号处理 ==========

    def _on_stop(self) -> None:
        """停止按钮点击处理"""
        self._stop_btn.setVisible(False)
        if self._client:
            self._client.cancel()

    def _on_mode_changed(self, text: str) -> None:
        """模式变化（由左侧导航栏驱动，隐藏下拉框承载旧状态）。"""
        if self._streaming:
            self._mode_combo.blockSignals(True)
            self._mode_combo.setCurrentText(self._last_mode)
            self._mode_combo.blockSignals(False)
            self._sync_mode_sidebar()
            return

        strategy_cls = STRATEGY_OPTIONS.get(text)
        if strategy_cls is None:
            return

        # 仅在保存后又发生变化时提示，已保存对话可直接切换模式。
        if not self._loading_conversation:
            if self._conversation_dirty:
                msg_box = QMessageBox(self)
                msg_box.setWindowTitle("切换模式")
                msg_box.setText("当前对话未保存，切换模式将丢失对话内容。")
                msg_box.setInformativeText("是否先保存再切换？")
                btn_save = msg_box.addButton("保存并切换", QMessageBox.ButtonRole.AcceptRole)
                btn_discard = msg_box.addButton("不保存，直接切换", QMessageBox.ButtonRole.DestructiveRole)
                btn_cancel = msg_box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
                msg_box.setDefaultButton(btn_save)
                msg_box.exec()
                clicked = msg_box.clickedButton()
                if clicked == btn_cancel or clicked is None:
                    self._mode_combo.blockSignals(True)
                    self._mode_combo.setCurrentText(self._last_mode)
                    self._mode_combo.blockSignals(False)
                    self._sync_mode_sidebar()
                    return
                if clicked == btn_save:
                    if not self._on_save_conversation():
                        self._mode_combo.blockSignals(True)
                        self._mode_combo.setCurrentText(self._last_mode)
                        self._mode_combo.blockSignals(False)
                        self._sync_mode_sidebar()
                        return

        self._last_mode = text
        strategy = strategy_cls()
        self._client.switch_strategy(strategy)
        # 用户主动切换模式时清除当前对话ID，避免覆盖其他模式的保存
        if not self._loading_conversation:
            self._current_conversation_id = None
            self._current_conversation_title = ""
            self._conversation_dirty = False
        self._model_combo.setCurrentText(self._client.model)
        # 同步滑块时阻止滑块事件把预设改成"自定义"
        current_preset = self._preset_combo.currentText()
        self._preset_applying = True
        self._sync_sliders_to_client()
        # 如果当前是命名预设，直接应用预设值（setCurrentText 在文本未变时不触发信号）
        if current_preset != CUSTOM_LABEL:
            preset = self._presets.get(current_preset)
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
        if is_role_play:
            self._refresh_character_list()
            self._sync_role_strategy()

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

        self._sync_mode_sidebar()
        self._update_status()

    def _on_model_changed(self, model: str) -> None:
        self._client.switch_model(model)
        self._save_runtime_settings()
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
        preset = self._presets.get(text)
        if preset is None:
            return
        self._preset_applying = True
        self._temp_slider.setValue(preset["temp"])
        self._top_p_slider.setValue(preset["top_p"])
        self._fp_slider.setValue(preset["fp"])
        self._mt_spin.setValue(preset["max_tokens"])
        self._preset_applying = False
        self._update_status()
        self._save_runtime_settings()

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
        self._reset_display()
        self._current_conversation_id = None
        self._current_conversation_title = ""
        self._conversation_dirty = False
        self._chat_state = ChatSessionState()
        self._chat_state.active_branch()

    def _reset_display(self) -> None:
        self._display.setHtml(INITIAL_HTML)

    def _current_book_for_status(self) -> str:
        try:
            if isinstance(self._client.strategy, NovelStrategy):
                return self._bookshelf_combo.currentText().strip()
            if isinstance(self._client.strategy, ContinuationStrategy):
                return self._cont_bookshelf_combo.currentText().strip()
        except Exception:
            pass
        return ""

    def _refresh_top_status(self) -> None:
        if not hasattr(self, "_top_status_label") or not self._client:
            return
        book = self._current_book_for_status() or "-"
        state = "生成中" if self._streaming else "就绪"
        self._top_status_label.setText(
            f"模式: {self._client.strategy.get_name()} | "
            f"模型: {self._client.model} | "
            f"书籍: {book} | 状态: {state}"
        )

    def _open_token_log_dialog(self) -> None:
        dialog = TokenLogDialog(self, self._token_log_manager)
        dialog.exec()

    def _log_token_usage(
        self,
        *,
        operation: str,
        direction: str,
        content: str,
        usage,
        model: str | None = None,
        strategy: str | None = None,
        started_at: str | None = None,
        finished_at: str | None = None,
        duration_ms: int | None = None,
        char_count: int | None = None,
        hanzi_count: int | None = None,
    ) -> None:
        try:
            usage_dict = DeepSeekChatClient._usage_to_dict(usage)
            self._token_log_manager.add_entry(
                operation=operation,
                direction=direction,
                strategy=strategy or self._client.strategy.get_name(),
                model=model or self._client.model,
                content=content,
                usage=usage_dict,
                started_at=started_at,
                finished_at=finished_at,
                duration_ms=duration_ms,
                char_count=char_count,
                hanzi_count=hanzi_count,
            )
        except Exception:
            pass

    def _count_hanzi(self, text: str) -> int:
        return len(re.findall(r"[\u4e00-\u9fff]", text or ""))

    def _usage_for_direction(self, usage: dict | None, direction: str) -> dict | None:
        if not usage:
            return None
        if direction == "send":
            return {
                "prompt_tokens": usage.get("prompt_tokens"),
                "completion_tokens": None,
                "total_tokens": usage.get("prompt_tokens"),
            }
        return {
            "prompt_tokens": None,
            "completion_tokens": usage.get("completion_tokens"),
            "total_tokens": usage.get("total_tokens"),
        }

    def _format_chapter_stats_block(self, stats: dict) -> str:
        usage = stats.get("usage") or {}
        prompt_tokens = usage.get("prompt_tokens")
        completion_tokens = usage.get("completion_tokens")
        total_tokens = usage.get("total_tokens")

        def token_text(value) -> str:
            return str(value) if value is not None else "未返回"

        return (
            "\n\n---\n"
            "📊 生成统计\n"
            f"- 发送时间：{stats['started_at']}\n"
            f"- 返回完成：{stats['finished_at']}\n"
            f"- 耗时：{stats['duration_ms'] / 1000:.1f} 秒\n"
            f"- 发送 token：{token_text(prompt_tokens)} / 返回 token：{token_text(completion_tokens)} / 总 token：{token_text(total_tokens)}\n"
            f"- 返回字符数：{stats['char_count']} / 汉字数：{stats['hanzi_count']}\n"
            "---\n"
        )

    def _stream_chapter_completion(
        self,
        *,
        operation: str,
        messages: list[dict],
        prompt_text: str,
        max_tokens: int,
        emit_tokens: bool = True,
    ) -> tuple[str, dict, bool]:
        started_at = time.strftime("%Y-%m-%d %H:%M:%S")
        start_time = time.time()
        usage_dict: dict | None = None
        chunks: list[str] = []
        stream = None

        kwargs = {
            "model": self._client.model,
            "messages": messages,
            "temperature": self._client.temperature,
            "top_p": self._client.top_p,
            "max_tokens": max_tokens,
            "frequency_penalty": self._client.frequency_penalty,
            "stream": True,
        }
        try:
            try:
                stream = self._client.raw_client.chat.completions.create(
                    **kwargs,
                    stream_options={"include_usage": True},
                )
            except Exception:
                stream = self._client.raw_client.chat.completions.create(**kwargs)

            for chunk in stream:
                if self._client._cancel_requested:
                    close = getattr(stream, "close", None)
                    if callable(close):
                        close()
                    break
                usage = getattr(chunk, "usage", None)
                if usage is not None:
                    usage_dict = DeepSeekChatClient._usage_to_dict(usage)
                choices = getattr(chunk, "choices", []) or []
                if not choices:
                    continue
                delta = getattr(choices[0], "delta", None)
                token = getattr(delta, "content", "") if delta else ""
                if token:
                    chunks.append(token)
                    if emit_tokens:
                        self._stream_signals.token.emit(token)
        finally:
            finished_at = time.strftime("%Y-%m-%d %H:%M:%S")

        content = "".join(chunks)
        duration_ms = int((time.time() - start_time) * 1000)
        stats = {
            "started_at": started_at,
            "finished_at": finished_at,
            "duration_ms": duration_ms,
            "char_count": len(content),
            "hanzi_count": self._count_hanzi(content),
            "usage": usage_dict,
        }
        cancelled = bool(self._client._cancel_requested)
        if cancelled:
            self._log_token_usage(
                operation=operation,
                direction="send",
                content=prompt_text,
                usage=self._usage_for_direction(usage_dict, "send"),
                started_at=started_at,
                finished_at=finished_at,
                duration_ms=duration_ms,
                char_count=len(prompt_text or ""),
                hanzi_count=self._count_hanzi(prompt_text),
            )
            return content, stats, True

        self._log_token_usage(
            operation=operation,
            direction="send",
            content=prompt_text,
            usage=self._usage_for_direction(usage_dict, "send"),
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=duration_ms,
            char_count=len(prompt_text or ""),
            hanzi_count=self._count_hanzi(prompt_text),
        )
        self._log_token_usage(
            operation=operation,
            direction="receive",
            content=content,
            usage=self._usage_for_direction(usage_dict, "receive"),
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=duration_ms,
            char_count=len(content),
            hanzi_count=self._count_hanzi(content),
        )
        if emit_tokens:
            self._stream_signals.token.emit(self._format_chapter_stats_block(stats))
        return content, stats, False

    def _usage_logged_client(self, operation: str):
        return _UsageLoggingClientProxy(self._client.raw_client, self, operation)

    def _open_settings_dialog(self) -> None:
        dialog = SettingsDialog(
            self,
            settings_manager=self._settings_manager,
            auth=self._auth,
            username=self._username,
            user_dir=self._user_dir,
            encrypted=self._enc_key is not None,
            api_key_callback=self._on_change_api_key,
            settings_changed_callback=self._apply_settings_to_controls,
            password_changed_callback=self._on_password_changed,
        )
        dialog.exec()

    def _on_password_changed(self, new_key: bytes) -> None:
        self._enc_key = new_key
        self._novel_manager._enc_key = new_key
        self._conversation_manager._enc_key = new_key
        self._character_book_manager._enc_key = new_key
        self._sender_profile_manager._enc_key = new_key
        self._scene_preset_manager._enc_key = new_key
        self._settings_manager._enc_key = new_key
        self._token_log_manager._enc_key = new_key

    def _update_status(self) -> None:
        self._status_label.setText(
            f"模式: {self._client.strategy.get_name()}\n"
            f"模型: {self._client.model}\n"
            f"温度: {self._client.temperature:.2f} | "
            f"top_p: {self._client.top_p:.2f}\n"
            f"freq_p: {self._client.frequency_penalty:.2f} | "
            f"max_tk: {self._client.max_tokens}"
        )
        self._refresh_top_status()

    # ========== 🎭 角色扮演面板事件 ==========

    def _load_character_book(self):
        return self._character_book_manager.load()

    def _save_character_book(self, book) -> None:
        self._character_book_manager.save(book)
        self._refresh_character_list()
        self._sync_role_strategy()

    def _mark_conversation_dirty(self) -> None:
        if not self._client:
            return
        has_content = any(
            message.get("role") in ("user", "assistant")
            for message in self._client.export_messages()
        )
        if has_content:
            self._conversation_dirty = True

    def _refresh_character_list(self) -> None:
        if not hasattr(self, "_character_list"):
            return
        selected = set(self._participant_character_ids)
        self._character_list.blockSignals(True)
        self._character_list.clear()
        for profile in self._load_character_book().profiles:
            item = QListWidgetItem(profile.name or "未命名角色")
            item.setData(Qt.ItemDataRole.UserRole, profile.character_id)
            self._character_list.addItem(item)
            item.setSelected(profile.character_id in selected)
        self._character_list.blockSignals(False)
        self._refresh_required_responder_list()

    def _refresh_required_responder_list(self) -> None:
        if not hasattr(self, "_required_responder_list"):
            return
        book = self._load_character_book()
        allowed = set(self._participant_character_ids)
        selected = set(self._required_responder_ids)
        self._required_responder_list.blockSignals(True)
        self._required_responder_list.clear()
        for profile in book.profiles:
            if profile.character_id not in allowed:
                continue
            item = QListWidgetItem(profile.name or "未命名角色")
            item.setData(Qt.ItemDataRole.UserRole, profile.character_id)
            self._required_responder_list.addItem(item)
            item.setSelected(profile.character_id in selected)
        self._required_responder_list.setEnabled(self._current_chat_type == "group")
        self._required_responder_list.blockSignals(False)

    def _selected_character_ids(self) -> list[str]:
        if not hasattr(self, "_character_list"):
            return []
        return [
            item.data(Qt.ItemDataRole.UserRole)
            for item in self._character_list.selectedItems()
            if item.data(Qt.ItemDataRole.UserRole)
        ]

    def _character_names(self, character_ids: list[str]) -> list[str]:
        book = self._load_character_book()
        names = []
        for cid in character_ids:
            profile = find_profile(book, cid)
            if profile:
                names.append(profile.name)
        return names

    def _sync_role_strategy(self) -> None:
        if not isinstance(self._client.strategy, RolePlayStrategy):
            return
        strategy = self._client.strategy
        strategy.character_book = self._load_character_book()
        strategy.participant_character_ids = list(self._participant_character_ids)
        strategy.primary_character_id = self._primary_character_id
        strategy.chat_type = self._current_chat_type
        strategy.timeline = list(self._chat_timeline)
        strategy.sender_name = self._sender_name or "你"
        strategy.sender_profile = self._sender_profile
        strategy.required_responder_ids = list(
            self._chat_state.turn_policy.required_speaker_ids or self._required_responder_ids
        )
        strategy.turn_policy = copy.deepcopy(self._chat_state.turn_policy)
        if self._chat_state.scene_state.present_character_ids:
            absent = [
                cid for cid in self._participant_character_ids
                if cid not in self._chat_state.scene_state.present_character_ids
            ]
            strategy.turn_policy.blocked_speaker_ids = list(
                dict.fromkeys([*strategy.turn_policy.blocked_speaker_ids, *absent])
            )
        strategy.scene_state = self._chat_state.scene_state
        strategy.narrator_enabled = self._chat_state.narrator_enabled
        strategy.active_branch_id = self._chat_state.active_branch_id
        sender_profile = next(
            (
                item for item in self._sender_profiles
                if item.sender_profile_id == self._chat_state.sender_profile_id
            ),
            None,
        )
        strategy.sender_profile_record = sender_profile
        if sender_profile:
            strategy.sender_name = sender_profile.name or strategy.sender_name
            strategy.sender_profile = "\n".join(
                value for value in (
                    sender_profile.identity,
                    sender_profile.personality,
                    sender_profile.appearance,
                    sender_profile.background,
                    sender_profile.relationships,
                    sender_profile.knowledge_state,
                    sender_profile.notes,
                ) if value
            )
        strategy.reply_mode = (
            RolePlayStrategy.REPLY_MODE_NARRATOR
            if self._radio_narrator.isChecked()
            else RolePlayStrategy.REPLY_MODE_CHARACTER
        )
        self._client.update_system_prompt()
        self._update_role_session_label()

    def _apply_sender_profile_to_runtime(self, sender_profile_id: str) -> None:
        sender = next(
            (
                item for item in self._sender_profiles
                if item.sender_profile_id == sender_profile_id
            ),
            None,
        )
        if not sender:
            return
        self._sender_name = sender.name or "你"
        self._sender_profile = "\n".join(
            value for value in (
                sender.identity,
                sender.personality,
                sender.appearance,
                sender.background,
                sender.relationships,
                sender.knowledge_state,
                sender.notes,
            ) if value
        )
        if hasattr(self, "_sender_name_edit"):
            self._sender_name_edit.blockSignals(True)
            self._sender_profile_edit.blockSignals(True)
            self._sender_name_edit.setText(self._sender_name)
            self._sender_profile_edit.setPlainText(self._sender_profile)
            self._sender_name_edit.blockSignals(False)
            self._sender_profile_edit.blockSignals(False)
    def _on_chat_control_center(self) -> None:
        self._chat_state.turn_policy.required_speaker_ids = list(
            self._required_responder_ids
        )
        old_scene = state_to_dict(self._chat_state).get("scene_state", {})
        dialog = ChatControlDialog(
            self,
            self._chat_state,
            self._load_character_book(),
            self._participant_character_ids,
            self._sender_profiles,
            self._scene_presets,
            self._apply_memory_change,
            self._modify_memory_change,
            self._reject_memory_change,
            self._revert_memory_change,
            self._switch_chat_branch,
            self._fork_current_branch,
            self._message_operation,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._sender_profile_manager.save(self._sender_profiles)
        self._scene_preset_manager.save(self._scene_presets)
        self._apply_sender_profile_to_runtime(
            self._chat_state.sender_profile_id
        )
        self._required_responder_ids = list(self._chat_state.turn_policy.required_speaker_ids)
        new_scene = state_to_dict(self._chat_state).get("scene_state", {})
        if old_scene != new_scene and (new_scene.get("location") or new_scene.get("description")):
            from core.character_book import ChatTimelineEntry
            entry = ChatTimelineEntry(
                event_id=new_id("evt"),
                turn_index=max(0, self._current_turn_index()),
                event=f"场景切换：{new_scene.get('location') or '未命名场景'}",
                participants=self._character_names(new_scene.get("present_character_ids", [])),
                impact=new_scene.get("objective", ""),
                source_message_range="scene-change",
                created_at=now_text(),
            )
            self._chat_timeline.append(entry)
            self._chat_state.active_branch().timeline = timeline_to_dict(self._chat_timeline)
        self._refresh_required_responder_list()
        self._sync_role_strategy()
        self._mark_conversation_dirty()

    def _current_turn_index(self) -> int:
        messages = self._chat_state.active_branch().messages
        return max((message.turn_index for message in messages), default=0)

    def _find_change_set(self, change_set_id: str):
        return next(
            (
                item for item in self._chat_state.memory_change_sets
                if item.change_set_id == change_set_id
            ),
            None,
        )

    def _apply_memory_change(self, change_set_id: str) -> None:
        change_set = self._find_change_set(change_set_id)
        if not change_set:
            return
        book = self._load_character_book()
        apply_memory_change_set(book, change_set)
        book.change_history.append({
            "change_set_id": change_set.change_set_id,
            "status": change_set.status,
            "applied_at": change_set.applied_at,
        })
        self._character_book_manager.save(book)
        self._snapshot_active_branch(book)
        self._sync_role_strategy()

    def _reject_memory_change(self, change_set_id: str) -> None:
        change_set = self._find_change_set(change_set_id)
        if change_set:
            change_set.status = "rejected"
            self._mark_conversation_dirty()

    def _modify_memory_change(self, change_set_id: str) -> None:
        change_set = self._find_change_set(change_set_id)
        if not change_set:
            return
        editable = [
            {
                "change_id": change.change_id,
                "character_id": change.character_id,
                "field_name": change.field_name,
                "new_value": change.new_value,
                "reason": change.reason,
            }
            for change in change_set.changes
        ]
        value, ok = QInputDialog.getMultiLineText(
            self,
            "修改人物书变更",
            "编辑 JSON 中的 new_value 后应用：",
            json.dumps(editable, ensure_ascii=False, indent=2),
        )
        if not ok:
            return
        try:
            updates = json.loads(value)
            update_by_id = {
                item.get("change_id"): item for item in updates if isinstance(item, dict)
            }
            for change in change_set.changes:
                update = update_by_id.get(change.change_id)
                if update:
                    change.new_value = update.get("new_value")
                    change.reason = str(update.get("reason", change.reason))
        except Exception as error:
            QMessageBox.warning(self, "JSON 无效", str(error))
            return
        self._apply_memory_change(change_set_id)

    def _revert_memory_change(self, change_set_id: str) -> None:
        change_set = self._find_change_set(change_set_id)
        if not change_set or change_set.status != "applied":
            return
        book = self._load_character_book()
        revert_memory_change_set(book, change_set)
        self._character_book_manager.save(book)
        self._snapshot_active_branch(book)
        self._sync_role_strategy()

    def _snapshot_active_branch(self, book=None) -> None:
        book = book or self._load_character_book()
        branch = self._chat_state.active_branch()
        branch.character_state_snapshot = character_book_to_dict(book)
        branch.timeline = timeline_to_dict(self._chat_timeline)

    def _switch_chat_branch(self, branch_id: str) -> None:
        branch = next((item for item in self._chat_state.branches if item.branch_id == branch_id), None)
        if not branch:
            return
        self._chat_state.active_branch_id = branch_id
        self._chat_timeline = dict_to_timeline(branch.timeline)
        if branch.character_state_snapshot:
            self._character_book_manager.save(dict_to_character_book(branch.character_state_snapshot))
        legacy = structured_to_legacy_messages(branch.messages, self._client.strategy.get_system_prompt())
        self._client.import_messages(legacy)
        self._render_structured_conversation(branch.messages)
        self._sync_role_strategy()
        self._mark_conversation_dirty()

    def _fork_current_branch(self) -> None:
        branch = self._chat_state.active_branch()
        if not branch.messages:
            return
        new_branch = fork_branch(self._chat_state, branch.messages[-1].message_id)
        if new_branch.character_state_snapshot:
            self._character_book_manager.save(dict_to_character_book(new_branch.character_state_snapshot))
        self._sync_role_strategy()
        self._mark_conversation_dirty()

    def _message_operation(self, operation: str, message_id: str) -> None:
        branch = self._chat_state.active_branch()
        index = next(
            (idx for idx, message in enumerate(branch.messages) if message.message_id == message_id),
            -1,
        )
        if index < 0:
            return
        message = branch.messages[index]
        if operation == "fork":
            new_branch = fork_branch(self._chat_state, message_id)
            self._render_structured_conversation(new_branch.messages)
            self._mark_conversation_dirty()
            return
        if operation == "edit":
            value, ok = QInputDialog.getMultiLineText(
                self, "编辑消息", "消息内容：", message.content
            )
            if ok:
                message.content = value.strip()
                self._render_structured_conversation(branch.messages)
                self._mark_conversation_dirty()
            return
        if operation == "delete":
            if QMessageBox.question(self, "删除消息", "删除该消息？") == QMessageBox.StandardButton.Yes:
                del branch.messages[index]
                self._render_structured_conversation(branch.messages)
                self._mark_conversation_dirty()
            return
        if operation in ("source", "changes"):
            if operation == "source":
                text = (
                    f"消息 ID：{message.message_id}\n"
                    f"来源消息：{message.source_message_id or '无'}\n"
                    f"分支：{message.branch_id}\n轮次：{message.turn_index}"
                )
            else:
                related = [
                    change_set for change_set in self._chat_state.memory_change_sets
                    if message.message_id in change_set.source_message_ids
                ]
                text = "\n\n".join(
                    f"{item.change_set_id} [{item.status}]\n"
                    + "\n".join(
                        f"- {change.character_id}.{change.field_name}: "
                        f"{change.old_value!r} -> {change.new_value!r} ({change.risk})"
                        for change in item.changes
                    )
                    for item in related
                ) or "该消息没有人物书变更。"
            QMessageBox.information(self, "消息信息", text)
            return
        if operation == "regenerate":
            if message.role != "assistant":
                QMessageBox.warning(self, "无法重生成", "只能重生成角色发言。")
                return
            tone, ok = QInputDialog.getText(
                self, "单角色重生成", "附加语气/行为要求（可留空）："
            )
            if not ok:
                return
            fork_at = branch.messages[index - 1].message_id if index > 0 else message.message_id
            new_branch = fork_branch(
                self._chat_state, fork_at, title=f"重生成-{message.speaker_name}"
            )
            self._switch_chat_branch(new_branch.branch_id)
            self._start_single_character_regeneration(message.speaker_id, message.speaker_name, tone)

    def _start_single_character_regeneration(
        self, speaker_id: str, speaker_name: str, requirement: str
    ) -> None:
        if self._streaming:
            return
        self._client.reset_cancel()
        self._streaming = True
        self._streaming_start_time = time.time()
        self._assistant_text_buffer = []
        self._stop_btn.setVisible(True)
        self._mode_combo.setEnabled(False)

        def run():
            try:
                branch = self._chat_state.active_branch()
                legacy = structured_to_legacy_messages(
                    branch.messages, self._client.strategy.get_system_prompt()
                )
                legacy.append({
                    "role": "user",
                    "content": (
                        f"只让角色「{speaker_name}」重新回复上一轮。"
                        f"要求：{requirement or '严格符合人物设定和当前视角'}。"
                        "输出合法 JSON messages 数组。"
                    ),
                })
                response = self._client.raw_client.chat.completions.create(
                    model=self._client.model,
                    messages=legacy,
                    temperature=self._client.temperature,
                    max_tokens=self._client.max_tokens,
                )
                raw = response.choices[0].message.content or ""
                name_to_id = {speaker_name: speaker_id}
                messages = parse_structured_reply(
                    raw, branch.branch_id, self._current_turn_index(), name_to_id
                )
                messages = [
                    item for item in messages
                    if item.speaker_id == speaker_id or item.speaker_name == speaker_name
                ]
                branch.messages.extend(messages)
                self._last_structured_assistant_messages = messages
                self._client.import_messages(
                    structured_to_legacy_messages(
                        branch.messages, self._client.strategy.get_system_prompt()
                    )
                )
                self._stream_signals.finished.emit()
            except Exception as error:
                self._stream_signals.error.emit(str(error))

        threading.Thread(target=run, daemon=True).start()

    def _update_role_session_label(self) -> None:
        if not hasattr(self, "_role_session_label"):
            return
        names = self._character_names(self._participant_character_ids)
        chat_type = "群聊" if self._current_chat_type == "group" else "私聊"
        if names:
            self._role_session_label.setText(
                f"当前会话：{chat_type} | 角色：{'、'.join(names)} | 时间线 {len(self._chat_timeline)} 条"
            )
        else:
            self._role_session_label.setText("当前会话：未绑定角色")

    def _on_sender_profile_changed(self) -> None:
        if not hasattr(self, "_sender_name_edit"):
            return
        self._sender_name = self._sender_name_edit.text().strip() or "你"
        self._sender_profile = self._sender_profile_edit.toPlainText().strip()
        self._mark_conversation_dirty()
        self._sync_role_strategy()

    def _on_required_responders_changed(self) -> None:
        self._required_responder_ids = [
            item.data(Qt.ItemDataRole.UserRole)
            for item in self._required_responder_list.selectedItems()
            if item.data(Qt.ItemDataRole.UserRole)
        ]
        self._chat_state.turn_policy.required_speaker_ids = list(
            self._required_responder_ids
        )
        self._mark_conversation_dirty()
        self._sync_role_strategy()

    def _on_new_character_profile(self) -> None:
        dlg = CharacterProfileDialog(self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        profile = dlg.get_profile()
        if not profile.name:
            QMessageBox.warning(self, "缺少名称", "角色名称不能为空。")
            return
        self._character_book_manager.create_profile(profile)
        self._refresh_character_list()

    def _on_edit_character_profile(self) -> None:
        ids = self._selected_character_ids()
        if not ids:
            QMessageBox.warning(self, "未选择角色", "请先选择一个角色。")
            return
        book = self._load_character_book()
        profile = find_profile(book, ids[0])
        if not profile:
            return
        dlg = CharacterProfileDialog(self, profile)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        updated = dlg.get_profile()
        if not updated.name:
            QMessageBox.warning(self, "缺少名称", "角色名称不能为空。")
            return
        self._character_book_manager.update_profile(updated)
        self._refresh_character_list()

    def _on_character_book(self) -> None:
        book = self._load_character_book()
        dlg = CharacterBookDialog(self, book, save_callback=self._save_character_book)
        dlg.exec()
        self._save_character_book(dlg.get_book())

    def _start_character_chat(self, chat_type: str) -> None:
        ids = self._selected_character_ids()
        if chat_type == "private" and len(ids) != 1:
            QMessageBox.warning(self, "私聊角色", "私聊需要且只能选择一个角色。")
            return
        if chat_type == "group" and len(ids) < 2:
            QMessageBox.warning(self, "群聊角色", "群聊至少选择两个角色。")
            return
        previous_sender_profile_id = self._chat_state.sender_profile_id
        available_sender_ids = {
            profile.sender_profile_id for profile in self._sender_profiles
        }
        if previous_sender_profile_id not in available_sender_ids:
            previous_sender_profile_id = (
                self._sender_profiles[0].sender_profile_id
                if self._sender_profiles else ""
            )
        self._current_chat_type = chat_type
        self._participant_character_ids = ids
        self._primary_character_id = ids[0] if ids else ""
        self._required_responder_ids = list(ids) if chat_type == "group" else list(ids[:1])
        self._chat_timeline = []
        self._chat_state = ChatSessionState(
            sender_profile_id=previous_sender_profile_id
        )
        self._apply_sender_profile_to_runtime(previous_sender_profile_id)
        branch = self._chat_state.active_branch()
        branch.character_state_snapshot = character_book_to_dict(self._load_character_book())
        self._chat_state.scene_state.present_character_ids = list(ids)
        self._chat_state.turn_policy.required_speaker_ids = list(self._required_responder_ids)
        self._chat_state.turn_policy.allowed_speaker_ids = list(ids)
        self._current_conversation_id = None
        self._current_conversation_title = ""
        self._conversation_dirty = False
        self._refresh_required_responder_list()
        self._sync_role_strategy()
        self._client.clear_context(keep_system=True)
        self._reset_display()
        names = "、".join(self._character_names(ids))
        self._append_user_message(f"已创建{'群聊' if chat_type == 'group' else '私聊'}：{names}")

    def _on_new_private_chat(self) -> None:
        self._start_character_chat("private")

    def _on_new_group_chat(self) -> None:
        self._start_character_chat("group")

    def _on_chat_timeline(self) -> None:
        if not self._chat_timeline:
            QMessageBox.information(self, "当前时间线", "当前对话还没有时间线事件。")
            return
        text = "\n".join(
            f"第{item.turn_index}轮：{item.event}\n参与：{'、'.join(item.participants)}\n影响：{item.impact}\n"
            for item in self._chat_timeline
        )
        dlg = QDialog(self)
        dlg.setWindowTitle("当前对话时间线")
        dlg.resize(620, 480)
        layout = QVBoxLayout(dlg)
        edit = QTextEdit()
        edit.setReadOnly(True)
        edit.setPlainText(text)
        layout.addWidget(edit)
        dlg.exec()

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
            self._mark_conversation_dirty()

    def _on_apply_role_settings(self) -> None:
        """将当前角色档案、人物书、时间线和回复方式写入 system prompt。"""
        if not isinstance(self._client.strategy, RolePlayStrategy):
            return
        if not self._participant_character_ids:
            ids = self._selected_character_ids()
            if ids:
                self._participant_character_ids = ids
                self._primary_character_id = ids[0]
        self._sync_role_strategy()
        self._mark_conversation_dirty()
        is_narrator = self._client.strategy.reply_mode == RolePlayStrategy.REPLY_MODE_NARRATOR
        mode_text = "旁白描述（第三人称）" if is_narrator else "角色回答（第一人称）"
        names = "、".join(self._character_names(self._participant_character_ids)) or "未选择角色"
        notice_parts = [
            "🎭 **角色档案设定已更新。**\n",
            f"**回复方式：** {mode_text}\n",
            f"**参与角色：** {names}\n",
            f"**时间线事件：** {len(self._chat_timeline)} 条\n",
        ]
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
        """删除选中的小说（小说面板）"""
        current = self._bookshelf_combo.currentText()
        if not current or current.startswith("（暂无小说"):
            return
        reply = QMessageBox.question(
            self,
            "确认删除",
            f"确定要删除小说「{current}」及其所有章节吗？\n此操作不可恢复！",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            ok = self._novel_manager.delete_book(current)
            if not ok:
                QMessageBox.warning(self, "删除失败", f"无法删除小说「{current}」，请检查文件权限。")
                return
            self._refresh_novel_bookshelf()
            self._on_book_selected(self._bookshelf_combo.currentText())

    def _on_rename_book(self) -> None:
        """重命名选中的小说"""
        current = self._get_current_book_title()
        if not current:
            QMessageBox.warning(self, "提示", "请先选择一本小说。")
            return
        new_title, ok = QInputDialog.getText(
            self, "重命名小说", "请输入新标题：", text=current
        )
        if ok and new_title.strip() and new_title.strip() != current:
            if new_title.strip() in self._novel_manager.list_books():
                QMessageBox.warning(self, "警告", f"小说「{new_title.strip()}」已存在。")
                return
            self._novel_manager.rename_book(current, new_title.strip())
            self._refresh_novel_bookshelf()
            self._bookshelf_combo.setCurrentText(new_title.strip())

    def _on_cont_rename_book(self) -> None:
        """续写面板：重命名小说"""
        current = self._cont_bookshelf_combo.currentText()
        if not current or current.startswith("（暂无小说"):
            QMessageBox.warning(self, "提示", "请先选择一本小说。")
            return
        new_title, ok = QInputDialog.getText(
            self, "重命名小说", "请输入新标题：", text=current
        )
        if ok and new_title.strip() and new_title.strip() != current:
            if new_title.strip() in self._novel_manager.list_books():
                QMessageBox.warning(self, "警告", f"小说「{new_title.strip()}」已存在。")
                return
            self._novel_manager.rename_book(current, new_title.strip())
            self._refresh_novel_bookshelf()
            self._cont_bookshelf_combo.setCurrentText(new_title.strip())

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
            self._xp_mode_check.blockSignals(True)
            self._xp_mode_check.setChecked(False)
            self._xp_mode_check.blockSignals(False)
            self._sync_xp_mode_to_cont()
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
        self._author_plan_edit.blockSignals(True)
        self._author_plan_edit.setPlainText(getattr(meta, "author_plan", ""))
        self._author_plan_edit.blockSignals(False)
        next_ch = self._novel_manager.get_active_generation_target(title)["chapter_num"]
        chapters = self._novel_manager.list_chapters(title)
        self._chapter_info_label.setText(
            f"已有 {len(chapters)} 章，下一章编号: 第{next_ch}章"
        )

        # 同步题材/风格到下拉框
        genre_display = get_genre_display(meta.genre)
        tone_display = get_tone_display(meta.style_tone)
        self._novel_genre_combo.blockSignals(True)
        self._novel_genre_combo.setCurrentText(genre_display or "无特定风格")
        self._novel_genre_combo.blockSignals(False)
        self._novel_tone_combo.blockSignals(True)
        self._novel_tone_combo.setCurrentText(tone_display or "默认")
        self._novel_tone_combo.blockSignals(False)
        self._xp_mode_check.blockSignals(True)
        self._xp_mode_check.setChecked(bool(meta.xp_mode))
        self._xp_mode_check.blockSignals(False)
        # 同步到续写面板
        self._sync_genre_tone_to_cont()
        self._sync_xp_mode_to_cont()

        # 同步到策略
        if isinstance(self._client.strategy, NovelStrategy):
            self._client.strategy.novel_title = title
            self._client.strategy.protagonist_bio = meta.protagonist_bio
            self._client.strategy.background_story = meta.background_story
            self._client.strategy.writing_demand = meta.writing_demand
            self._client.strategy.genre = meta.genre
            self._client.strategy.style_tone = meta.style_tone
            self._client.strategy.xp_mode = bool(meta.xp_mode)

    def _on_novel_title_changed(self, text: str) -> None:
        if isinstance(self._client.strategy, NovelStrategy):
            self._client.strategy.novel_title = text.strip()

    def _on_chapter_title_changed(self, text: str) -> None:
        if isinstance(self._client.strategy, NovelStrategy):
            self._client.strategy.chapter_title = text.strip()

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

    # ========== 题材与风格基调 Handler ==========

    def _apply_genre_params(self, display_name: str) -> None:
        """根据题材显示名设置推荐参数（temperature / frequency_penalty）"""
        cfg = get_genre_by_display(display_name)
        if cfg is None:
            return
        changed = False
        if cfg.temperature is not None:
            self._client.set_temperature(cfg.temperature)
            int_val = int(cfg.temperature * 100)
            self._temp_slider.setValue(int_val)
            self._temp_value.setText(f"{cfg.temperature:.2f}")
            changed = True
        if cfg.frequency_penalty is not None:
            self._client.set_frequency_penalty(cfg.frequency_penalty)
            int_val = int(cfg.frequency_penalty * 100)
            self._fp_slider.setValue(int_val)
            self._fp_value.setText(f"{cfg.frequency_penalty:.2f}")
            changed = True
        if changed:
            self._preset_combo.setCurrentText(CUSTOM_LABEL)

    def _sync_genre_tone_to_cont(self) -> None:
        """将小说面板的题材/风格同步到续写面板（blockSignals 防递归）"""
        self._cont_genre_combo.blockSignals(True)
        self._cont_genre_combo.setCurrentText(self._novel_genre_combo.currentText())
        self._cont_genre_combo.blockSignals(False)
        self._cont_tone_combo.blockSignals(True)
        self._cont_tone_combo.setCurrentText(self._novel_tone_combo.currentText())
        self._cont_tone_combo.blockSignals(False)

    def _sync_xp_mode_to_cont(self) -> None:
        self._cont_xp_mode_check.blockSignals(True)
        self._cont_xp_mode_check.setChecked(self._xp_mode_check.isChecked())
        self._cont_xp_mode_check.blockSignals(False)

    def _sync_genre_tone_to_novel(self) -> None:
        """将续写面板的题材/风格同步到小说面板（blockSignals 防递归）"""
        self._novel_genre_combo.blockSignals(True)
        self._novel_genre_combo.setCurrentText(self._cont_genre_combo.currentText())
        self._novel_genre_combo.blockSignals(False)
        self._novel_tone_combo.blockSignals(True)
        self._novel_tone_combo.setCurrentText(self._cont_tone_combo.currentText())
        self._novel_tone_combo.blockSignals(False)

    def _sync_xp_mode_to_novel(self) -> None:
        self._xp_mode_check.blockSignals(True)
        self._xp_mode_check.setChecked(self._cont_xp_mode_check.isChecked())
        self._xp_mode_check.blockSignals(False)

    def _on_xp_mode_changed(self, checked: bool) -> None:
        if isinstance(self._client.strategy, NovelStrategy):
            self._client.strategy.xp_mode = checked
        self._sync_xp_mode_to_cont()
        title = self._novel_title_edit.text().strip()
        if title:
            self._novel_manager.save_meta(title, xp_mode=checked)

    def _on_cont_xp_mode_changed(self, checked: bool) -> None:
        if isinstance(self._client.strategy, NovelStrategy):
            self._client.strategy.xp_mode = checked
        self._sync_xp_mode_to_novel()
        book_title = self._cont_bookshelf_combo.currentText()
        if book_title and not book_title.startswith("（暂无小说"):
            self._novel_manager.save_meta(book_title, xp_mode=checked)

    def _on_novel_genre_changed(self, display_name: str) -> None:
        if not display_name:
            return
        cfg = get_genre_by_display(display_name)
        if cfg is None:
            return
        # 同步到 strategy
        if isinstance(self._client.strategy, NovelStrategy):
            self._client.strategy.genre = cfg.key
        # 同步参数滑块
        self._apply_genre_params(display_name)
        # 同步到续写面板
        self._sync_genre_tone_to_cont()
        # 持久化
        title = self._novel_title_edit.text().strip()
        if title:
            self._novel_manager.save_meta(title, genre=cfg.key)

    def _on_novel_tone_changed(self, display_name: str) -> None:
        if not display_name:
            return
        tone = get_tone_by_display(display_name)
        if tone is None:
            return
        if isinstance(self._client.strategy, NovelStrategy):
            self._client.strategy.style_tone = tone.key
        self._sync_genre_tone_to_cont()
        title = self._novel_title_edit.text().strip()
        if title:
            self._novel_manager.save_meta(title, style_tone=tone.key)

    def _on_cont_genre_changed(self, display_name: str) -> None:
        if not display_name:
            return
        cfg = get_genre_by_display(display_name)
        if cfg is None:
            return
        self._apply_genre_params(display_name)
        self._sync_genre_tone_to_novel()
        book_title = self._cont_bookshelf_combo.currentText()
        if book_title:
            self._novel_manager.save_meta(book_title, genre=cfg.key)

    def _on_cont_tone_changed(self, display_name: str) -> None:
        if not display_name:
            return
        tone = get_tone_by_display(display_name)
        if tone is None:
            return
        self._sync_genre_tone_to_novel()
        book_title = self._cont_bookshelf_combo.currentText()
        if book_title:
            self._novel_manager.save_meta(book_title, style_tone=tone.key)

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
            self._cont_author_plan_edit.clear()
            self._cont_xp_mode_check.blockSignals(True)
            self._cont_xp_mode_check.setChecked(False)
            self._cont_xp_mode_check.blockSignals(False)
            self._sync_xp_mode_to_novel()
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
        self._cont_author_plan_edit.blockSignals(True)
        self._cont_author_plan_edit.setPlainText(getattr(meta, "author_plan", ""))
        self._cont_author_plan_edit.blockSignals(False)

        # 同步题材/风格到下拉框
        genre_display = get_genre_display(meta.genre)
        tone_display = get_tone_display(meta.style_tone)
        self._cont_genre_combo.blockSignals(True)
        self._cont_genre_combo.setCurrentText(genre_display or "无特定风格")
        self._cont_genre_combo.blockSignals(False)
        self._cont_tone_combo.blockSignals(True)
        self._cont_tone_combo.setCurrentText(tone_display or "默认")
        self._cont_tone_combo.blockSignals(False)
        self._cont_xp_mode_check.blockSignals(True)
        self._cont_xp_mode_check.setChecked(bool(meta.xp_mode))
        self._cont_xp_mode_check.blockSignals(False)
        self._sync_genre_tone_to_novel()
        self._sync_xp_mode_to_novel()
        if isinstance(self._client.strategy, NovelStrategy):
            self._client.strategy.xp_mode = bool(meta.xp_mode)

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

        next_ch = self._novel_manager.get_active_generation_target(title)["chapter_num"]
        chapters = self._novel_manager.list_chapters(title)
        self._cont_chapter_info_label.setText(
            f"已有 {len(chapters)} 章，下一章编号: 第{next_ch}章"
        )

    def _on_cont_create_book(self) -> None:
        """续写面板：新建小说"""
        from PyQt6.QtWidgets import QInputDialog
        title, ok = QInputDialog.getText(self, "新建小说", "请输入小说标题：")
        if ok and title.strip():
            existing = self._novel_manager.list_books()
            if title.strip() in existing:
                QMessageBox.warning(self, "警告", f"小说「{title.strip()}」已存在。")
                return
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
            ok = self._novel_manager.delete_book(title)
            if not ok:
                QMessageBox.warning(self, "删除失败", f"无法删除小说「{title}」，请检查文件权限。")
                return
            self._refresh_novel_bookshelf()
            self._on_cont_book_selected(self._cont_bookshelf_combo.currentText())

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
            author_plan=self._cont_author_plan_edit.toPlainText().strip(),
            xp_mode=self._cont_xp_mode_check.isChecked(),
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
        self._cont_author_plan_edit.blockSignals(True)
        self._cont_author_plan_edit.setPlainText(getattr(meta, "author_plan", ""))
        self._cont_author_plan_edit.blockSignals(False)
        self._cont_xp_mode_check.blockSignals(True)
        self._cont_xp_mode_check.setChecked(bool(meta.xp_mode))
        self._cont_xp_mode_check.blockSignals(False)
        self._sync_xp_mode_to_novel()

    def _on_cont_generate_chapter(self) -> None:
        """续写面板：生成下一章"""
        if self._streaming or not self._chapter_finalized:
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

        generation_target = self._novel_manager.get_active_generation_target(book_title)
        chapter_num = int(generation_target["chapter_num"])
        chapter_title = self._cont_chapter_title_edit.text().strip()
        if not chapter_title:
            chapter_title = f"续写 (第{chapter_num}章)"
            self._cont_chapter_title_edit.setText(chapter_title)

        requirement = self._continue_requirement.toPlainText().strip()
        word_count = self._continue_word_count.value()
        plot = self._continue_plot.toPlainText().strip()
        self._chapter_finalized = False
        self._generate_btn.setEnabled(False)
        self._cont_generate_btn.setEnabled(False)
        self._client.reset_cancel()
        self._stop_btn.setVisible(True)
        self._stop_btn.setEnabled(True)
        self._stop_btn.setText("⏹")
        self._mode_combo.setEnabled(False)
        self._streaming = True
        self._streaming_start_time = time.time()
        self._assistant_text_buffer = []
        self._append_user_message(f"📝 续写「{book_title}」→ 第{chapter_num}章「{chapter_title}」")

        # 保存当前设定到 meta.json
        self._novel_manager.save_meta(
            book_title,
            protagonist_bio=self._cont_protagonist_edit.toPlainText().strip(),
            background_story=self._cont_background_edit.toPlainText().strip(),
            writing_demand=self._cont_demand_edit.toPlainText().strip(),
            author_plan=self._cont_author_plan_edit.toPlainText().strip(),
            xp_mode=self._cont_xp_mode_check.isChecked(),
        )

        threading.Thread(
            target=self._run_continuation,
            args=(
                book_title, chapter_num, chapter_title, source_text,
                requirement, word_count, plot, "", generation_target,
            ),
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
            files = [f for f in os.listdir(source_folder) if os.path.splitext(f)[1].lower() in ext_map]
            if not files:
                return ""
            # 文件夹模式只验证文件存在，完整内容由 SectionPreviewDialog 读取
            return "[文件夹模式]"
        return ""

    def _on_save_novel_settings(self) -> None:
        """保存当前小说设定到 meta.json"""
        title = self._novel_title_edit.text().strip()
        if not title:
            QMessageBox.warning(self, "提示", "请先设置小说标题。")
            return
        genre_cfg = get_genre_by_display(self._novel_genre_combo.currentText())
        tone_cfg = get_tone_by_display(self._novel_tone_combo.currentText())
        self._novel_manager.create_book(title)
        self._novel_manager.save_meta(
            title,
            protagonist_bio=self._protagonist_edit.toPlainText().strip(),
            background_story=self._background_edit.toPlainText().strip(),
            writing_demand=self._demand_edit.toPlainText().strip(),
            author_plan=self._author_plan_edit.toPlainText().strip(),
            genre=genre_cfg.key if genre_cfg else "",
            style_tone=tone_cfg.key if tone_cfg else "",
            xp_mode=self._xp_mode_check.isChecked(),
        )
        self._refresh_novel_bookshelf()
        self._bookshelf_combo.setCurrentText(title)
        if isinstance(self._client.strategy, NovelStrategy):
            self._client.strategy.novel_title = title
            self._client.strategy.protagonist_bio = self._protagonist_edit.toPlainText().strip()
            self._client.strategy.background_story = self._background_edit.toPlainText().strip()
            self._client.strategy.writing_demand = self._demand_edit.toPlainText().strip()
            self._client.strategy.genre = genre_cfg.key if genre_cfg else ""
            self._client.strategy.style_tone = tone_cfg.key if tone_cfg else ""
            self._client.strategy.xp_mode = self._xp_mode_check.isChecked()
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
        self._author_plan_edit.blockSignals(True)
        self._author_plan_edit.setPlainText(getattr(meta, "author_plan", ""))
        self._author_plan_edit.blockSignals(False)
        self._on_book_selected(title)

    # ========== 🚀 生成章节 ==========

    def _on_generate_chapter(self) -> None:
        """生成下一章 → 完整工作流"""
        if self._streaming or not self._chapter_finalized:
            return

        title = self._novel_title_edit.text().strip()
        chapter_title = self._chapter_title_edit.text().strip()
        if not title:
            QMessageBox.warning(self, "提示", "请先设置小说标题。")
            return
        self._novel_manager.create_book(title)
        generation_target = self._novel_manager.get_active_generation_target(title)
        chapter_num = int(generation_target["chapter_num"])
        if not chapter_title:
            chapter_title = f"第{chapter_num}章"
            self._chapter_title_edit.setText(chapter_title)

        self._chapter_finalized = False
        self._generate_btn.setEnabled(False)
        self._cont_generate_btn.setEnabled(False)
        self._client.reset_cancel()
        self._stop_btn.setVisible(True)
        self._stop_btn.setEnabled(True)
        self._stop_btn.setText("⏹")
        self._mode_combo.setEnabled(False)
        self._streaming = True
        self._streaming_start_time = time.time()
        self._assistant_text_buffer = []

        # 同步 UI 值到策略对象
        if isinstance(self._client.strategy, NovelStrategy):
            self._client.strategy.chapter_title = chapter_title
            self._client.strategy.novel_title = title
            self._client.strategy.protagonist_bio = self._protagonist_edit.toPlainText().strip()
            self._client.strategy.background_story = self._background_edit.toPlainText().strip()
            self._client.strategy.writing_demand = self._demand_edit.toPlainText().strip()
            genre_cfg_sync = get_genre_by_display(self._novel_genre_combo.currentText())
            tone_cfg_sync = get_tone_by_display(self._novel_tone_combo.currentText())
            self._client.strategy.genre = genre_cfg_sync.key if genre_cfg_sync else ""
            self._client.strategy.style_tone = tone_cfg_sync.key if tone_cfg_sync else ""
            self._client.strategy.xp_mode = self._xp_mode_check.isChecked()

        # 保存当前设定到 meta.json
        genre_cfg_save = get_genre_by_display(self._novel_genre_combo.currentText())
        tone_cfg_save = get_tone_by_display(self._novel_tone_combo.currentText())
        self._novel_manager.save_meta(
            title,
            protagonist_bio=self._protagonist_edit.toPlainText().strip(),
            background_story=self._background_edit.toPlainText().strip(),
            writing_demand=self._demand_edit.toPlainText().strip(),
            author_plan=self._author_plan_edit.toPlainText().strip(),
            genre=genre_cfg_save.key if genre_cfg_save else "",
            style_tone=tone_cfg_save.key if tone_cfg_save else "",
            xp_mode=self._xp_mode_check.isChecked(),
        )

        self._append_user_message(f"📖 生成第{chapter_num}章：{chapter_title}")

        # 在主线程中捕获 UI 值，避免后台线程访问 QWidget
        plot_content = self._plot_edit.toPlainText().strip()
        target_words = self._chapter_word_count.value()

        threading.Thread(
            target=self._run_chapter_generation,
            args=(title, chapter_title, plot_content, target_words, generation_target),
            daemon=True,
        ).start()

    def _build_chapter_prompt(
        self,
        title: str,
        chapter_title: str,
        plot_content: str = "",
        chapter_num: int | None = None,
    ) -> str:
        """构造章节续写的完整 User Prompt（含历史记录参考）"""
        chapter_num = chapter_num or self._novel_manager.get_active_generation_target(title)["chapter_num"]

        # 智能前情提要（剧情摘要）
        client = self._usage_logged_client("novel_context_summary") if hasattr(self, '_client') else None
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
            xp_mode = strategy.xp_mode
        else:
            bio = bg = demand = ""
            xp_mode = False
        # plot_content 在 _on_generate_chapter 中捕获后传入

        # 注入世界书信息
        world_bible_text = ""
        try:
            bible = self._novel_manager.load_world_bible(title)
            if bible and (bible.characters or bible.locations or bible.rules or bible.active_plot_threads):
                from core.world_bible import format_relevant_world_bible_for_prompt
                world_bible_text = format_relevant_world_bible_for_prompt(
                    bible,
                    f"{chapter_title}\n{plot_content}\n{demand}",
                    active_chapters={
                        int(node.get("chapter_num", 0) or 0)
                        for node in self._novel_manager.get_active_path_nodes(title)
                    },
                    target_chapter=chapter_num,
                    token_budget=4000,
                )
        except Exception:
            pass

        parts = [f"【前情提要】：\n{summary}\n"]
        try:
            contract = self._novel_manager.build_continuity_contract(
                title, chapter_num, chapter_title, plot_content
            )
            if contract:
                parts.append(f"{contract}\n")
        except Exception:
            pass
        try:
            author_plan = self._novel_manager.build_author_planning_prompt(title)
            if author_plan:
                parts.append(f"{author_plan}\n")
        except Exception:
            pass
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
        if xp_mode:
            parts.append(f"{Prompts.XP_MODE_SYSTEM}\n")

        return "\n".join(parts)

    def _audit_and_repair_chapter_content(
        self,
        *,
        title: str,
        chapter_num: int,
        chapter_title: str,
        content: str,
        context: str,
        xp_mode: bool,
        operation_prefix: str,
    ) -> str:
        """检查生成正文与长篇上下文的一致性，必要时做最小修补。"""
        if not content.strip():
            return content
        try:
            from utils.continuity import audit_chapter_continuity, repair_chapter_continuity

            self._stream_signals.token.emit("\n🧭 正在检查章节连贯性...\n")
            audit = audit_chapter_continuity(
                self._usage_logged_client(f"{operation_prefix}_continuity_audit"),
                chapter_content=content,
                context=context,
                chapter_title=f"第{chapter_num}章「{chapter_title}」",
                model=self._client.model,
                global_user_prompt=self._client.global_user_prompt,
                xp_mode=xp_mode,
            )
            issues = audit.get("issues", [])
            if not audit.get("has_issues") or not issues:
                self._stream_signals.token.emit("✅ 连贯性检查通过。\n")
                return content

            major_count = sum(1 for item in issues if item.get("severity") == "major")
            issue_types = "、".join(
                str(item.get("type", "逻辑")).strip() for item in issues[:3] if item.get("type")
            )
            self._stream_signals.token.emit(
                f"⚠️ 发现 {len(issues)} 个连贯性问题"
                f"{f'（严重 {major_count} 个）' if major_count else ''}"
                f"{f'：{issue_types}' if issue_types else ''}，正在定向修补...\n"
            )
            repaired = repair_chapter_continuity(
                self._usage_logged_client(f"{operation_prefix}_continuity_repair"),
                chapter_content=content,
                context=context,
                audit_result=audit,
                chapter_title=f"第{chapter_num}章「{chapter_title}」",
                model=self._client.model,
                temperature=min(float(getattr(self._client, "temperature", 0.7)), 0.5),
                global_user_prompt=self._client.global_user_prompt,
                xp_mode=xp_mode,
            )
            if repaired:
                self._stream_signals.token.emit("✅ 连贯性修补完成，后续摘要和世界书将基于修补版正文。\n")
                return repaired
            self._stream_signals.token.emit("⚠️ 连贯性修补未产出有效正文，保留原生成结果。\n")
        except Exception as e:
            self._stream_signals.token.emit(f"⚠️ 连贯性检查跳过: {e}\n")
        return content

    def _supervise_chapter_content(
        self, *, chapter_num: int, chapter_title: str, content: str,
        context: str, chapter_outline: str, requirements: str,
        target_words: int, xp_mode: bool, operation_prefix: str,
    ) -> tuple[str, dict]:
        """Run the chapter supervision quality gate and return content plus report."""
        if not content.strip():
            return content, {"status": "warning", "audit_failed": True, "error": "empty chapter"}
        try:
            from utils.supervision import supervise_chapter

            def progress(stage: str) -> None:
                messages = {
                    "audit": "\n[Supervision] Auditing outline, constraints, and continuity...\n",
                    "repair": "[Supervision] Repairing failed checks...\n",
                    "reaudit": "[Supervision] Re-auditing repaired chapter...\n",
                }
                self._stream_signals.token.emit(messages.get(stage, ""))

            final_content, result = supervise_chapter(
                lambda action: self._usage_logged_client(f"{operation_prefix}_supervision_{action}"),
                chapter_content=content,
                chapter_title=f"Chapter {chapter_num}: {chapter_title}",
                chapter_outline=chapter_outline,
                requirements=requirements,
                continuity_context=context,
                target_words=target_words,
                model=self._client.model,
                temperature=min(float(getattr(self._client, "temperature", 0.7)), 0.5),
                global_user_prompt=self._client.global_user_prompt,
                xp_mode=xp_mode,
                max_repair_rounds=2,
                progress=progress,
            )
            fulfilled = sum(1 for item in result.outline_items if item.get("status") == "fulfilled")
            total = len(result.outline_items)
            coverage = f", outline coverage {fulfilled}/{total}" if total else ""
            if result.status == "passed":
                self._stream_signals.token.emit(
                    f"[Supervision] Passed{coverage}; repair rounds: {result.repair_rounds}.\n"
                )
            else:
                self._stream_signals.token.emit(
                    f"[Supervision] Completed{coverage}; {len(result.unresolved_issues)} risks remain. "
                    f"Keeping the last valid chapter after {result.repair_rounds} repair rounds.\n"
                )
            return final_content, result.to_dict()
        except Exception as e:
            self._stream_signals.token.emit(f"[Supervision] Skipped after error: {e}\n")
            return content, {
                "status": "warning", "audit_failed": True, "error": str(e),
                "outline_items": [], "hard_constraint_issues": [],
                "continuity_issues": [], "repair_rounds": 0,
            }

    def _run_chapter_generation(
        self,
        title: str,
        chapter_title: str,
        plot_content: str = "",
        target_words: int = 40000,
        generation_target: dict | None = None,
    ) -> None:
        """后台线程：生成章节 + 版本保存 + 摘要"""
        try:
            generation_target = generation_target or self._novel_manager.get_active_generation_target(title)
            chapter_num = int(generation_target["chapter_num"])

            strategy = self._client.strategy
            messages = [{"role": "system", "content": Prompts.NOVEL_CHAPTER_WRITING}]
            xp_mode = isinstance(strategy, NovelStrategy) and strategy.xp_mode
            if xp_mode:
                messages.append({"role": "system", "content": Prompts.XP_MODE_SYSTEM})
            messages.append({
                "role": "system",
                "content": (
                    f"【本章硬性字数要求】本章字数不少于{target_words}字。"
                    "请通过场景细节、对话交互、动作过程和内心活动自然充实内容，"
                    "不得用解释、提纲或作者说明凑字数。"
                ),
            })
            if isinstance(strategy, NovelStrategy):
                messages += strategy.build_system_messages()

            user_prompt = self._build_chapter_prompt(
                title,
                chapter_title,
                plot_content=plot_content,
                chapter_num=chapter_num,
            )
            messages.append({"role": "user", "content": user_prompt})

            self._stream_signals.token.emit(f"\n\n📝 正在创作第 {chapter_num} 章「{chapter_title}」...\n\n")

            content, generation_stats, cancelled = self._stream_chapter_completion(
                operation="novel_chapter",
                messages=messages,
                prompt_text=user_prompt,
                max_tokens=max(target_words * 2, self._client.max_tokens),
            )
            if cancelled:
                self._stream_signals.token.emit("\n\n⏹️ 已取消\n")
                self._stream_signals.finished.emit()
                return

            content, supervision_report = self._supervise_chapter_content(
                chapter_num=chapter_num,
                chapter_title=chapter_title,
                content=content,
                context=user_prompt,
                chapter_outline=plot_content,
                requirements=getattr(strategy, "writing_demand", ""),
                target_words=target_words,
                xp_mode=xp_mode,
                operation_prefix="novel_chapter",
            )

            if self._client._cancel_requested:
                self._stream_signals.token.emit("\n\n⏹️ 已取消\n")
                self._stream_signals.finished.emit()
                return

            # 确定版本号
            existing_versions = self._novel_manager.get_chapter_versions(title, chapter_num)
            if existing_versions:
                version = int(generation_target["version"])
                old_active = self._novel_manager.get_active_version(title, chapter_num)
                new_chapter = False
            else:
                version = int(generation_target["version"])
                old_active = None
                new_chapter = True

            file_path, saved_version = self._novel_manager.save_chapter_version(
                title,
                chapter_num,
                chapter_title,
                content,
                version=version,
                parent_id=generation_target["parent_id"],
            )
            self._novel_manager.switch_active_node(
                title, self._novel_manager._node_id(chapter_num, saved_version)
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
                supervision_report=supervision_report,
                plot=plot_content,
            )

            if self._client._cancel_requested:
                self._stream_signals.token.emit("\n\n⏹️ 已取消\n")
                self._stream_signals.finished.emit()
                return

            self._stream_signals.token.emit("\n🔍 正在提炼剧情记忆...\n")
            summary = self._novel_manager.generate_summary(
                self._usage_logged_client("novel_summary"), content, chapter_num, chapter_title,
                global_user_prompt=self._client.global_user_prompt,
                xp_mode=xp_mode,
            )
            if summary.strip():
                self._novel_manager.set_chapter_node_summary(title, chapter_num, saved_version, summary)
            self._novel_manager.rebuild_plot_summary_from_tree(title)
            self._stream_signals.token.emit(f"📋 剧情摘要已绑定至章节树。\n\n")

            if self._client._cancel_requested:
                self._stream_signals.token.emit("\n\n⏹️ 已取消\n")
                self._stream_signals.finished.emit()
                return

            # 更新世界书
            self._stream_signals.token.emit("\n📖 正在更新世界书...\n")
            try:
                from core.world_bible import extract_and_merge_world_bible
                bible = self._novel_manager.load_world_bible(title)
                updated_bible = extract_and_merge_world_bible(
                    self._usage_logged_client("world_bible_update"), content, chapter_num, bible, self._client.model,
                    chapter_version=saved_version,
                    global_user_prompt=self._client.global_user_prompt,
                    xp_mode=xp_mode,
                )
                self._novel_manager.save_world_bible(title, updated_bible)
                self._stream_signals.token.emit("✅ 世界书已更新。\n")
                if getattr(updated_bible, "consistency_warnings", None):
                    self._stream_signals.token.emit(
                        f"⚠️ 世界书发现 {len(updated_bible.consistency_warnings)} 条一致性提醒，可在世界书窗口查看。\n"
                    )
            except Exception as wb_e:
                self._stream_signals.token.emit(f"⚠️ 世界书更新跳过: {wb_e}\n")

            self._refresh_chapter_info_display(title)
            next_ch = self._novel_manager.get_active_generation_target(title)["chapter_num"]

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
        client = self._usage_logged_client("continuation_segment") if hasattr(self, '_client') else None
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
            genre_cfg_cont = get_genre_by_display(self._cont_genre_combo.currentText())
            tone_cfg_cont = get_tone_by_display(self._cont_tone_combo.currentText())
            self._novel_manager.save_meta(
                book_title,
                protagonist_bio=self._cont_protagonist_edit.toPlainText().strip(),
                background_story=self._cont_background_edit.toPlainText().strip(),
                writing_demand=self._cont_demand_edit.toPlainText().strip(),
                author_plan=self._cont_author_plan_edit.toPlainText().strip(),
                genre=genre_cfg_cont.key if genre_cfg_cont else "",
                style_tone=tone_cfg_cont.key if tone_cfg_cont else "",
                xp_mode=self._cont_xp_mode_check.isChecked(),
            )
            self._refresh_novel_bookshelf()
            self._cont_bookshelf_combo.setCurrentText(book_title)

        generation_target = self._novel_manager.get_active_generation_target(book_title)
        chapter_num = int(generation_target["chapter_num"])
        chapter_title = self._cont_chapter_title_edit.text().strip()
        if not chapter_title:
            chapter_title = f"续写 (第{chapter_num}章)"

        self._client.reset_cancel()
        self._stop_btn.setVisible(True)
        self._stop_btn.setEnabled(True)
        self._stop_btn.setText("⏹")
        self._mode_combo.setEnabled(False)
        self._streaming = True
        self._streaming_start_time = time.time()
        self._assistant_text_buffer = []

        notice = f"续写「{os.path.basename(source_file) if source_file else os.path.basename(source_folder)}」→ 第{chapter_num}章"
        self._append_user_message(notice)

        # 保存当前设定到 meta.json
        self._novel_manager.save_meta(
            book_title,
            protagonist_bio=self._cont_protagonist_edit.toPlainText().strip(),
            background_story=self._cont_background_edit.toPlainText().strip(),
            writing_demand=self._cont_demand_edit.toPlainText().strip(),
            author_plan=self._cont_author_plan_edit.toPlainText().strip(),
            xp_mode=self._cont_xp_mode_check.isChecked(),
        )

        threading.Thread(
            target=self._run_continuation,
            args=(
                book_title, chapter_num, chapter_title, source_text,
                requirement, word_count, plot, "", generation_target,
            ),
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
        generation_target: dict | None = None,
    ) -> None:
        """后台线程：执行续写（增强版：含世界书+剧情摘要+设定）"""
        try:
            generation_target = generation_target or self._novel_manager.get_active_generation_target(book_title)
            # ── 构建 User Prompt（含前情提要 + 世界书 + 设定） ──
            user_parts = []
            if source_text:
                user_parts.append(f"【原文内容】\n{source_text}\n")

            # 加载前情提要（复用小说模式的智能摘要算法）
            try:
                summary = self._novel_manager.load_smart_summary(
                    book_title, self._usage_logged_client("continuation_context_summary"),
                    next_chapter_num=chapter_num,
                    max_recent=10,
                    model=self._client.model,
                    global_user_prompt=self._client.global_user_prompt,
                )
                if summary and "故事刚刚开始" not in summary:
                    user_parts.append(f"【前情提要】\n{summary}\n")
            except Exception:
                pass

            # 加载世界书
            try:
                from core.world_bible import format_relevant_world_bible_for_prompt
                bible = self._novel_manager.load_world_bible(book_title)
                if bible:
                    wb_text = format_relevant_world_bible_for_prompt(
                        bible,
                        f"{chapter_title}\n{requirement}\n{plot}\n{source_text[-2000:]}",
                        active_chapters={
                            int(node.get("chapter_num", 0) or 0)
                            for node in self._novel_manager.get_active_path_nodes(book_title)
                        },
                        target_chapter=chapter_num,
                        token_budget=4000,
                    )
                    if wb_text.strip():
                        user_parts.append(f"【世界书（已建立设定库）】\n{wb_text}\n")
            except Exception:
                pass

            try:
                contract = self._novel_manager.build_continuity_contract(
                    book_title, chapter_num, chapter_title, plot
                )
                if contract:
                    user_parts.append(f"{contract}\n")
            except Exception:
                pass
            try:
                author_plan = self._novel_manager.build_author_planning_prompt(book_title)
                if author_plan:
                    user_parts.append(f"{author_plan}\n")
            except Exception:
                pass

            # 加载小说设定（优先使用 analysis 传入的 setting，否则读 meta.json）
            bg_story = setting
            protagonist_bio = ""
            xp_mode = False
            try:
                meta = self._novel_manager.load_meta(book_title)
                if not bg_story:
                    bg_story = meta.background_story
                protagonist_bio = meta.protagonist_bio
                xp_mode = bool(meta.xp_mode)
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
            if xp_mode:
                user_parts.append(f"{Prompts.XP_MODE_SYSTEM}\n")

            user_parts.append(
                f"请直接输出续写正文，不要加任何解释或前言。"
                f"字数不少于{word_count}字，请通过扩展场景细节、增加对话交互、深入刻画内心活动来充实内容。"
            )

            user_prompt = "\n".join(user_parts)
            messages = [{"role": "system", "content": Prompts.NOVEL_CHAPTER_WRITING}]
            if xp_mode:
                messages.append({"role": "system", "content": Prompts.XP_MODE_SYSTEM})
            if bg_story:
                messages.append({"role": "system", "content": f"【核心设定】\n{bg_story}"})
            if protagonist_bio:
                messages.append({"role": "system", "content": f"【人物背景】\n{protagonist_bio}"})
            # 风格设定
            genre_key = meta.genre if hasattr(meta, 'genre') else ""
            tone_key = meta.style_tone if hasattr(meta, 'style_tone') else ""
            if genre_key or tone_key:
                style_parts = []
                gcfg = get_genre_by_key(genre_key)
                if gcfg and gcfg.style_instruction:
                    style_parts.append(f"题材方向（{gcfg.display_name}）：{gcfg.style_instruction}")
                tcfg = get_tone_by_key(tone_key)
                if tcfg and tcfg.style_instruction:
                    style_parts.append(f"写作基调（{tcfg.display_name}）：{tcfg.style_instruction}")
                if style_parts:
                    messages.append({"role": "system", "content": f"【风格设定】\n{chr(10).join(style_parts)}"})
            messages.append({"role": "user", "content": user_prompt})

            self._stream_signals.token.emit(
                f"\n\n📝 正在续写第 {chapter_num} 章「{chapter_title}」...\n\n"
            )

            content, generation_stats, cancelled = self._stream_chapter_completion(
                operation="continuation",
                messages=messages,
                prompt_text=user_prompt,
                max_tokens=max(word_count * 2, self._client.max_tokens),
            )
            if cancelled:
                self._stream_signals.token.emit("\n\n⏹️ 已取消\n")
                self._stream_signals.finished.emit()
                return

            content, supervision_report = self._supervise_chapter_content(
                chapter_num=chapter_num,
                chapter_title=chapter_title,
                content=content,
                context=user_prompt,
                chapter_outline=plot,
                requirements=requirement,
                target_words=word_count,
                xp_mode=xp_mode,
                operation_prefix="continuation",
            )

            if self._client._cancel_requested:
                self._stream_signals.token.emit("\n\n⏹️ 已取消\n")
                self._stream_signals.finished.emit()
                return

            # 保存为章节
            file_path, saved_version = self._novel_manager.save_chapter_version(
                book_title,
                chapter_num,
                chapter_title,
                content,
                version=int(generation_target["version"]),
                parent_id=generation_target["parent_id"],
            )
            self._novel_manager.switch_active_node(
                book_title, self._novel_manager._node_id(chapter_num, saved_version)
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
                supervision_report=supervision_report,
                plot=plot,
            )

            if self._client._cancel_requested:
                self._stream_signals.token.emit("\n\n⏹️ 已取消\n")
                self._stream_signals.finished.emit()
                return

            self._stream_signals.token.emit("\n🔍 正在提炼剧情记忆...\n")
            summary = self._novel_manager.generate_summary(
                self._usage_logged_client("continuation_summary"), content, chapter_num, chapter_title,
                global_user_prompt=self._client.global_user_prompt,
                xp_mode=xp_mode,
            )
            if summary.strip():
                self._novel_manager.set_chapter_node_summary(book_title, chapter_num, saved_version, summary)
            self._novel_manager.rebuild_plot_summary_from_tree(book_title)
            self._stream_signals.token.emit("📋 剧情摘要已绑定至章节树。\n")

            if self._client._cancel_requested:
                self._stream_signals.token.emit("\n\n⏹️ 已取消\n")
                self._stream_signals.finished.emit()
                return

            # 更新世界书
            self._stream_signals.token.emit("📖 正在更新世界书...\n")
            try:
                from core.world_bible import extract_and_merge_world_bible
                bible = self._novel_manager.load_world_bible(book_title)
                updated_bible = extract_and_merge_world_bible(
                    self._usage_logged_client("world_bible_update"), content, chapter_num, bible, self._client.model,
                    chapter_version=saved_version,
                    global_user_prompt=self._client.global_user_prompt,
                    xp_mode=xp_mode,
                )
                self._novel_manager.save_world_bible(book_title, updated_bible)
                self._stream_signals.token.emit("✅ 世界书已更新。\n")
                if getattr(updated_bible, "consistency_warnings", None):
                    self._stream_signals.token.emit(
                        f"⚠️ 世界书发现 {len(updated_bible.consistency_warnings)} 条一致性提醒，可在世界书窗口查看。\n"
                    )
            except Exception as wb_e:
                self._stream_signals.token.emit(f"⚠️ 世界书更新跳过: {wb_e}\n")

            self._stream_signals.refresh_chapter_info.emit(book_title)
            self._stream_signals.token.emit(
                f"\n📖 下一章：第{self._novel_manager.get_active_generation_target(book_title)['chapter_num']}章\n"
            )
            self._stream_signals.finished.emit()
        except Exception as e:
            self._stream_signals.error.emit(f"续写失败: {e}")

    # ========== 发送消息 ==========

    def _on_send(self) -> None:
        """发送按钮点击处理"""
        if self._streaming:
            # streaming 卡死超过 180 秒则强制复位，允许重新发送
            if self._streaming_start_time and time.time() - self._streaming_start_time > 180:
                self._streaming = False
                self._chapter_finalized = True
                self._streaming_start_time = 0
                self._stop_btn.setVisible(False)
                self._stop_btn.setEnabled(True)
                self._mode_combo.setEnabled(True)
                self._generate_btn.setEnabled(True)
                self._cont_generate_btn.setEnabled(True)
            else:
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
        if isinstance(self._client.strategy, RolePlayStrategy):
            self._sync_role_strategy()

        self._input_box.clear()
        self._append_user_message(user_input)
        self._conversation_dirty = True
        if isinstance(self._client.strategy, RolePlayStrategy):
            self._record_structured_user_message(user_input)

        self._client.reset_cancel()
        self._stop_btn.setVisible(True)
        self._stop_btn.setEnabled(True)
        self._stop_btn.setText("⏹")
        self._mode_combo.setEnabled(False)
        self._streaming = True
        self._streaming_start_time = time.time()
        self._assistant_text_buffer = []
        self._last_chat_user_input = user_input

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
            usage = self._client.last_usage
            strategy_name = self._client.strategy.get_name()
            self._token_log_manager.add_entry(
                operation="chat",
                direction="send",
                strategy=strategy_name,
                model=self._client.model,
                content=user_input,
                usage=usage,
            )
            assistant_messages = [
                m.get("content", "") for m in self._client.export_messages()
                if m.get("role") == "assistant"
            ]
            assistant_content = assistant_messages[-1] if assistant_messages else ""
            if isinstance(self._client.strategy, RolePlayStrategy):
                branch = self._chat_state.active_branch()
                message_count_before_parse = len(branch.messages)
                try:
                    structured = self._parse_and_store_assistant_messages(assistant_content)
                    assistant_content = "\n\n".join(
                        f"{message.speaker_name}：{message.content}" for message in structured
                    )
                except Exception as responder_error:
                    if assistant_content.strip() and len(branch.messages) == message_count_before_parse:
                        fallback = ChatMessage(
                            message_id=new_id("msg"),
                            branch_id=branch.branch_id,
                            role="assistant",
                            speaker_id="assistant",
                            speaker_name="群聊回复",
                            content=assistant_content.strip(),
                            turn_index=self._current_turn_index(),
                            created_at=now_text(),
                        )
                        branch.messages.append(fallback)
                        self._last_structured_assistant_messages = [fallback]
                    self._stream_signals.token.emit(
                        f"\n\n⚠️ 结构化群聊处理失败，已按原文显示：{responder_error}\n"
                    )
            self._token_log_manager.add_entry(
                operation="chat",
                direction="receive",
                strategy=strategy_name,
                model=self._client.model,
                content=assistant_content,
                usage=usage,
            )
            should_sync_character_book = (
                isinstance(self._client.strategy, RolePlayStrategy)
                and bool(self._participant_character_ids)
                and bool(assistant_content.strip())
            )
            sync_context = None
            if should_sync_character_book:
                branch = self._chat_state.active_branch()
                turn_index = self._current_turn_index()
                sync_context = {
                    "branch_id": branch.branch_id,
                    "turn_index": turn_index,
                    "participant_ids": list(self._participant_character_ids),
                    "present_character_ids": list(
                        self._chat_state.scene_state.present_character_ids
                    ),
                    "timeline": copy.deepcopy(self._chat_timeline),
                    "source_message_ids": [
                        message.message_id for message in branch.messages
                        if message.turn_index == turn_index
                    ],
                    "sender_name": self._sender_name,
                    "sender_profile": self._sender_profile,
                }
            self._stream_signals.finished.emit()
            if sync_context:
                threading.Thread(
                    target=self._sync_character_book_after_chat,
                    args=(user_input, assistant_content, sync_context),
                    daemon=True,
                ).start()
        except Exception as e:
            self._stream_signals.error.emit(str(e))

    def _record_structured_user_message(self, content: str) -> ChatMessage:
        branch = self._chat_state.active_branch()
        turn_index = self._current_turn_index() + 1
        message = ChatMessage(
            message_id=new_id("msg"),
            branch_id=branch.branch_id,
            role="user",
            speaker_id=self._chat_state.sender_profile_id or "sender",
            speaker_name=self._sender_name or "你",
            content=content,
            turn_index=turn_index,
            created_at=now_text(),
        )
        branch.messages.append(message)
        return message

    def _parse_and_store_assistant_messages(self, raw: str) -> list[ChatMessage]:
        branch = self._chat_state.active_branch()
        turn_index = self._current_turn_index()
        book = self._load_character_book()
        name_to_id = {
            profile.name: profile.character_id
            for profile in book.profiles
            if profile.character_id in self._participant_character_ids
        }
        for profile in book.profiles:
            if profile.character_id not in self._participant_character_ids:
                continue
            for alias in profile.aliases:
                if alias:
                    name_to_id[alias] = profile.character_id
        messages = parse_structured_reply(raw, branch.branch_id, turn_index, name_to_id)
        if self._current_chat_type == "private":
            profile = find_profile(book, self._primary_character_id)
            for message in messages:
                message.speaker_id = self._primary_character_id or message.speaker_id
                message.speaker_name = profile.name if profile else message.speaker_name
        policy = self._chat_state.turn_policy
        blocked = set(policy.blocked_speaker_ids)
        if self._chat_state.scene_state.present_character_ids:
            blocked.update(
                cid for cid in self._participant_character_ids
                if cid not in self._chat_state.scene_state.present_character_ids
            )
        allowed = set(policy.allowed_speaker_ids)
        messages = [
            message for message in messages
            if message.speaker_id == "narrator"
            or (
                message.speaker_id not in blocked
                and (not allowed or message.speaker_id in allowed)
            )
        ]
        if not self._chat_state.narrator_enabled:
            messages = [message for message in messages if message.speaker_id != "narrator"]
        if policy.mention_only_ids:
            mentioned_ids = {
                profile.character_id
                for profile in book.profiles
                if profile.name and profile.name in self._last_chat_user_input
            }
            messages = [
                message for message in messages
                if message.speaker_id not in policy.mention_only_ids
                or message.speaker_id in mentioned_ids
            ]
        required = list(policy.required_speaker_ids or self._required_responder_ids)
        required = [cid for cid in required if cid not in blocked and (not allowed or cid in allowed)]
        missing = [cid for cid in required if cid not in {message.speaker_id for message in messages}]
        if missing:
            missing_names = self._character_names(missing)
            prompt = (
                "只补充以下角色遗漏的发言：" + "、".join(missing_names)
                + "。输出合法 JSON："
                '{"messages":[{"speaker_id":"角色ID","speaker_name":"角色名","content":"内容","action":""}]}'
            )
            try:
                response = self._client.raw_client.chat.completions.create(
                    model=self._client.model,
                    messages=[
                        {"role": "system", "content": self._client.strategy.get_system_prompt()},
                        {"role": "assistant", "content": raw},
                        {"role": "user", "content": prompt},
                    ],
                    temperature=self._client.temperature,
                    max_tokens=min(self._client.max_tokens, 2048),
                )
                supplement_raw = response.choices[0].message.content or ""
                supplement = parse_structured_reply(
                    supplement_raw, branch.branch_id, turn_index, name_to_id
                )
                messages.extend(
                    message for message in supplement
                    if message.speaker_id in missing
                )
            except Exception as error:
                self._chat_state.consistency_warnings.append(
                    f"必回角色补全失败：{error}"
                )
        if policy.speaker_order:
            order = {speaker_id: index for index, speaker_id in enumerate(policy.speaker_order)}
            messages.sort(key=lambda message: order.get(message.speaker_id, len(order)))
        if policy.max_speakers:
            speaker_limit = max(policy.max_speakers, len(set(required)))
            kept_speakers = []
            filtered = []
            for message in messages:
                if message.speaker_id not in kept_speakers:
                    if len(kept_speakers) >= speaker_limit:
                        continue
                    kept_speakers.append(message.speaker_id)
                filtered.append(message)
            messages = filtered
        if not messages and raw.strip():
            messages = [ChatMessage(
                message_id=new_id("msg"),
                branch_id=branch.branch_id,
                role="assistant",
                speaker_id="assistant",
                speaker_name="群聊回复",
                content=raw.strip(),
                turn_index=turn_index,
                created_at=now_text(),
            )]
            self._chat_state.consistency_warnings.append(
                "本轮回复无法匹配当前角色或发言规则，已按原文显示。"
            )
        branch.messages.extend(messages)
        self._audit_character_knowledge(messages, book)
        self._last_structured_assistant_messages = messages
        combined = "\n\n".join(f"{message.speaker_name}：{message.content}" for message in messages)
        if self._client._messages and self._client._messages[-1].get("role") == "assistant":
            self._client._messages[-1]["content"] = combined
        return messages

    def _audit_character_knowledge(self, messages: list[ChatMessage], book) -> None:
        knowledge_by_character = {
            memory.character_id: {
                item.get("fact", "")
                for item in memory.knowledge
                if item.get("awareness") != "unknown"
            }
            for memory in book.memories
        }
        all_facts = set().union(*knowledge_by_character.values()) if knowledge_by_character else set()
        for message in messages:
            own = knowledge_by_character.get(message.speaker_id, set())
            for fact in all_facts - own:
                if len(fact) >= 4 and fact in message.content:
                    warning = f"{message.speaker_name}可能使用了未知信息：{fact[:80]}"
                    if warning not in self._chat_state.consistency_warnings:
                        self._chat_state.consistency_warnings.append(warning)

    def _sync_character_book_after_chat(
        self,
        user_input: str,
        assistant_content: str,
        context: dict,
    ) -> None:
        if not context.get("participant_ids") or not assistant_content.strip():
            return
        self._stream_signals.character_book_sync_status.emit("人物书后台同步中…")
        try:
            with self._character_book_sync_lock:
                self._sync_character_book_after_chat_locked(
                    user_input, assistant_content, context
                )
        except Exception as e:
            self._stream_signals.character_book_sync_status.emit(
                f"人物书同步失败：{e}"
            )

    def _sync_character_book_after_chat_locked(
        self,
        user_input: str,
        assistant_content: str,
        context: dict,
    ) -> None:
        try:
            branch = next(
                (
                    item for item in self._chat_state.branches
                    if item.branch_id == context.get("branch_id")
                ),
                None,
            )
            if branch is None:
                self._stream_signals.character_book_sync_status.emit(
                    "人物书同步已跳过：原会话已切换。"
                )
                return
            turn_index = int(context.get("turn_index", 0))
            book = self._load_character_book()
            extraction_participants = (
                list(context.get("present_character_ids", []))
                or list(context.get("participant_ids", []))
            )
            change_set, new_events = extract_character_book_changes(
                self._usage_logged_client("character_book_update"),
                self._client.model,
                book,
                extraction_participants,
                user_input,
                assistant_content,
                context.get("timeline", []),
                turn_index,
                branch.branch_id,
                list(context.get("source_message_ids", [])),
                global_user_prompt=self._client.global_user_prompt,
                sender_name=context.get("sender_name", ""),
                sender_profile=context.get("sender_profile", ""),
            )
            applied_count = 0
            pending_count = 0
            if change_set and change_set.changes:
                low_changes = [change for change in change_set.changes if change.risk == "low"]
                high_changes = [change for change in change_set.changes if change.risk == "high"]
                if low_changes:
                    low_set = copy.deepcopy(change_set)
                    low_set.change_set_id = new_id("changeset")
                    low_set.changes = low_changes
                    apply_memory_change_set(book, low_set)
                    self._chat_state.memory_change_sets.append(low_set)
                    applied_count = len(low_changes)
                if high_changes:
                    change_set.changes = high_changes
                    self._chat_state.memory_change_sets.append(change_set)
                    pending_count = len(high_changes)
            self._character_book_manager.save(book)
            branch_timeline = list(context.get("timeline", []))
            if new_events:
                branch_timeline.extend(new_events)
            branch.timeline = timeline_to_dict(branch_timeline)
            branch.character_state_snapshot = character_book_to_dict(book)
            if self._chat_state.active_branch_id == branch.branch_id:
                self._chat_timeline = branch_timeline
            self._stream_signals.character_book_sync_status.emit(
                f"人物书同步完成：自动应用 {applied_count} 项，"
                f"待审核 {pending_count} 项，新增时间线 {len(new_events)} 条。"
            )
        except Exception:
            raise

    def _on_character_book_sync_status(self, message: str) -> None:
        self.statusBar().showMessage(message, 6000)
        if (
            message.startswith("人物书同步完成")
            and isinstance(self._client.strategy, RolePlayStrategy)
        ):
            self._sync_role_strategy()
            self._mark_conversation_dirty()

    def _on_stream_token(self, token: str) -> None:
        """主线程：接收一个 token"""
        if not self._assistant_text_buffer:
            self._assistant_char_count = 0
        self._assistant_text_buffer.append(token)
        self._assistant_char_count += len(token)
        if not (
            isinstance(self._client.strategy, RolePlayStrategy)
            and self._current_chat_type == "group"
        ):
            # WebEngine + Markdown 全量渲染成本较高，合并短时间内到达的 token。
            # 这样最多约每 80ms 刷新一次，而不是每个 token 都重排整个页面。
            if not self._stream_render_timer.isActive():
                self._stream_render_timer.start()
        # 实时显示已接收字符数
        self._stream_count_label.setText(f"⏳ 已接收 {self._assistant_char_count} 字符")
        if not self._stream_count_label.isVisible():
            self._stream_count_label.setVisible(True)

    def _flush_stream_render(self) -> None:
        """按节流频率将当前完整回复提交给 WebEngine。"""
        if not self._assistant_text_buffer:
            return
        if (
            isinstance(self._client.strategy, RolePlayStrategy)
            and self._current_chat_type == "group"
        ):
            return
        self._render_assistant_stream("".join(self._assistant_text_buffer))

    def _on_stream_finished(self) -> None:
        """主线程：流式完成（可能因取消而结束）"""
        self._stream_render_timer.stop()
        was_cancelled = self._client._cancel_requested if self._client else False
        self._stop_btn.setVisible(False)
        self._stop_btn.setEnabled(True)
        self._mode_combo.setEnabled(True)
        self._stream_count_label.setVisible(False)

        if was_cancelled:
            self._streaming = False
            self._assistant_text_buffer = []
            self._append_user_message("⏹️ 已取消")
            if self._client:
                self._client.reset_cancel()
            self._on_chapter_rendering_done()
        else:
            if isinstance(self._client.strategy, RolePlayStrategy):
                self._render_structured_conversation(
                    self._chat_state.active_branch().messages,
                    callback=self._on_chapter_rendering_done,
                )
            else:
                full_text = "".join(self._assistant_text_buffer)
                self._render_assistant_message(full_text, callback=self._on_chapter_rendering_done)

    def _on_chapter_rendering_done(self, result=None) -> None:
        """JS 渲染完成后回调 — 释放章节生成锁"""
        self._streaming = False
        self._chapter_finalized = True
        self._generate_btn.setEnabled(True)
        self._cont_generate_btn.setEnabled(True)

    def _on_stream_error(self, error_msg: str) -> None:
        """主线程：流式出错"""
        self._stream_render_timer.stop()
        self._streaming = False
        self._chapter_finalized = True
        self._generate_btn.setEnabled(True)
        self._cont_generate_btn.setEnabled(True)
        self._stop_btn.setVisible(False)
        self._stop_btn.setEnabled(True)
        self._mode_combo.setEnabled(True)
        self._stream_count_label.setVisible(False)
        if self._client:
            was_cancelled = self._client._cancel_requested
            self._client.reset_cancel()
        else:
            was_cancelled = False
        if not was_cancelled:
            QMessageBox.critical(self, "API 错误", f"调用失败：{error_msg}")

    # ========== 渲染 ==========

    def _assistant_display_name(self) -> str:
        if not isinstance(self._client.strategy, RolePlayStrategy):
            return "助手"
        if self._current_chat_type == "group":
            return "群聊"
        names = self._character_names([self._primary_character_id])
        return names[0] if names else "角色"

    def _format_roleplay_display_text(self, text: str) -> str:
        if not isinstance(self._client.strategy, RolePlayStrategy):
            return text
        names = self._character_names(self._participant_character_ids)
        labels = [*names, "旁白"]
        if not labels:
            return text
        pattern = re.compile(
            r"^(\s*)(" + "|".join(re.escape(name) for name in labels) + r")\s*[：:]\s*",
            re.MULTILINE,
        )
        return pattern.sub(lambda m: f"{m.group(1)}**{m.group(2)}：** ", text)

    def _append_user_message(self, text: str) -> None:
        """追加用户消息到显示区域"""
        escaped = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        escaped = escaped.replace("\n", "<br>")
        js_safe = self._escape_for_js(escaped)
        script = f"""
            (function() {{
                var previousReply = document.getElementById('stream-container');
                if (previousReply) {{
                    previousReply.removeAttribute('id');
                }}
                var div = document.createElement('div');
                div.className = 'user-msg';
                div.innerHTML = '<strong>🧑 {self._escape_for_js(self._sender_name or "你")}：</strong><br>' + `{js_safe}`;
                document.body.appendChild(div);
                window.scrollTo(0, document.body.scrollHeight);
            }})();
        """
        self._display.page().runJavaScript(script)

    def _render_assistant_stream(self, text: str) -> None:
        """流式渲染 Markdown"""
        text = self._format_roleplay_display_text(text)
        html_body = md_lib.markdown(
            text,
            extensions=["fenced_code", "tables", "codehilite", "nl2br", "sane_lists"],
        )
        escaped_body = self._escape_for_js(html_body)

        script = f"""
            (function() {{
                var container = document.getElementById('stream-container');
                var isNewReply = !container;
                if (!container) {{
                    container = document.createElement('div');
                    container.id = 'stream-container';
                    container.className = 'assistant-msg';
                    document.body.appendChild(container);
                }}
                container.innerHTML = '<strong>💬 {self._escape_for_js(self._assistant_display_name())}：</strong><br>' + `{escaped_body}`;
                if (isNewReply) {{
                    {REPLY_JUMP_NAV_SCRIPT}
                    rebuildReplyJumpNav();
                }}
                window.scrollTo(0, document.body.scrollHeight);
            }})();
        """
        self._display.page().runJavaScript(script)

    def _render_assistant_message(self, text: str, callback=None) -> None:
        """最终渲染（含可选 JS 完成回调）"""
        text = self._format_roleplay_display_text(text)
        html_body = md_lib.markdown(
            text,
            extensions=["fenced_code", "tables", "codehilite", "nl2br", "sane_lists"],
        )
        escaped_body = self._escape_for_js(html_body)

        script = f"""
            (function() {{
                var old = document.getElementById('stream-container');
                var isNewReply = !old;
                if (!old) {{
                    old = document.createElement('div');
                    old.id = 'stream-container';
                    old.className = 'assistant-msg';
                    document.body.appendChild(old);
                }}
                old.innerHTML = '<strong>💬 {self._escape_for_js(self._assistant_display_name())}：</strong><br>' + `{escaped_body}`;
                if (isNewReply) {{
                    {REPLY_JUMP_NAV_SCRIPT}
                    rebuildReplyJumpNav();
                }}
                window.scrollTo(0, document.body.scrollHeight);
            }})();
        """
        self._display.page().runJavaScript(script, callback)

    def _render_structured_conversation(self, messages: list[ChatMessage], callback=None) -> None:
        body_parts = []
        for message in messages[-100:]:
            content = message.content or ""
            if message.action:
                content = f"*{message.action}*\n\n{content}"
            html_body = md_lib.markdown(
                content,
                extensions=["fenced_code", "tables", "codehilite", "nl2br", "sane_lists"],
            )
            speaker = (
                (message.speaker_name or ("你" if message.role == "user" else "角色"))
                .replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
            )
            css_class = "user-msg" if message.role == "user" else "system-msg" if message.speaker_id == "narrator" else "assistant-msg"
            icon = "🧑" if message.role == "user" else "📖" if message.speaker_id == "narrator" else "💬"
            body_parts.append(
                f'<div class="{css_class}" data-message-id="{message.message_id}">'
                f"<strong>{icon} {speaker}：</strong><br>{html_body}</div>"
            )
        full_html = (
            f"<html><head>{CURRENT_HTML_STYLE}</head><body>{''.join(body_parts)}"
            f"<script>{REPLY_JUMP_NAV_SCRIPT}rebuildReplyJumpNav();</script></body></html>"
        )
        if callback:
            def on_loaded(ok):
                try:
                    self._display.loadFinished.disconnect(on_loaded)
                except (TypeError, RuntimeError):
                    pass
                callback(ok)

            self._display.loadFinished.connect(on_loaded)
        self._display.setHtml(full_html)

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
            info_text = f"暂无章节，下一章编号: 第{self._novel_manager.get_active_generation_target(title)['chapter_num']}章"
        else:
            lines = [f"已有 {len(chapters)} 章，下一章: 第{self._novel_manager.get_active_generation_target(title)['chapter_num']}章"]
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
        try:
            if hasattr(self, "_chapter_tree_status") and title:
                nodes = self._novel_manager.get_active_path_nodes(title)
                if nodes:
                    path_text = " → ".join(
                        f"第{n.get('chapter_num')}章v{n.get('version')}" for n in nodes
                    )
                    self._chapter_tree_status.setText(f"活跃路径：{path_text}")
                else:
                    self._chapter_tree_status.setText("活跃路径：第零章（尚未选择正文分支）")
        except Exception:
            if hasattr(self, "_chapter_tree_status"):
                self._chapter_tree_status.setText("活跃路径：读取失败")
        self._refresh_top_status()

    # ========== 📖 世界书对话框 ==========

    def _on_world_bible(self) -> None:
        """打开世界书编辑对话框"""
        title = self._get_current_book_title()
        if not title:
            QMessageBox.warning(self, "提示", "请先选择或创建一本小说。")
            return
        bible = self._novel_manager.load_world_bible(title)
        load_error = self._novel_manager.world_bible_load_error(title)
        if load_error:
            QMessageBox.critical(self, "世界书加载失败", load_error)
            return
        try:
            active_chapters = {
                int(node.get("chapter_num", 0) or 0)
                for node in self._novel_manager.get_active_path_nodes(title)
                if int(node.get("chapter_num", 0) or 0) > 0
            }
        except Exception:
            active_chapters = set()
        dlg = WorldBibleDialog(self, bible, active_chapters=active_chapters)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            try:
                self._novel_manager.save_world_bible(title, dlg.get_bible())
                QMessageBox.information(self, "提示", "世界书已保存。")
            except Exception as exc:
                QMessageBox.critical(self, "世界书保存失败", str(exc))
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

        try:
            self._do_analyze_continuation()
        except Exception as e:
            import traceback
            self._streaming = False
            self._stop_btn.setVisible(False)
            self._stop_btn.setEnabled(True)
            self._mode_combo.setEnabled(True)
            self._append_user_message(f"❌ 分析异常: {e}")
            QMessageBox.critical(self, "分析异常", f"分析过程出现意外错误:\n{e}\n\n详细信息见聊天记录。")
            self._stream_signals.token.emit(f"\n❌ 分析异常: {e}\n")
            self._stream_signals.token.emit(f"\n```\n{traceback.format_exc()}\n```\n")

    def _do_analyze_continuation(self) -> None:
        """_on_analyze_continuation 的实际逻辑，外层有 try/except 保护"""
        source_text = self._read_continuation_source()
        if not source_text:
            QMessageBox.warning(self, "提示", "请先选择续写源文档或文件夹。")
            return

        client = self._usage_logged_client("continuation_segment") if hasattr(self, '_client') else None
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
            self._client.reset_cancel()
            self._stop_btn.setVisible(True)
            self._stop_btn.setEnabled(True)
            self._stop_btn.setText("⏹")
            self._mode_combo.setEnabled(False)
            self._streaming = True
            self._streaming_start_time = time.time()
            self._assistant_text_buffer = []
            self._append_user_message(f"📂 批量导入章节 → {title}")
            self._cont_analysis_source = source_text
            self._cont_analysis_source_path = source_folder
            threading.Thread(
                target=self._run_batch_folder_import,
                args=(title, source_folder, self._client.model, self._usage_logged_client("batch_import_analysis")),
                kwargs={"files_list": result["files"], "xp_mode": self._cont_xp_mode_check.isChecked()},
                daemon=True,
            ).start()
            return

        # 文件模式：使用确认后的段落直接进入分析
        sections = result.get("sections", [])
        if not sections:
            QMessageBox.warning(self, "提示", "段落列表为空，无法分析。")
            return

        model = self._client.model
        self._client.reset_cancel()
        self._stop_btn.setVisible(True)
        self._stop_btn.setEnabled(True)
        self._stop_btn.setText("⏹")
        self._mode_combo.setEnabled(False)
        self._streaming = True
        self._streaming_start_time = time.time()
        self._assistant_text_buffer = []
        self._append_user_message(f"🔍 分析源文档并导入小说：{title}")

        self._cont_analysis_source = source_text
        self._cont_analysis_source_path = source_file or source_folder or ""

        self._start_analysis_with_sections(
            title, source_text, sections, self._usage_logged_client("continuation_import_analysis")
        )

    def _start_analysis_with_sections(self, title: str, source_text: str, sections: list, client) -> None:
        """后台线程：单篇导入按确认分段保存为章节，并逐章绑定摘要与世界书来源。"""
        xp_mode = self._cont_xp_mode_check.isChecked()

        def _run():
            try:
                from dataclasses import asdict
                from utils.summarize import generate_novel_settings_from_world_bible
                from core.world_bible import WorldBible, extract_and_merge_world_bible

                si = self._stream_signals
                _global_prompt = self._client.global_user_prompt

                si.token.emit(f"\n✅ 已确认 {len(sections)} 个段落\n")
                si.token.emit("\n⏳ 第一步：创建小说，并按确认分段保存为章节节点…\n")
                self._novel_manager.create_book(title)
                world_bible = WorldBible()
                meta = self._novel_manager.load_meta(title)
                for idx, (section_title, content) in enumerate(sections, 1):
                    if self._client._cancel_requested:
                        si.token.emit(f"\n⏹️ 已取消（已处理 {idx - 1}/{len(sections)} 个章节节点）\n")
                        si.finished.emit()
                        return
                    chapter_num = idx
                    chapter_title = (section_title or f"导入段落 {idx}").strip()
                    chapter_content = (content or "").strip()
                    if not chapter_content:
                        continue
                    _, saved_version = self._novel_manager.save_chapter_version(
                        title, chapter_num, chapter_title, chapter_content,
                    )
                    si.token.emit(f"  📖 [{idx}/{len(sections)}] 已保存第{chapter_num}章「{chapter_title}」v{saved_version}\n")

                    story_context = ""
                    try:
                        current_summary = self._novel_manager.load_smart_summary(
                            title, client, next_chapter_num=chapter_num,
                            model=self._client.model, global_user_prompt=_global_prompt,
                        )
                        if current_summary and "故事刚刚开始" not in current_summary:
                            story_context = current_summary
                    except Exception:
                        pass

                    summary_text = self._novel_manager.generate_summary(
                        client, chapter_content, chapter_num, chapter_title,
                        model=self._client.model,
                        global_user_prompt=_global_prompt,
                        xp_mode=xp_mode,
                    )
                    if summary_text.strip():
                        self._novel_manager.set_chapter_node_summary(
                            title, chapter_num, saved_version, summary_text
                        )
                    self._novel_manager.rebuild_plot_summary_from_tree(title)

                    world_bible = extract_and_merge_world_bible(
                        client, chapter_content, chapter_num, world_bible,
                        self._client.model,
                        chapter_version=saved_version,
                        global_user_prompt=_global_prompt,
                        story_context=story_context,
                        background_story=meta.background_story,
                        protagonist_bio=meta.protagonist_bio,
                        writing_demand=meta.writing_demand,
                        xp_mode=xp_mode,
                    )
                    self._novel_manager.save_world_bible(title, world_bible)
                    si.token.emit("    ✅ 摘要已绑定章节树，世界书来源已绑定到本章\n")

                chars = len(world_bible.characters)
                locs = len(world_bible.locations)
                rules = len(world_bible.rules)
                wb_count = len(world_bible.key_worldbuilding_passages)
                fs_count = len(world_bible.global_foreshadowing)
                si.token.emit(
                    f"\n  ✅ 累积世界书: {chars}角色 / {locs}地点 / {rules}规则"
                    + (f" / {wb_count}关键设定 / {fs_count}伏笔" if wb_count or fs_count else "")
                    + "\n"
                )

                si.token.emit("\n⏳ 第二步：从世界书生成小说设定…\n")
                world_data = {
                    "characters": [asdict(c) for c in world_bible.characters],
                    "locations": [asdict(l) for l in world_bible.locations],
                    "rules": list(world_bible.rules),
                    "plot_threads": [asdict(p) for p in world_bible.active_plot_threads],
                    "timeline": [asdict(t) for t in world_bible.timeline],
                    "key_worldbuilding": list(world_bible.key_worldbuilding_passages),
                    "global_foreshadowing": list(world_bible.global_foreshadowing),
                    "global_key_dialogues": list(world_bible.global_key_dialogues),
                }
                settings = generate_novel_settings_from_world_bible(
                    client,
                    world_data,
                    self._client.model,
                    global_user_prompt=_global_prompt,
                    xp_mode=xp_mode,
                )
                self._novel_manager.save_meta(
                    title,
                    protagonist_bio=settings.get("protagonist_bio", ""),
                    background_story=settings.get("background_story", ""),
                    writing_demand=settings.get("writing_demand", ""),
                    author_plan=settings.get("author_plan", ""),
                    xp_mode=xp_mode,
                )

                si.token.emit(f"  ✅ 设定已保存\n")
                si.token.emit(
                    f"\n{'='*50}\n"
                    f"✅ 分析完成！「{title}」创建成功\n"
                    f"  • 单篇原文已按 {len(sections)} 个确认分段保存为章节节点\n"
                    f"  • 每个章节节点已绑定摘要，可在章节树管理中查看\n"
                    f"  • 世界书来源已按对应章节号和版本绑定\n"
                    f"  • 世界书 {chars}角色 + {locs}地点 + {rules}规则\n"
                    f"  • 小说设定已生成并加载到面板\n"
                    f"  • 现在可以从第{self._novel_manager.get_active_generation_target(title)['chapter_num']}章开始续写\n"
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
                                  files_list: list | None = None,
                                  xp_mode: bool = False) -> None:
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
                if self._client._cancel_requested:
                    si.token.emit(f"\n⏹️ 批量导入已取消（已处理 {idx-1}/{total} 章）\n")
                    break

                chapter_title = os.path.splitext(fname)[0]
                si.token.emit(f"  📖 [{idx}/{total}] 第{chapter_num}章「{chapter_title}」…\n")

                # 加载前文摘要（基于章节树活跃路径节点摘要）
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

                # 先保存章节文件，拿到实际版本号，世界书来源才能绑定到章节版本
                _, saved_version = self._novel_manager.save_chapter_version(
                    title, chapter_num, chapter_title, content,
                )

                # 提取并合并世界书（带上全局上下文）
                world_bible = extract_and_merge_world_bible(
                    client, content, chapter_num, world_bible,
                    model, chapter_version=saved_version, global_user_prompt=_global_prompt,
                    story_context=story_context,
                    background_story=meta.background_story,
                    protagonist_bio=meta.protagonist_bio,
                    writing_demand=meta.writing_demand,
                    xp_mode=xp_mode,
                )
                self._novel_manager.save_world_bible(title, world_bible)
                if getattr(world_bible, "consistency_warnings", None):
                    si.token.emit(f"    ⚠️ 世界书一致性提醒：{len(world_bible.consistency_warnings)} 条\n")

                # 生成章节摘要
                summary_text = self._novel_manager.generate_summary(
                    client, content, chapter_num, chapter_title,
                    model=model, global_user_prompt=_global_prompt,
                    xp_mode=xp_mode,
                )
                if summary_text.strip():
                    self._novel_manager.set_chapter_node_summary(title, chapter_num, saved_version, summary_text)
                self._novel_manager.rebuild_plot_summary_from_tree(title)

                si.token.emit(f"    ✅ 世界书已更新 | 摘要已绑定章节树\n")

            if self._client._cancel_requested:
                si.token.emit(f"\n⏹️ 已取消（已处理 {total} 章中的部分章节）\n")
                self._stream_signals.novel_imported.emit(title)
                return

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
                xp_mode=xp_mode,
            )
            self._novel_manager.save_meta(
                title,
                protagonist_bio=settings.get("protagonist_bio", ""),
                background_story=settings.get("background_story", ""),
                writing_demand=settings.get("writing_demand", ""),
                author_plan=settings.get("author_plan", ""),
                xp_mode=xp_mode,
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
        else:
            self._stream_signals.finished.emit()

    def _show_analysis_dialog(self, world_data_str: str, settings_str: str, title: str) -> None:
        """在主线程显示分析结果对话框"""
        self._streaming = False
        self._stop_btn.setVisible(False)
        self._stop_btn.setEnabled(True)
        self._mode_combo.setEnabled(True)
        if self._client:
            self._client.reset_cancel()
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
        client = self._usage_logged_client("continuation_suggest") if hasattr(self, '_client') else None
        if client is None:
            return

        self._client.reset_cancel()
        self._stop_btn.setVisible(True)
        self._stop_btn.setEnabled(True)
        self._stop_btn.setText("⏹")
        self._mode_combo.setEnabled(False)
        self._streaming = True
        self._streaming_start_time = time.time()
        self._assistant_text_buffer = []
        self._append_user_message("🎲 AI 建议发展方向")
        xp_mode = False
        title = self._get_current_book_title()
        if title:
            try:
                xp_mode = bool(self._novel_manager.load_meta(title).xp_mode)
            except Exception:
                xp_mode = self._cont_xp_mode_check.isChecked()
        else:
            xp_mode = self._cont_xp_mode_check.isChecked()

        def _run():
            try:
                if self._client._cancel_requested:
                    self._stream_signals.finished.emit()
                    return
                self._stream_signals.token.emit("\n\n🎲 AI 正在分析发展方向...\n\n")
                directions = suggest_directions(client, setting, plot_outline,
                                                self._client.model, world_data,
                                                global_user_prompt=self._client.global_user_prompt,
                                                xp_mode=xp_mode)
                self._stream_signals.finished.emit()
                self._stream_signals.directions_ready.emit(directions, setting, plot_outline, word_count)
            except Exception as e:
                self._stream_signals.error.emit(f"方向建议失败: {e}")

        threading.Thread(target=_run, daemon=True).start()

    def _show_direction_selector(self, directions: list, setting: str, plot_outline: str, word_count: int):
        """在主线程显示方向选择对话框"""
        self._streaming = False
        self._stop_btn.setVisible(False)
        self._stop_btn.setEnabled(True)
        self._mode_combo.setEnabled(True)
        if self._client:
            self._client.reset_cancel()
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

        generation_target = self._novel_manager.get_active_generation_target(book_title)
        chapter_num = int(generation_target["chapter_num"])
        chapter_title = self._cont_chapter_title_edit.text().strip()
        if not chapter_title:
            chapter_title = f"续写 (第{chapter_num}章)"
        requirement = self._continue_requirement.toPlainText().strip()
        if not plot:
            plot = self._continue_plot.toPlainText().strip()
        word_count = word_count or self._continue_word_count.value()

        self._client.reset_cancel()
        self._stop_btn.setVisible(True)
        self._stop_btn.setEnabled(True)
        self._stop_btn.setText("⏹")
        self._mode_combo.setEnabled(False)
        self._streaming = True
        self._streaming_start_time = time.time()
        self._assistant_text_buffer = []
        self._append_user_message(f"📝 续写第{chapter_num}章：{chapter_title}")

        threading.Thread(
            target=self._run_continuation,
            args=(
                book_title, chapter_num, chapter_title, source_text,
                requirement, word_count, plot, setting, generation_target,
            ),
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
            _rebuild_done_signal = pyqtSignal()
            _rebuild_error_signal = pyqtSignal(str)

            def __init__(self, parent, novel_mgr, book_title, client):
                super().__init__(parent)
                self._novel_mgr = novel_mgr
                self._book_title = book_title
                self._client = client
                self._generating = False
                self._rebuild_success_message = "剧情记忆和世界书已按活跃路径同步。"
                self.setWindowTitle(f"章节管理 - {book_title}")
                self.resize(500, 400)
                self.setModal(True)

                self._regenerate_done_signal.connect(self._on_regenerate_done)
                self._regenerate_error_signal.connect(self._on_regenerate_error)
                self._rebuild_done_signal.connect(self._on_rebuild_done)
                self._rebuild_error_signal.connect(self._on_rebuild_error)

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

                current_wb_btn = QPushButton("重提当前章节世界书")
                current_wb_btn.clicked.connect(self._on_force_extract_current_world_bible)
                btn_row.addWidget(current_wb_btn)

                all_wb_btn = QPushButton("重提全部路径世界书")
                all_wb_btn.clicked.connect(self._on_force_extract_all_world_bible)
                btn_row.addWidget(all_wb_btn)

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
                self._load_chapters()
                parent = self.parent()
                if hasattr(parent, "_refresh_chapter_info_display"):
                    parent._refresh_chapter_info_display(self._book_title)

                self._rebuild_success_message = (
                    f"第{data['chapter_num']}章 v{data['version']} 已设为活跃版本，"
                    "剧情记忆和世界书已同步。"
                )
                self._close_btn.setText("⏳ 同步记忆中...")
                self._close_btn.setEnabled(False)
                threading.Thread(target=self._do_rebuild_summary, daemon=True).start()

            def _do_rebuild_summary(self):
                try:
                    self._novel_mgr.rebuild_plot_summary_from_tree(self._book_title)
                    self._novel_mgr.rebuild_world_bible_from_active(
                        self._client.raw_client, self._book_title,
                        model=self._client.model,
                        global_user_prompt=self._client.global_user_prompt,
                    )
                    self._rebuild_done_signal.emit()
                except Exception as e:
                    self._rebuild_error_signal.emit(str(e))

            def _on_rebuild_done(self):
                QMessageBox.information(self, "成功", self._rebuild_success_message)
                self._close_btn.setText("关闭")
                self._close_btn.setEnabled(True)
                self._load_chapters()
                parent = self.parent()
                if hasattr(parent, "_refresh_chapter_info_display"):
                    parent._refresh_chapter_info_display(self._book_title)

            def _on_rebuild_error(self, error_str: str):
                QMessageBox.warning(self, "同步失败", error_str)
                self._close_btn.setText("关闭")
                self._close_btn.setEnabled(True)

            def _on_force_extract_all_world_bible(self):
                reply = QMessageBox.question(
                    self,
                    "提取全部活跃路径世界书",
                    "将读取当前活跃路径上的全部章节正文，重新调用模型提取世界书。\n"
                    "这个操作用于修复或刷新世界书，耗时和消耗会高于普通同步。\n\n继续吗？",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if reply != QMessageBox.StandardButton.Yes:
                    return
                self._rebuild_success_message = "世界书已从全部活跃路径正文重新提取。"
                self._close_btn.setText("⏳ 重提世界书中...")
                self._close_btn.setEnabled(False)
                threading.Thread(target=self._do_force_extract_all_world_bible, daemon=True).start()

            def _do_force_extract_all_world_bible(self):
                try:
                    self._novel_mgr.rebuild_world_bible_from_active(
                        self._client.raw_client,
                        self._book_title,
                        model=self._client.model,
                        global_user_prompt=self._client.global_user_prompt,
                        force_extract=True,
                    )
                    self._rebuild_done_signal.emit()
                except Exception as e:
                    self._rebuild_error_signal.emit(str(e))

            def _on_force_extract_current_world_bible(self):
                data = self._get_selected_data()
                if not data:
                    return
                reply = QMessageBox.question(
                    self,
                    "提取当前章节世界书",
                    f"只重新提取第{data['chapter_num']}章 v{data['version']} 的世界书快照，"
                    "然后按活跃路径重新合并世界书。\n\n继续吗？",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if reply != QMessageBox.StandardButton.Yes:
                    return
                node_id = self._novel_mgr._node_id(data["chapter_num"], data["version"])
                self._rebuild_success_message = (
                    f"第{data['chapter_num']}章 v{data['version']} 世界书快照已刷新。"
                )
                self._close_btn.setText("⏳ 重提当前章节中...")
                self._close_btn.setEnabled(False)
                threading.Thread(
                    target=self._do_force_extract_current_world_bible,
                    args=(node_id,),
                    daemon=True,
                ).start()

            def _do_force_extract_current_world_bible(self, node_id: str):
                try:
                    self._novel_mgr.extract_world_bible_for_node(
                        self._client.raw_client,
                        self._book_title,
                        node_id,
                        model=self._client.model,
                        global_user_prompt=self._client.global_user_prompt,
                    )
                    self._rebuild_done_signal.emit()
                except Exception as e:
                    self._rebuild_error_signal.emit(str(e))

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
                if hasattr(parent, "_client"):
                    parent._chapter_finalized = False
                    parent._generate_btn.setEnabled(False)
                    parent._cont_generate_btn.setEnabled(False)
                    parent._client.reset_cancel()
                    parent._stop_btn.setVisible(True)
                    parent._stop_btn.setEnabled(True)
                    parent._stop_btn.setText("⏹")
                    parent._mode_combo.setEnabled(False)
                    parent._streaming = True
                    parent._streaming_start_time = time.time()
                    parent._assistant_text_buffer = []
                    parent._append_user_message(
                        f"🔁 重写第 {data['chapter_num']} 章「{data.get('title', '')}」"
                    )
                _bg = parent._background_edit.toPlainText().strip()
                _bio = parent._protagonist_edit.toPlainText().strip()
                _demand = parent._demand_edit.toPlainText().strip()
                _global_prompt = self._client.global_user_prompt
                _xp_mode = bool(self._novel_mgr.load_meta(self._book_title).xp_mode)

                threading.Thread(target=self._do_regenerate, args=(data, _bg, _bio, _demand, _global_prompt, _xp_mode), daemon=True).start()

            def _do_regenerate(self, data: dict, bg: str, bio: str, demand: str, global_prompt: str = "", xp_mode: bool = False) -> None:
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

                    from utils.prompts import Prompts
                    target_words = parent._chapter_word_count.value()

                    messages = [
                        {"role": "system", "content": Prompts.NOVEL_CHAPTER_WRITING},
                    ]
                    if xp_mode:
                        messages.append({"role": "system", "content": Prompts.XP_MODE_SYSTEM})
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
                            from core.world_bible import format_relevant_world_bible_for_prompt
                            wb_text = format_relevant_world_bible_for_prompt(
                                bible,
                                f"{chapter_title}\n{req}\n{plot}",
                                active_chapters={
                                    int(node.get("chapter_num", 0) or 0)
                                    for node in self._novel_mgr.get_active_path_nodes(self._book_title)
                                },
                                target_chapter=chapter_num,
                                token_budget=4000,
                            )
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
                    try:
                        contract = self._novel_mgr.build_continuity_contract(
                            self._book_title, chapter_num, chapter_title, plot
                        )
                        if contract:
                            user_parts.append(f"{contract}\n")
                    except Exception:
                        pass
                    try:
                        author_plan = self._novel_mgr.build_author_planning_prompt(self._book_title)
                        if author_plan:
                            user_parts.append(f"{author_plan}\n")
                    except Exception:
                        pass
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
                    if xp_mode:
                        user_parts.append(f"{Prompts.XP_MODE_SYSTEM}\n")
                    user_parts.append(f"请直接输出第 {chapter_num} 章正文。字数不少于{target_words}字，通过丰富环境细节、增加对话交互和内心描写来充实内容。")
                    messages.append({"role": "user", "content": "\n".join(user_parts)})

                    prompt_text = messages[-1].get("content", "")
                    content, generation_stats, cancelled = parent._stream_chapter_completion(
                        operation="chapter_regenerate",
                        messages=messages,
                        prompt_text=prompt_text,
                        max_tokens=max(target_words * 2, self._client.max_tokens),
                    )
                    if cancelled:
                        parent._stream_signals.token.emit("\n\n⏹️ 已取消\n")
                        parent._stream_signals.finished.emit()
                        self._regenerate_error_signal.emit("已取消生成。")
                        return

                    content, supervision_report = parent._supervise_chapter_content(
                        chapter_num=chapter_num,
                        chapter_title=chapter_title,
                        content=content,
                        chapter_outline=plot,
                        requirements=req,
                        target_words=target_words,
                        context=prompt_text,
                        xp_mode=xp_mode,
                        operation_prefix="chapter_regenerate",
                    )

                    new_version = self._novel_mgr.get_next_version(self._book_title, chapter_num)
                    file_path, saved_version = self._novel_mgr.save_chapter_version(
                        self._book_title, chapter_num, chapter_title, content,
                        version=new_version,
                    )
                    self._novel_mgr.save_generation_record(
                        title=self._book_title,
                        chapter_num=chapter_num,
                        chapter_title=chapter_title,
                        version=saved_version,
                        prompt=prompt_text,
                        model=self._client.model,
                        temperature=self._client.temperature,
                        top_p=self._client.top_p,
                        max_tokens=self._client.max_tokens,
                        frequency_penalty=self._client.frequency_penalty,
                        content_preview=content.replace("\n", " "),
                        requirement=req,
                        plot=plot,
                        supervision_report=supervision_report,
                    )


                    summary = self._novel_mgr.generate_summary(
                        self._client.raw_client,
                        content,
                        chapter_num,
                        chapter_title,
                        model=self._client.model,
                        global_user_prompt=global_prompt,
                        xp_mode=xp_mode,
                    )
                    if summary.strip():
                        self._novel_mgr.set_chapter_node_summary(
                            self._book_title, chapter_num, saved_version, summary
                        )
                    self._novel_mgr.rebuild_plot_summary_from_tree(self._book_title)

                    # 更新世界书
                    try:
                        from core.world_bible import extract_and_merge_world_bible
                        bible = self._novel_mgr.load_world_bible(self._book_title)
                        updated_bible = extract_and_merge_world_bible(
                            self._client.raw_client, content, chapter_num, bible,
                            self._client.model,
                            chapter_version=saved_version,
                            global_user_prompt=self._client.global_user_prompt,
                            xp_mode=xp_mode,
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
                if "已取消" in error_str:
                    QMessageBox.information(self, "已取消", error_str)
                else:
                    QMessageBox.critical(self, "重新生成失败", f"API 调用出错：{error_str}")
                self._generating = False
                self._close_btn.setText("关闭")
                self._close_btn.setEnabled(True)

            def _on_delete_version(self):
                data = self._get_selected_data()
                if not data:
                    return
                warning = ""
                if data["is_active"]:
                    warning = "\n\n⚠ 这是活跃版本，删除后将自动切换到该章节的最新版本。"
                reply = QMessageBox.question(
                    self, "确认删除",
                    f"确定要删除第{data['chapter_num']}章 v{data['version']}？\n此操作不可恢复！{warning}",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                )
                if reply == QMessageBox.StandardButton.Yes:
                    self._novel_mgr.delete_chapter_version(
                        self._book_title, data["chapter_num"], data["version"]
                    )
                    self._load_chapters()
                    parent = self.parent()
                    if hasattr(parent, "_refresh_chapter_info_display"):
                        parent._refresh_chapter_info_display(self._book_title)
                    self._rebuild_success_message = "版本已删除，剧情记忆和世界书已按剩余活跃路径同步。"
                    self._close_btn.setText("⏳ 同步记忆中...")
                    self._close_btn.setEnabled(False)
                    threading.Thread(target=self._do_rebuild_summary, daemon=True).start()

        dialog = ChapterTreeDialog(self, self._novel_manager, title, self._client)
        dialog.exec()
        self._refresh_chapter_info_display(title)

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

    def _on_save_conversation(self) -> bool:
        """保存当前对话到历史记录"""
        messages = self._client.export_messages()
        if isinstance(self._client.strategy, RolePlayStrategy):
            branch = self._chat_state.active_branch()
            if branch.messages:
                messages = structured_to_legacy_messages(
                    branch.messages, self._client.strategy.get_system_prompt()
                )
        # 过滤掉 system prompt，只统计用户和助手的消息
        user_assistant = [m for m in messages if m.get("role") in ("user", "assistant")]
        if not user_assistant:
            QMessageBox.warning(self, "提示", "当前没有对话内容，无法保存。")
            return False

        # 弹出对话框获取标题
        title, ok = QInputDialog.getText(
            self,
            "保存对话",
            "请输入对话标题：",
            text=self._current_conversation_title or ""
        )
        if not ok or not title.strip():
            return False

        title = title.strip()
        if self._current_conversation_id:
            # 已有对话：让用户选择更新还是另存为新
            old_title = self._current_conversation_title or "未命名"
            msg_box = QMessageBox(self)
            msg_box.setWindowTitle("保存对话")
            msg_box.setText(f"当前已绑定对话「{old_title}」")
            msg_box.setInformativeText("更新已有对话，还是另存为新对话？")
            btn_update = msg_box.addButton("更新已有", QMessageBox.ButtonRole.AcceptRole)
            btn_new = msg_box.addButton("另存为新", QMessageBox.ButtonRole.ActionRole)
            btn_cancel = msg_box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
            msg_box.setDefaultButton(btn_update)
            msg_box.exec()
            clicked = msg_box.clickedButton()
            if clicked == btn_cancel or clicked is None:
                return False
            if clicked == btn_new:
                conversation_id = self._conversation_manager.generate_id(title)
            else:
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
            self._sync_role_strategy()

        file_path = self._conversation_manager.save_conversation(
            conversation_id=conversation_id,
            title=title,
            model=self._client.model,
            messages=messages,
            character_description=char_desc,
            story_background=story_bg,
            strategy=strategy_name,
            reply_mode=reply_mode,
            chat_type=self._current_chat_type if isinstance(self._client.strategy, RolePlayStrategy) else "",
            participant_character_ids=list(self._participant_character_ids) if isinstance(self._client.strategy, RolePlayStrategy) else [],
            primary_character_id=self._primary_character_id if isinstance(self._client.strategy, RolePlayStrategy) else "",
            timeline_id=conversation_id if isinstance(self._client.strategy, RolePlayStrategy) else "",
            timeline=timeline_to_dict(self._chat_timeline) if isinstance(self._client.strategy, RolePlayStrategy) else [],
            character_book_snapshot=character_book_to_dict(self._load_character_book()) if isinstance(self._client.strategy, RolePlayStrategy) else {},
            sender_name=self._sender_name if isinstance(self._client.strategy, RolePlayStrategy) else "",
            sender_profile=self._sender_profile if isinstance(self._client.strategy, RolePlayStrategy) else "",
            required_responder_ids=list(self._required_responder_ids) if isinstance(self._client.strategy, RolePlayStrategy) else [],
            structured_messages=[
                asdict(message) for message in self._chat_state.active_branch().messages
            ] if isinstance(self._client.strategy, RolePlayStrategy) else [],
            branches=state_to_dict(self._chat_state).get("branches", []) if isinstance(self._client.strategy, RolePlayStrategy) else [],
            active_branch_id=self._chat_state.active_branch_id if isinstance(self._client.strategy, RolePlayStrategy) else "main",
            sender_profile_id=self._chat_state.sender_profile_id if isinstance(self._client.strategy, RolePlayStrategy) else "",
            scene_state=state_to_dict(self._chat_state).get("scene_state", {}) if isinstance(self._client.strategy, RolePlayStrategy) else {},
            turn_policy=state_to_dict(self._chat_state).get("turn_policy", {}) if isinstance(self._client.strategy, RolePlayStrategy) else {},
            memory_change_sets=state_to_dict(self._chat_state).get("memory_change_sets", []) if isinstance(self._client.strategy, RolePlayStrategy) else [],
            narrator_enabled=self._chat_state.narrator_enabled if isinstance(self._client.strategy, RolePlayStrategy) else False,
            schema_version=4,
        )
        self._current_conversation_id = conversation_id
        self._current_conversation_title = title
        self._conversation_dirty = False
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
        return True

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

        if isinstance(self._client.strategy, RolePlayStrategy):
            state_payload = {
                "branches": record.get("branches", []),
                "active_branch_id": record.get("active_branch_id", "main"),
                "sender_profile_id": record.get("sender_profile_id", ""),
                "scene_state": record.get("scene_state", {}),
                "turn_policy": record.get("turn_policy", {}),
                "memory_change_sets": record.get("memory_change_sets", []),
                "narrator_enabled": record.get("narrator_enabled", False),
            }
            self._chat_state = state_from_dict(state_payload)
            self._current_chat_type = record.get("chat_type") or "private"
            self._participant_character_ids = list(record.get("participant_character_ids") or [])
            self._primary_character_id = record.get("primary_character_id") or (
                self._participant_character_ids[0] if self._participant_character_ids else ""
            )
            self._chat_timeline = dict_to_timeline(record.get("timeline", []))
            self._sender_name = record.get("sender_name") or "你"
            self._sender_profile = record.get("sender_profile") or ""
            self._required_responder_ids = list(record.get("required_responder_ids") or [])
            # Legacy conversation migration: create a reusable profile from old role text.
            if not self._participant_character_ids:
                legacy_name = (record.get("title") or "旧角色").strip()
                legacy_desc = record.get("character_description", "") or ""
                legacy_bg = record.get("story_background", "") or ""
                if legacy_desc or legacy_bg:
                    profile = CharacterProfile(
                        name=legacy_name[:40],
                        personality=legacy_desc,
                        background=legacy_bg,
                    )
                    profile = self._character_book_manager.create_profile(profile)
                    self._participant_character_ids = [profile.character_id]
                    self._primary_character_id = profile.character_id
                    self._current_chat_type = "private"
            if not self._required_responder_ids:
                self._required_responder_ids = (
                    list(self._participant_character_ids)
                    if self._current_chat_type == "group"
                    else list(self._participant_character_ids[:1])
                )
            branch = self._chat_state.active_branch()
            if not branch.messages:
                structured = record.get("structured_messages", [])
                if structured:
                    branch.messages = [
                        ChatMessage(**{
                            key: value for key, value in item.items()
                            if key in ChatMessage.__dataclass_fields__
                        })
                        for item in structured
                    ]
                else:
                    assistant_name = (
                        self._character_names([self._primary_character_id])[0]
                        if self._primary_character_id and self._character_names([self._primary_character_id])
                        else "角色"
                    )
                    branch.messages = legacy_messages_to_structured(
                        messages,
                        branch_id=branch.branch_id,
                        sender_name=self._sender_name,
                        assistant_name=assistant_name,
                        name_to_id={
                            profile.name: profile.character_id
                            for profile in self._load_character_book().profiles
                            if profile.character_id in self._participant_character_ids
                        },
                    )
                branch.timeline = timeline_to_dict(self._chat_timeline)
                branch.character_state_snapshot = record.get("character_book_snapshot", {})
            self._sender_name_edit.blockSignals(True)
            self._sender_profile_edit.blockSignals(True)
            self._sender_name_edit.setText(self._sender_name)
            self._sender_profile_edit.setPlainText(self._sender_profile)
            self._sender_name_edit.blockSignals(False)
            self._sender_profile_edit.blockSignals(False)
            self._refresh_character_list()
            self._refresh_required_responder_list()
            self._sync_role_strategy()

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
        if isinstance(self._client.strategy, RolePlayStrategy):
            self._render_structured_conversation(self._chat_state.active_branch().messages)
        else:
            self._render_full_conversation(messages)
        self._conversation_dirty = False
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
            try:
                ok = self._conversation_manager.delete_conversation(conversation_id)
                if not ok:
                    QMessageBox.warning(self, "删除失败", f"找不到对话文件，可能已被删除。")
                    self._refresh_history_list()
                    return
            except Exception as e:
                QMessageBox.critical(self, "删除失败", f"删除对话时出错：{e}")
                return
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
                sender = (self._sender_name or "你").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                body_parts.append(f'<div class="user-msg"><strong>🧑 {sender}：</strong><br>{escaped}</div>')
            elif role == "assistant":
                content = self._format_roleplay_display_text(content)
                html_body = md_lib.markdown(
                    content,
                    extensions=["fenced_code", "tables", "codehilite", "nl2br", "sane_lists"],
                )
                assistant_name = (
                    self._assistant_display_name()
                    .replace("&", "&amp;")
                    .replace("<", "&lt;")
                    .replace(">", "&gt;")
                )
                body_parts.append(f'<div class="assistant-msg"><strong>💬 {assistant_name}：</strong><br>{html_body}</div>')

        full_html = (
            f"<html><head>{CURRENT_HTML_STYLE}</head><body>{''.join(body_parts)}"
            f"<script>{REPLY_JUMP_NAV_SCRIPT}rebuildReplyJumpNav();</script></body></html>"
        )
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

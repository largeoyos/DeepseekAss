"""核心模块。

保留 ``from core import DeepSeekChatClient`` 的公开 API，但避免在导入
``core.control_*`` 时连带加载 GUI/导出依赖。
"""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .chat_client import DeepSeekChatClient

__all__ = ["DeepSeekChatClient"]


def __getattr__(name: str) -> Any:
    if name == "DeepSeekChatClient":
        from .chat_client import DeepSeekChatClient

        return DeepSeekChatClient
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

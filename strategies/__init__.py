"""
策略模块 - 策略模式（Strategy Pattern）实现
每个聊天模式作为一个独立的策略类
"""
from .base_strategy import BaseStrategy
from .role_play_strategy import RolePlayStrategy
from .novel_strategy import NovelStrategy
from .code_assistant_strategy import CodeAssistantStrategy
from .continuation_strategy import ContinuationStrategy

__all__ = [
    "BaseStrategy",
    "RolePlayStrategy",
    "NovelStrategy",
    "CodeAssistantStrategy",
    "ContinuationStrategy",
]
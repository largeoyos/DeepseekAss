"""
代码助手模式策略
"""
from .base_strategy import BaseStrategy
from utils.prompts import Prompts


class CodeAssistantStrategy(BaseStrategy):
    """代码助手模式 - 编程帮助、代码审查、调试辅助"""

    def get_name(self) -> str:
        return "代码助手"

    def get_system_prompt(self) -> str:
        return Prompts.CODE_ASSISTANT

    def get_welcome_message(self) -> str:
        return (
            "💻 === 代码助手模式 ===\n"
            "我是你的编程助手。可以帮你：\n"
            "  • 编写 / 优化 / 重构代码\n"
            "  • Debug 分析与错误排查\n"
            "  • 解释技术概念与算法\n"
            "  • 提供架构设计建议\n"
            "直接粘贴代码或描述你的问题即可！\n"
        )

    @property
    def recommended_model(self) -> str:
        return "deepseek-reasoner"  # 推理模型更适合代码任务

    @property
    def recommended_temperature(self) -> float:
        return 0.1  # 低温度保证代码精确性

    @property
    def recommended_max_tokens(self) -> int:
        return 16384  # 代码输出通常较长

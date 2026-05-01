"""
角色扮演模式策略
"""
from .base_strategy import BaseStrategy
from utils.prompts import Prompts


class RolePlayStrategy(BaseStrategy):
    """角色扮演模式 - 模拟特定人物/身份的对话风格"""

    def get_name(self) -> str:
        return "角色扮演"

    def get_system_prompt(self) -> str:
        return Prompts.ROLE_PLAY

    def get_welcome_message(self) -> str:
        return (
            "🎭 === 角色扮演模式 ===\n"
            "你可以让我扮演任意角色（如历史人物、电影角色、职业身份等）。\n"
            "直接告诉我你想让我扮演谁，或者直接开始对话即可。\n"
            "示例：「请扮演一位中世纪骑士」「你是一只有智慧的猫」\n"
        )

    @property
    def recommended_model(self) -> str:
        return "deepseek-chat"

    @property
    def recommended_temperature(self) -> float:
        return 0.9  # 较高温度增加创造性
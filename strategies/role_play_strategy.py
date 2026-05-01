"""
角色扮演模式策略
"""
from .base_strategy import BaseStrategy
from utils.prompts import Prompts


class RolePlayStrategy(BaseStrategy):
    """角色扮演模式 - 模拟特定人物/身份的对话风格"""

    REPLY_MODE_CHARACTER = "character"
    REPLY_MODE_NARRATOR = "narrator"

    def __init__(self):
        self.character_description: str = ""
        self.story_background: str = ""
        self.reply_mode: str = self.REPLY_MODE_CHARACTER

    def get_name(self) -> str:
        return "角色扮演"

    def get_system_prompt(self) -> str:
        base = (
            Prompts.ROLE_PLAY_NARRATOR
            if self.reply_mode == self.REPLY_MODE_NARRATOR
            else Prompts.ROLE_PLAY
        )
        parts = [base]
        if self.character_description.strip():
            parts.append(f"\n\n【角色描述】\n{self.character_description.strip()}")
        if self.story_background.strip():
            parts.append(f"\n\n【故事背景】\n{self.story_background.strip()}")
        return "".join(parts)

    def get_welcome_message(self) -> str:
        return (
            "🎭 === 角色扮演模式 ===\n"
            "你可以让我扮演任意角色（如历史人物、电影角色、职业身份等）。\n"
            "在左侧填写「角色描述」和「故事背景」，然后点击「应用设定」开始对话。\n"
            "示例：「请扮演一位中世纪骑士」「你是一只有智慧的猫」\n"
        )

    @property
    def recommended_model(self) -> str:
        return "deepseek-chat"

    @property
    def recommended_temperature(self) -> float:
        return 0.9  # 较高温度增加创造性
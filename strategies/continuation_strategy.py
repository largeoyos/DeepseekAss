"""
续写小说模式策略
"""
from .base_strategy import BaseStrategy
from utils.prompts import Prompts
from config import Config


class ContinuationStrategy(BaseStrategy):
    """续写小说模式 - 基于已有文档内容续写后续章节"""

    def get_name(self) -> str:
        return "续写小说"

    def get_system_prompt(self) -> str:
        return Prompts.NOVEL_CHAPTER_WRITING

    def get_welcome_message(self) -> str:
        return (
            "📄 === 续写小说模式 ===\n"
            "可以基于现有文档内容续写后续章节。\n"
            "  • 选择源文档（.txt / .md）或包含文本文件的文件夹\n"
            "  • 填写续写要求和剧情走向\n"
            "  • AI 自动续写并保存为书架中的新章节\n"
            "在左侧面板选择源文档后点击「开始续写」即可。\n"
        )

    @property
    def recommended_model(self) -> str:
        return Config.MODEL_V4_FLASH

    @property
    def recommended_temperature(self) -> float:
        return 0.85

    @property
    def recommended_top_p(self) -> float:
        return 0.9

    @property
    def recommended_frequency_penalty(self) -> float:
        return 0.3

    @property
    def recommended_max_tokens(self) -> int:
        return 32768

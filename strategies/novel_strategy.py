"""
小说写作模式策略（增强版）
- 章节控制：下一章编号、章节标题
- 书架选项：创建/列出/删除小说项目
- 小说设定：标题、主角背景、世界观、写作要求
- 自动前情提要 + 章节摘要生成
"""
from .base_strategy import BaseStrategy
from utils.prompts import Prompts
from config import Config


class NovelStrategy(BaseStrategy):
    """小说写作模式 - 辅助创意写作、情节构思、文笔润色"""

    def __init__(self) -> None:
        super().__init__()
        # 小说特有参数
        self._novel_title: str = ""
        self._chapter_title: str = ""
        self._protagonist_bio: str = ""
        self._background_story: str = ""
        self._writing_demand: str = ""
        # 是否使用小说章节模式（而非自由对话）
        self._chapter_mode: bool = False

    # ========== 小说参数 getter/setter ==========

    @property
    def novel_title(self) -> str:
        return self._novel_title

    @novel_title.setter
    def novel_title(self, value: str) -> None:
        self._novel_title = value

    @property
    def chapter_title(self) -> str:
        return self._chapter_title

    @chapter_title.setter
    def chapter_title(self, value: str) -> None:
        self._chapter_title = value

    @property
    def protagonist_bio(self) -> str:
        return self._protagonist_bio

    @protagonist_bio.setter
    def protagonist_bio(self, value: str) -> None:
        self._protagonist_bio = value

    @property
    def background_story(self) -> str:
        return self._background_story

    @background_story.setter
    def background_story(self, value: str) -> None:
        self._background_story = value

    @property
    def writing_demand(self) -> str:
        return self._writing_demand

    @writing_demand.setter
    def writing_demand(self, value: str) -> None:
        self._writing_demand = value

    @property
    def chapter_mode(self) -> bool:
        return self._chapter_mode

    @chapter_mode.setter
    def chapter_mode(self, value: bool) -> None:
        self._chapter_mode = value

    # ========== 策略接口 ==========

    def get_name(self) -> str:
        return "小说写作"

    def get_system_prompt(self) -> str:
        if self._chapter_mode and self._novel_title:
            return Prompts.NOVEL_CHAPTER_WRITING
        return Prompts.NOVEL_WRITING

    def get_welcome_message(self) -> str:
        return (
            "✍️ === 小说写作模式 ===\n"
            "我是你的创意写作助手。可以帮你：\n"
            "  • 构思情节与人物设定\n"
            "  • 续写章节 / 润色文笔\n"
            "  • 世界观搭建 / 对话编写\n"
            "  • 提供写作建议与灵感\n"
            "  • 📚 使用书架管理多部小说\n"
            "  • 📖 按章节自动续写 + 剧情记忆\n"
            "告诉我你想写什么题材，或直接在左侧面板设置小说参数后开始！\n"
        )

    @property
    def recommended_model(self) -> str:
        return Config.MODEL_V4_FLASH

    @property
    def recommended_temperature(self) -> float:
        return 0.85  # 写作需要一定创造性

    @property
    def recommended_top_p(self) -> float:
        return 0.9

    @property
    def recommended_frequency_penalty(self) -> float:
        return 0.5  # 防止词汇匮乏

    @property
    def recommended_max_tokens(self) -> int:
        return 32768  # 长篇小说单次输出上限（约20000～30000中文字）

    # ========== 构建章节写作 User Prompt ==========

    def build_chapter_prompt(
        self, summary: str, chapter_num: int, chapter_title: str
    ) -> str:
        """
        构建章节续写的 User Prompt，整合所有设定

        Args:
            summary: 前情提要
            chapter_num: 本章编号
            chapter_title: 本章标题

        Returns:
            完整的 user prompt 字符串
        """
        parts = [
            f"【前情提要】：\n{summary}\n",
            f"现在请开始撰写第 {chapter_num} 章：{chapter_title}。\n",
        ]
        if self._writing_demand.strip():
            parts.append(f"【本章要求】：\n{self._writing_demand}\n")
        return "\n".join(parts)

    def build_system_messages(self) -> list[dict]:
        """
        构建多层 System Message 列表（核心设定 + 人物背景）

        Returns:
            包含多个 system role dict 的列表
        """
        messages = []
        if self._background_story.strip():
            messages.append({
                "role": "system",
                "content": f"【核心设定】：\n{self._background_story}",
            })
        if self._protagonist_bio.strip():
            messages.append({
                "role": "system",
                "content": f"【人物背景】：\n{self._protagonist_bio}",
            })
        return messages
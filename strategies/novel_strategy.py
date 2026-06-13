"""
小说写作模式策略（增强版）
- 章节控制：下一章编号、章节标题
- 书架选项：创建/列出/删除小说项目
- 小说设定：标题、主角背景、世界观、写作要求
- 自动前情提要 + 章节摘要生成
"""
from .base_strategy import BaseStrategy
from utils.prompts import Prompts
from utils.genre_styles import get_genre_by_key, get_tone_by_key
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
        # 题材与风格基调
        self._genre: str = ""
        self._style_tone: str = ""
        self._xp_mode: bool = False

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

    # ========== 题材与风格基调 ==========

    @property
    def genre(self) -> str:
        return self._genre

    @genre.setter
    def genre(self, value: str) -> None:
        self._genre = value

    @property
    def style_tone(self) -> str:
        return self._style_tone

    @style_tone.setter
    def style_tone(self, value: str) -> None:
        self._style_tone = value

    @property
    def xp_mode(self) -> bool:
        return self._xp_mode

    @xp_mode.setter
    def xp_mode(self, value: bool) -> None:
        self._xp_mode = bool(value)

    @property
    def genre_style_text(self) -> str:
        """组装【风格设定】文本，供注入 prompt"""
        parts = []
        cfg = get_genre_by_key(self._genre)
        if cfg and cfg.style_instruction:
            parts.append(f"题材方向（{cfg.display_name}）：{cfg.style_instruction}")
        tone = get_tone_by_key(self._style_tone)
        if tone and tone.style_instruction:
            parts.append(f"写作基调（{tone.display_name}）：{tone.style_instruction}")
        if self._xp_mode:
            parts.append(Prompts.XP_MODE_SYSTEM)
        return "\n".join(parts)

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

    def build_system_messages(self) -> list[dict]:
        """
        构建多层 System Message 列表（核心设定 + 人物背景 + 风格设定）

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
        style_text = self.genre_style_text
        if style_text:
            messages.append({
                "role": "system",
                "content": f"【风格设定】\n{style_text}",
            })
        return messages

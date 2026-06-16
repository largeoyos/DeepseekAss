"""
Role-play chat strategy.
"""
from .base_strategy import BaseStrategy
from utils.prompts import Prompts
from core.character_book import CharacterBook, ChatTimelineEntry, format_character_book_for_prompt


class RolePlayStrategy(BaseStrategy):
    """Role-play mode backed by global character profiles and per-chat timelines."""

    REPLY_MODE_CHARACTER = "character"
    REPLY_MODE_NARRATOR = "narrator"

    def __init__(self):
        # Legacy fields kept for old conversations and quick one-off role play.
        self.character_description: str = ""
        self.story_background: str = ""
        self.reply_mode: str = self.REPLY_MODE_CHARACTER

        self.character_book: CharacterBook | None = None
        self.participant_character_ids: list[str] = []
        self.primary_character_id: str = ""
        self.chat_type: str = "private"
        self.timeline: list[ChatTimelineEntry] = []

    def get_name(self) -> str:
        return "角色扮演"

    def get_system_prompt(self) -> str:
        base = (
            Prompts.ROLE_PLAY_NARRATOR
            if self.reply_mode == self.REPLY_MODE_NARRATOR
            else Prompts.ROLE_PLAY
        )
        parts = [base]

        if self.character_book and self.participant_character_ids:
            if self.chat_type == "group":
                parts.append(
                    "\n\n【聊天类型】\n群聊"
                    "\n\n【群聊规则】\n"
                    "你需要同时维护所有参与角色的人设、关系和当前状态。"
                    "发言时用「角色名：内容」区分说话者；旁白可单独成段。"
                    "不要让角色知道其人物书中没有获得的信息。"
                )
            else:
                parts.append(
                    "\n\n【聊天类型】\n私聊"
                    "\n\n【私聊规则】\n"
                    "你主要扮演主角色与用户互动，保持其说话风格、边界、经历和当前状态。"
                    "不要混入其他会话的时间线事件。"
                )
            book_text = format_character_book_for_prompt(
                self.character_book,
                self.participant_character_ids,
                self.timeline,
            )
            if book_text:
                parts.append(f"\n\n【人物书与当前会话时间线】\n{book_text}")

        if self.character_description.strip():
            parts.append(f"\n\n【角色描述】\n{self.character_description.strip()}")
        if self.story_background.strip():
            parts.append(f"\n\n【故事背景】\n{self.story_background.strip()}")
        return "".join(parts)

    def get_welcome_message(self) -> str:
        return (
            "🎭 === 角色扮演模式 ===\n"
            "可以创建多个全局角色档案，并发起单人私聊或多人群聊。\n"
            "对话推进后，角色经历、关系、状态会自动同步到人物书；每个对话保留独立时间线。\n"
        )

    @property
    def recommended_model(self) -> str:
        return "deepseek-chat"

    @property
    def recommended_temperature(self) -> float:
        return 0.9

"""
Role-play chat strategy.
"""
from .base_strategy import BaseStrategy
from utils.prompts import Prompts
from core.character_book import CharacterBook, ChatTimelineEntry, format_character_book_for_prompt
from core.chat_domain import SceneState, TurnPolicy, SenderProfile


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
        self.sender_name: str = "你"
        self.sender_profile: str = ""
        self.required_responder_ids: list[str] = []
        self.turn_policy = TurnPolicy()
        self.scene_state = SceneState()
        self.sender_profile_record: SenderProfile | None = None
        self.narrator_enabled: bool = False
        self.structured_output: bool = True
        self.active_branch_id: str = "main"

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
            sender_parts = [f"发送者称呼：{self.sender_name or '用户'}"]
            if self.sender_profile.strip():
                sender_parts.append(f"发送者信息：{self.sender_profile.strip()}")
            parts.append("\n\n【聊天发送者档案】\n" + "\n".join(sender_parts))
            if self.scene_state.location or self.scene_state.description:
                scene_lines = []
                for label, value in (
                    ("时间", self.scene_state.time),
                    ("地点", self.scene_state.location),
                    ("天气", self.scene_state.weather),
                    ("目标", self.scene_state.objective),
                    ("环境", self.scene_state.description),
                ):
                    if value:
                        scene_lines.append(f"{label}：{value}")
                if self.scene_state.tags:
                    scene_lines.append("标签：" + "、".join(self.scene_state.tags))
                parts.append("\n\n【当前场景】\n" + "\n".join(scene_lines))
            if self.chat_type == "group":
                required_names = []
                for profile in self.character_book.profiles:
                    if profile.character_id in self.required_responder_ids:
                        required_names.append(profile.name)
                parts.append(
                    "\n\n【聊天类型】\n群聊"
                    "\n\n【群聊规则】\n"
                    "你需要同时维护所有参与角色的人设、关系和当前状态。"
                    "每个角色的每段发言必须以「角色名：」开头；旁白必须以「旁白：」开头。"
                    "不要让角色知道其人物书中没有获得的信息。"
                )
                if required_names:
                    parts.append(
                        "\n\n【本轮强制回复角色】\n"
                        + "、".join(required_names)
                        + "\n以上角色本轮都必须各自至少发言一次，其他参与角色可按情境决定是否发言。"
                    )
                policy = self.turn_policy
                name_by_id = {profile.character_id: profile.name for profile in self.character_book.profiles}
                policy_lines = []
                if policy.allowed_speaker_ids:
                    policy_lines.append("允许发言：" + "、".join(name_by_id.get(cid, cid) for cid in policy.allowed_speaker_ids))
                if policy.blocked_speaker_ids:
                    policy_lines.append("禁止发言：" + "、".join(name_by_id.get(cid, cid) for cid in policy.blocked_speaker_ids))
                if policy.mention_only_ids:
                    policy_lines.append("仅被点名时发言：" + "、".join(name_by_id.get(cid, cid) for cid in policy.mention_only_ids))
                if policy.speaker_order:
                    policy_lines.append("发言顺序：" + " → ".join(name_by_id.get(cid, cid) for cid in policy.speaker_order))
                if policy.max_speakers:
                    policy_lines.append(f"本轮最多 {policy.max_speakers} 个角色发言")
                if policy_lines:
                    parts.append("\n\n【本轮发言策略】\n" + "\n".join(policy_lines))
                if self.narrator_enabled:
                    parts.append("\n\n【旁白主持】\n启用独立旁白主持。旁白负责场景推进和动作描写，但不替角色做决定。")
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
            knowledge_lines = []
            for memory in self.character_book.memories:
                if memory.character_id not in self.participant_character_ids:
                    continue
                visible = [
                    item for item in memory.knowledge
                    if item.get("branch_id", "main") in ("", self.active_branch_id)
                    and item.get("awareness") != "unknown"
                ]
                if visible:
                    knowledge_lines.append(
                        f"{memory.name}可知：" + "；".join(item.get("fact", "") for item in visible[-8:])
                    )
            if knowledge_lines:
                parts.append("\n\n【角色视角隔离】\n" + "\n".join(knowledge_lines))
                parts.append("\n任何角色不得使用未列在自己可知信息中的秘密或场外信息。")

        if self.structured_output and self.chat_type == "group":
            parts.append(
                "\n\n【输出格式】\n只输出合法 JSON，不要使用 Markdown 代码块："
                '{"messages":[{"speaker_id":"角色ID或narrator","speaker_name":"角色名或旁白",'
                '"content":"发言内容","action":"可选动作"}]}'
            )

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

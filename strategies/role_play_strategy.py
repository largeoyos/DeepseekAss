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
                    "\n\n【聊天类型】\n发送者与多个角色聊天"
                    "\n\n【多人聊天核心规则】\n"
                    f"1. 当前真实聊天发送者是「{self.sender_name or '用户'}」。"
                    "用户的最新输入是发送者刚刚发出的消息；本轮所有角色发言都应优先回应这条消息。"
                    "\n2. 你只代演角色，不得替发送者补写台词、动作、心理、决定或未提供的信息。"
                    "\n3. 每个 JSON 消息对象只表示一个角色发给发送者的一条回复。"
                    "不要在同一个 content 中继续编写其他角色的台词，也不要自行展开角色之间的连续对戏。"
                    "\n4. 角色可以简短提及或回应其他角色已经说过的话，但回复重心仍是发送者。"
                    "只有发送者明确要求角色互相讨论、争论或演出场景时，才允许角色之间连续互动。"
                    "\n5. 同时维护所有参与角色的人设、关系、当前状态和独立知识边界。"
                    "不要让角色知道其人物书中没有获得的信息。"
                )
                if self.reply_mode == self.REPLY_MODE_NARRATOR:
                    parts.append(
                        "\n\n【群聊叙述视角】\n每个消息块使用第三人称叙述，"
                        "可包含对应角色的台词，但不要把整段写成角色第一人称独白。"
                    )
                else:
                    parts.append(
                        "\n\n【群聊叙述视角】\n每个角色的 content 以该角色的第一人称表达，"
                        "动作和神态可用括号或星号补充，不要改成全知第三人称旁白。"
                    )
                if required_names:
                    parts.append(
                        "\n\n【本轮强制回复角色】\n"
                        + "、".join(required_names)
                        + f"\n以上角色本轮都必须各自至少向「{self.sender_name or '用户'}」回复一次，"
                        "其他参与角色可按情境决定是否回复。"
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
                '"content":"该角色对发送者的回复","action":"仅填写该角色自己的可选动作"}]}'
                "\nmessages 中禁止出现 user/sender 类型消息，禁止生成发送者的回复，"
                "禁止把多个角色的发言合并在一个 content 中。"
            )

        if self.character_description.strip():
            parts.append(f"\n\n【角色描述】\n{self.character_description.strip()}")
        if self.story_background.strip():
            parts.append(f"\n\n【故事背景】\n{self.story_background.strip()}")
        return "".join(parts)

    def get_welcome_message(self) -> str:
        return (
            "🎭 === 角色扮演模式 ===\n"
            "可以创建多个全局角色档案，并发起单角色聊天或与多个角色聊天。\n"
            "对话推进后，角色经历、关系、状态会自动同步到人物书；每个对话保留独立时间线。\n"
        )

    @property
    def recommended_model(self) -> str:
        return "deepseek-chat"

    @property
    def recommended_temperature(self) -> float:
        return 0.9

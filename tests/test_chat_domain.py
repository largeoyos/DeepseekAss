import tempfile
import unittest
from dataclasses import asdict

from core.character_book import (
    CharacterBook,
    CharacterMemory,
    CharacterProfile,
    build_memory_change_set,
)
from core.chat_domain import (
    ChatSessionState,
    MemoryChangeSet,
    ScenePreset,
    ScenePresetManager,
    SceneState,
    SenderProfile,
    SenderProfileManager,
    TurnPolicy,
    apply_memory_change_set,
    fork_branch,
    legacy_messages_to_structured,
    parse_structured_reply,
    revert_memory_change_set,
    state_from_dict,
    state_to_dict,
)
from core.conversation_manager import ConversationManager
from strategies.role_play_strategy import RolePlayStrategy


class ChatDomainTests(unittest.TestCase):
    def test_legacy_group_messages_are_split_by_speaker(self):
        messages = legacy_messages_to_structured(
            [
                {"role": "user", "content": "go"},
                {"role": "assistant", "content": "A: one\n\nB: two"},
            ],
            name_to_id={"A": "a", "B": "b"},
        )
        self.assertEqual(["sender", "a", "b"], [message.speaker_id for message in messages])

    def test_invalid_json_falls_back_to_speaker_lines(self):
        messages = parse_structured_reply(
            "A: hello\nB: world", "main", 1, {"A": "a", "B": "b"}
        )
        self.assertEqual(["a", "b"], [message.speaker_id for message in messages])

    def test_structured_reply_normalizes_name_based_speaker_ids(self):
        messages = parse_structured_reply(
            '{"messages":[{"speaker_id":"A","speaker_name":"A","content":"hello"},'
            '{"speaker_id":"wrong-example-id","speaker_name":"B","content":"world"}]}',
            "main",
            1,
            {"A": "character-a", "B": "character-b"},
        )
        self.assertEqual(
            ["character-a", "character-b"],
            [message.speaker_id for message in messages],
        )
    def test_multi_character_chat_targets_sender(self):
        strategy = RolePlayStrategy()
        strategy.chat_type = "group"
        strategy.sender_name = "Player"
        strategy.character_book = CharacterBook(
            profiles=[
                CharacterProfile(character_id="a", name="A"),
                CharacterProfile(character_id="b", name="B"),
            ]
        )
        strategy.participant_character_ids = ["a", "b"]
        strategy.required_responder_ids = ["a", "b"]
        prompt = strategy.get_system_prompt()
        self.assertIn("本轮所有角色发言都应优先回应这条消息", prompt)
        self.assertIn("不得替发送者补写台词", prompt)
        self.assertIn("各自至少向「Player」回复一次", prompt)
        self.assertIn("禁止生成发送者的回复", prompt)
    def test_branch_round_trip_is_independent(self):
        state = ChatSessionState()
        main = state.active_branch()
        main.messages = legacy_messages_to_structured(
            [{"role": "user", "content": "hello"}]
        )
        branch = fork_branch(state, main.messages[0].message_id)
        branch.messages[0].content = "changed"
        restored = state_from_dict(state_to_dict(state))
        self.assertEqual(2, len(restored.branches))
        self.assertEqual("hello", restored.branches[0].messages[0].content)
        self.assertEqual("changed", restored.branches[1].messages[0].content)

    def test_low_and_high_risk_changes_can_apply_and_revert(self):
        book = CharacterBook(
            profiles=[CharacterProfile(character_id="a", name="A", identity="old")],
            memories=[CharacterMemory(character_id="a", name="A")],
        )
        change_set = build_memory_change_set(
            book,
            {
                "characters": [{
                    "character_id": "a",
                    "experiences": ["met player"],
                    "high_risk_changes": [{
                        "field_name": "identity",
                        "new_value": "new",
                        "reason": "revealed",
                    }],
                }],
            },
            ["a"],
            "main",
            ["message-1"],
        )
        apply_memory_change_set(book, change_set)
        self.assertEqual(["met player"], book.memories[0].experiences)
        self.assertEqual("new", book.profiles[0].identity)
        revert_memory_change_set(book, change_set)
        self.assertEqual([], book.memories[0].experiences)
        self.assertEqual("old", book.profiles[0].identity)

    def test_schema_v4_and_sender_profile_round_trip(self):
        root = tempfile.mkdtemp()
        state = ChatSessionState()
        state.scene_state = SceneState(location="room", present_character_ids=["a"])
        state.turn_policy = TurnPolicy(required_speaker_ids=["a"], max_speakers=1)
        manager = ConversationManager(root)
        manager.save_conversation(
            "conv",
            "title",
            "model",
            [{"role": "user", "content": "hello"}],
            branches=state_to_dict(state)["branches"],
            scene_state=asdict(state.scene_state),
            turn_policy=asdict(state.turn_policy),
            schema_version=4,
        )
        restored = state_from_dict(manager.load_conversation("conv"))
        self.assertEqual("room", restored.scene_state.location)
        self.assertEqual(["a"], restored.turn_policy.required_speaker_ids)

        sender_manager = SenderProfileManager(root)
        sender_manager.save([SenderProfile(name="Player")])
        self.assertEqual("Player", sender_manager.load()[0].name)

        scene_manager = ScenePresetManager(root)
        scene_manager.save([
            ScenePreset(
                name="Library",
                scene=SceneState(
                    time="night",
                    location="library",
                    present_character_ids=["a"],
                ),
            )
        ])
        scene = scene_manager.load()[0]
        self.assertEqual("Library", scene.name)
        self.assertEqual("library", scene.scene.location)
        self.assertEqual(["a"], scene.scene.present_character_ids)


if __name__ == "__main__":
    unittest.main()

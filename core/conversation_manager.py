"""
角色扮演对话历史管理器
负责角色扮演模式下对话记录的保存、加载、列表与删除
"""
import json
import os
from dataclasses import dataclass, asdict
from datetime import datetime


CONVERSATIONS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "conversations"
)


@dataclass
class ConversationMeta:
    """单次对话的元信息"""
    conversation_id: str = ""
    title: str = ""
    model: str = ""
    strategy: str = ""
    created_at: str = ""
    updated_at: str = ""
    message_count: int = 0
    chat_type: str = ""


class ConversationManager:
    """角色扮演对话历史管理器"""

    def __init__(self, root_dir: str | None = None,
                 crypto=None, enc_key: bytes | None = None) -> None:
        self._root_dir = root_dir or CONVERSATIONS_DIR
        self._crypto = crypto
        from core.repositories import ConversationRepository
        from core.storage import EncryptedStorage
        self._repository = ConversationRepository(
            EncryptedStorage(self._root_dir, crypto=crypto, enc_key=enc_key)
        )
        self._enc_key = enc_key
        os.makedirs(self._root_dir, exist_ok=True)

    # ========== 加密文件 I/O 辅助 ==========

    def _encrypt_path(self, path: str) -> str:
        if self._enc_key is None:
            return path
        return path + ".enc"

    def _read_encrypted_json(self, path: str) -> dict | None:
        return self._repository.storage.read_json(os.path.relpath(path, self._root_dir).replace("\\", "/"))

    def _write_encrypted_json(self, path: str, data: dict) -> None:
        relative = os.path.relpath(path, self._root_dir).replace("\\", "/")
        self._repository.storage.write_json(relative, data)

    def _encrypted_file_exists(self, path: str) -> bool:
        return os.path.exists(self._encrypt_path(path))

    # ========== 列表 ==========

    def list_conversations(self) -> list[ConversationMeta]:
        """列出所有已保存的对话"""
        result: list[ConversationMeta] = []
        if not os.path.isdir(self._root_dir):
            return result
        for fname in sorted(os.listdir(self._root_dir), reverse=True):
            match_name = fname[:-4] if fname.endswith(".enc") else fname
            if not match_name.endswith(".json"):
                continue
            fpath = os.path.join(self._root_dir, match_name)
            try:
                data = self._read_encrypted_json(fpath)
                if data is None:
                    continue
                result.append(ConversationMeta(
                    conversation_id=data.get("conversation_id", ""),
                    title=data.get("title", "未命名对话"),
                    model=data.get("model", ""),
                    strategy=data.get("strategy", ""),
                    created_at=data.get("created_at", ""),
                    updated_at=data.get("updated_at", ""),
                    message_count=len(data.get("messages", [])),
                    chat_type=data.get("chat_type", ""),
                ))
            except (json.JSONDecodeError, KeyError, Exception):
                continue
        return result

    # ========== 保存 ==========

    def save_conversation(
        self,
        conversation_id: str,
        title: str,
        model: str,
        messages: list[dict],
        character_description: str = "",
        story_background: str = "",
        strategy: str = "",
        reply_mode: str = "",
        chat_type: str = "",
        participant_character_ids: list[str] | None = None,
        primary_character_id: str = "",
        timeline_id: str = "",
        timeline: list[dict] | None = None,
        character_book_snapshot: dict | None = None,
        sender_name: str = "",
        sender_profile: str = "",
        required_responder_ids: list[str] | None = None,
        structured_messages: list[dict] | None = None,
        branches: list[dict] | None = None,
        active_branch_id: str = "main",
        sender_profile_id: str = "",
        scene_state: dict | None = None,
        turn_policy: dict | None = None,
        memory_change_sets: list[dict] | None = None,
        narrator_enabled: bool = False,
        schema_version: int = 1,
    ) -> str:
        """
        保存对话记录到 JSON 文件

        Args:
            conversation_id: 对话唯一标识
            title: 对话标题
            model: 使用的模型
            messages: 完整消息列表（含 system/user/assistant）
            character_description: 角色扮演模式下的角色描述
            story_background: 角色扮演模式下的故事背景
            strategy: 创建此对话的策略/模式名称
            reply_mode: 角色扮演模式下的回复方式（character/narrator）

        Returns:
            保存的文件路径
        """
        os.makedirs(self._root_dir, exist_ok=True)

        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # 检查是否已存在（更新而非新建）
        existing_path = self._find_file(conversation_id)
        created_at = now_str
        if existing_path:
            try:
                old_data = self._read_encrypted_json(existing_path)
                if old_data:
                    created_at = old_data.get("created_at", now_str)
            except Exception:
                pass

        record = {
            "conversation_id": conversation_id,
            "title": title,
            "model": model,
            "strategy": strategy,
            "reply_mode": reply_mode,
            "created_at": created_at,
            "updated_at": now_str,
            "character_description": character_description,
            "story_background": story_background,
            "messages": messages,
            "chat_type": chat_type,
            "participant_character_ids": participant_character_ids or [],
            "primary_character_id": primary_character_id,
            "timeline_id": timeline_id,
            "timeline": timeline or [],
            "character_book_snapshot": character_book_snapshot or {},
            "sender_name": sender_name,
            "sender_profile": sender_profile,
            "required_responder_ids": required_responder_ids or [],
            "structured_messages": structured_messages or [],
            "branches": branches or [],
            "active_branch_id": active_branch_id,
            "sender_profile_id": sender_profile_id,
            "scene_state": scene_state or {},
            "turn_policy": turn_policy or {},
            "memory_change_sets": memory_change_sets or [],
            "narrator_enabled": narrator_enabled,
            "schema_version": schema_version,
        }

        safe_id = conversation_id.replace("/", "-").replace("\\", "-").replace(":", "：")
        file_path = os.path.join(self._root_dir, f"{safe_id}.json")
        self._write_encrypted_json(file_path, record)
        return self._encrypt_path(file_path)

    # ========== 加载 ==========

    def load_conversation(self, conversation_id: str) -> dict | None:
        """
        加载指定对话的完整记录

        Returns:
            包含 conversation_id / title / model / messages 的字典，不存在返回 None
        """
        file_path = self._find_file(conversation_id)
        if not file_path:
            return None
        return self._read_encrypted_json(file_path)

    def load_messages(self, conversation_id: str) -> list[dict] | None:
        """仅加载对话的消息列表"""
        record = self.load_conversation(conversation_id)
        if record is None:
            return None
        return record.get("messages", [])

    def get_preview(self, conversation_id: str, max_chars: int = 80) -> str:
        """获取对话的简短预览（最后一条用户消息的前 max_chars 字）"""
        messages = self.load_messages(conversation_id)
        if not messages:
            return "(空对话)"
        # 从后往前找第一条 user 消息
        for msg in reversed(messages):
            if msg.get("role") == "user":
                content = msg.get("content", "").strip()
                if content:
                    preview = content[:max_chars].replace("\n", " ")
                    return preview + ("…" if len(content) > max_chars else "")
        return "(无用户消息)"

    # ========== 删除 ==========

    def delete_conversation(self, conversation_id: str) -> bool:
        """删除指定对话"""
        file_path = self._find_file(conversation_id)
        if file_path:
            try:
                actual_path = self._encrypt_path(file_path)
                os.remove(actual_path)
                return True
            except OSError:
                return False
        return False

    # ========== 生成 ID ==========

    @staticmethod
    def generate_id(title: str) -> str:
        """根据标题和时间生成唯一 ID"""
        safe_title = title.replace(" ", "_").replace("/", "-").replace("\\", "-")[:30]
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return f"{safe_title}_{timestamp}"

    # ========== 内部 ==========

    def _find_file(self, conversation_id: str) -> str | None:
        """根据 conversation_id 查找对应文件（会检查 .enc 变体）"""
        safe_id = conversation_id.replace("/", "-").replace("\\", "-").replace(":", "：")
        base = os.path.join(self._root_dir, f"{safe_id}.json")
        if os.path.exists(base):
            return base
        base_enc = base + ".enc"
        if os.path.exists(base_enc):
            return base  # 返回无 .enc 路径，read/write/delete 会通过 _encrypt_path 处理
        return None

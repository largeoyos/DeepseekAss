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
    created_at: str = ""
    updated_at: str = ""
    message_count: int = 0


class ConversationManager:
    """角色扮演对话历史管理器"""

    def __init__(self, root_dir: str | None = None) -> None:
        self._root_dir = root_dir or CONVERSATIONS_DIR
        os.makedirs(self._root_dir, exist_ok=True)

    # ========== 列表 ==========

    def list_conversations(self) -> list[ConversationMeta]:
        """列出所有已保存的对话"""
        result: list[ConversationMeta] = []
        if not os.path.isdir(self._root_dir):
            return result
        for fname in sorted(os.listdir(self._root_dir), reverse=True):
            if fname.endswith(".json"):
                fpath = os.path.join(self._root_dir, fname)
                try:
                    with open(fpath, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    result.append(ConversationMeta(
                        conversation_id=data.get("conversation_id", ""),
                        title=data.get("title", "未命名对话"),
                        model=data.get("model", ""),
                        created_at=data.get("created_at", ""),
                        updated_at=data.get("updated_at", ""),
                        message_count=len(data.get("messages", [])),
                    ))
                except (json.JSONDecodeError, KeyError):
                    continue
        return result

    # ========== 保存 ==========

    def save_conversation(
        self,
        conversation_id: str,
        title: str,
        model: str,
        messages: list[dict],
    ) -> str:
        """
        保存对话记录到 JSON 文件

        Args:
            conversation_id: 对话唯一标识
            title: 对话标题
            model: 使用的模型
            messages: 完整消息列表（含 system/user/assistant）

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
                with open(existing_path, "r", encoding="utf-8") as f:
                    old_data = json.load(f)
                created_at = old_data.get("created_at", now_str)
            except (json.JSONDecodeError, KeyError):
                pass

        record = {
            "conversation_id": conversation_id,
            "title": title,
            "model": model,
            "created_at": created_at,
            "updated_at": now_str,
            "messages": messages,
        }

        safe_id = conversation_id.replace("/", "-").replace("\\", "-").replace(":", "：")
        file_path = os.path.join(self._root_dir, f"{safe_id}.json")
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False, indent=2)
        return file_path

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
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def load_messages(self, conversation_id: str) -> list[dict] | None:
        """仅加载对话的消息列表"""
        record = self.load_conversation(conversation_id)
        if record is None:
            return None
        return record.get("messages", [])

    # ========== 删除 ==========

    def delete_conversation(self, conversation_id: str) -> bool:
        """删除指定对话"""
        file_path = self._find_file(conversation_id)
        if file_path:
            os.remove(file_path)
            return True
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
        """根据 conversation_id 查找对应文件"""
        safe_id = conversation_id.replace("/", "-").replace("\\", "-").replace(":", "：")
        file_path = os.path.join(self._root_dir, f"{safe_id}.json")
        if os.path.exists(file_path):
            return file_path
        return None
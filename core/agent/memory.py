from __future__ import annotations

from core.agent.types import now_iso


class ContextCompactor:
    def __init__(self, threshold_ratio: float = 0.70, keep_recent: int = 8) -> None:
        self.threshold_ratio = threshold_ratio
        self.keep_recent = max(4, keep_recent)

    def needs_compaction(self, messages: list[dict], budget: int) -> bool:
        return sum(len(str(item.get("content", ""))) for item in messages) >= int(budget * self.threshold_ratio)

    def compact(self, messages: list[dict]) -> tuple[list[dict], dict | None]:
        if len(messages) <= self.keep_recent + 1:
            return messages, None
        system = messages[:1] if messages and messages[0].get("role") == "system" else []
        old, recent = messages[len(system):-self.keep_recent], messages[-self.keep_recent:]
        facts, decisions, actions, pending = [], [], [], []
        for item in old:
            role, text = item.get("role", ""), str(item.get("content", "")).strip()
            if not text:
                continue
            clipped = text[:600].replace("\n", " ")
            (decisions if role == "user" else actions if role == "tool" else facts).append(clipped)
            if any(word in text for word in ("待", "需要", "未完成", "审批")):
                pending.append(clipped)
        summary = {"created_at": now_iso(), "confirmed_facts": facts[-8:], "user_decisions": decisions[-8:], "executed_actions": actions[-8:], "pending_items": pending[-8:], "protected_constraints": ["正式写入必须审批", "禁止文件系统和 Shell"], "compacted_message_count": len(old)}
        summary_message = {"role": "system", "content": "历史会话压缩记忆：\n" + "\n".join(f"{key}: {value}" for key, value in summary.items() if key != "created_at")}
        return system + [summary_message] + recent, summary

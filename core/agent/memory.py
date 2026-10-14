from __future__ import annotations

from core.agent.types import now_iso


def fit_retrievable_context(messages: list[dict], input_budget: int) -> list[dict]:
    """Clip re-readable facts only, preserving user text and tool call pairs."""
    import copy
    from core.model_gateway import estimate_messages_tokens, _clip_text_to_tokens
    result = copy.deepcopy(messages)
    if estimate_messages_tokens(result) <= input_budget:
        return result
    eligible = []
    for index, item in enumerate(result):
        metadata = item.get("metadata") or {}
        if item.get("manual_reference") or metadata.get("manual_reference") or metadata.get("pinned"):
            continue
        if item.get("role") == "tool" or metadata.get("agent_retrievable_context"):
            if isinstance(item.get("content"), str):
                eligible.append((index, item["content"]))
    marker = "\n[因模型输入预算裁剪；这不是完整资料。需要更多事实时请按节点或实体精确读取。]"
    def candidate(cap):
        for index, text in eligible:
            clipped = _clip_text_to_tokens(text, cap)
            result[index]["content"] = clipped + marker if clipped != text else text
        return estimate_messages_tokens(result)
    # Do not discard mandatory instructions or leave orphaned tool responses.
    if not eligible or candidate(64) > input_budget:
        return messages
    low, high = 64, input_budget
    while low < high:
        mid = (low + high + 1) // 2
        if candidate(mid) <= input_budget:
            low = mid
        else:
            high = mid - 1
    candidate(low)
    return result


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

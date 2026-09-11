"""Compact instructions exposed to MCP clients as a discoverable resource."""

AI_CONTROL_GUIDE = """# DeepseekAss AI 控制快速指南

你正在操作本地小说项目。必须遵守以下顺序和安全边界。

1. 首先调用 `get_control_settings`，了解默认分页参数和当前权限。
2. 调用 `list_books`，之后始终优先使用返回的稳定 `book_id`。
3. 写作或分析前读取 `get_project`、`get_chapter_tree` 和 `get_world_bible`。
4. 章节树、正文和世界书都可分页。只要 `next_offset`/`next_start` 不为 null，就继续读取，不要假设已看到全部内容。
5. 搜索默认范围由设置决定；检查平行版本时显式使用 `scope="all"`。
6. 需要创作新章时，优先调用 `run_writing_agent`。它会让 DeepseekAss 内置写作总管读取项目上下文，并创建待人工审批的章节提案。
7. 只有在用户已提供完整新正文、且是修订既有节点时，才使用 `propose_chapter_revision`。
8. 世界书变更只能通过 `propose_world_bible_patch`，并使用允许的字段级操作与合法作用域。
9. 任何提案都不代表已生效。返回 `change_set_id` 后要明确告诉用户「正在等待人工审批」。
10. 你无权批准、拒绝、删除、切换分支或重挂节点；不得绕过该限制。

## 推荐写作调用

```json
{
  "book": "<book_id>",
  "instruction": "续写下一章，推进失踪信件的线索，保持克制的悬疑节奏，不要提前揭示幕后人。",
  "chapter_title": "雪后的脚印",
  "target_words": 3500,
  "manual_references": []
}
```

返回后检查 `status`、`change_set_ids`、`requires_human_approval` 和 `warning`。如果没有 `change_set_ids`，不要声称已提交章节。
"""

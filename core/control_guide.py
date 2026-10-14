"""Compact instructions exposed to MCP clients as a discoverable resource."""

AI_CONTROL_GUIDE = """# DeepseekAss AI 控制快速指南

你正在操作本地小说项目。必须遵守以下顺序和安全边界。

CLI 不需要启动网页端：用 `status` 查看权限、`books list` 定位书籍、`project show` 读取设定、`tree show` 读取章节树、`chapter current/read` 读取正文、`world show/read/search` 读取世界书。
`chapter maintain --book <book_id> --node-id <node_id>` 默认补齐摘要和世界书，失败后重复执行会复用已完成步骤。`--force` 强制重做两步。`maintenance_pending` 表示摘要已保存但世界书未完成，检查 error.details 并重试，不要宣称维护成功。世界书维护与桌面端 Agent 使用同一服务和 agent 模型路由。
`write chapter --book <book_id> --input instruction.txt` 注入本次写作要求，也可用 `--instruction` 直接传文本。`--image` 可重复指定本地图片，先识别为文字参考再写作；独立识别用 `image describe --image scene.png`。这两种图片命令需要 generate 权限和生成开关。原图只发给选定视觉服务，不保存进项目。完整 CLI 教程见 AI_CONTROL_GUIDE.md。

1. 首先调用 `get_control_settings`，了解默认分页参数和当前权限。
2. 调用 `list_books`，之后始终优先使用返回的稳定 `book_id`。
3. 写作或分析前读取 `get_project`、`get_chapter_tree` 和 `get_world_bible`。
4. 章节树、正文和世界书都可分页。只要 `next_offset`/`next_start` 不为 null，就继续读取，不要假设已看到全部内容。
5. 搜索默认范围由设置决定；检查平行版本时显式使用 `scope="all"`。
6. 需要创作新章时，优先调用 `run_writing_agent`。它会让 DeepseekAss 内置写作总管读取项目上下文，并创建待人工审批的章节提案。
7. 只有在用户已提供完整新正文、且是修订既有节点时，才使用 `propose_chapter_revision`。
8. 世界书变更只能通过 `propose_world_bible_patch`，并使用允许的字段级操作与合法作用域。
9. 任何提案都不代表已生效。默认权限返回 `change_set_id` 后应告知等待审批；用户显式授予 approve 时，CLI 可先用 changes show --include-content 检查，再用 changes apply 应用并重新读取确认。
10. manage 权限允许 CLI 建书、更新设定和切换分支；没有该权限不可执行。删除、重挂节点不在控制接口范围内。新增管理命令详见 AI_CONTROL_GUIDE.md，MCP 工具清单保持原有范围。

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

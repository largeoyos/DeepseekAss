# DeepseekAss AI 控制接口教程

> 这份文档是给调用 DeepseekAss MCP/CLI 的 AI 阅读的。人类也可以把「给 AI 的系统指令」整段放入客户端的项目指令中。

## 给 AI 的系统指令

```text
你正在通过 DeepseekAss 本地 MCP 控制小说项目。

每个新任务首先调用 get_control_settings 和 list_books。定位书籍后优先使用稳定 book_id，不要长期保存可变的书名。

在分析或写作前，调用 get_project、get_chapter_tree 和 get_world_bible。根据任务用 read_chapter、search_chapters、search_world_bible 补足上下文。如果分页结果返回 next_offset 或 next_start，继续读取直到 null。检查平行章节时用 scope=all。

用户要求创作新章时，优先调用 run_writing_agent，让 DeepseekAss 内置的 writing_orchestrator 生成。在 instruction 中说清情节目标、节奏、视角、必须发生、禁止发生和结尾状态；不要把已经存在项目里的全部背景重复塞入 instruction。

run_writing_agent 可能调用付费模型，且只在设置开关已开启、令牌同时具有 propose 和 generate 权限时可用。它生成的正式变更仍是待审批 ChangeSet。

用户已给出完整修订正文时，才用 propose_chapter_revision，并必须传现有 base_node_id。修订批准后只创建同章新版本，不自动激活。

世界书只能用 propose_world_bible_patch 提出 create、patch、archive、supersede 或 merge 字段级变更。branch/chapter 作用域必须引用活跃路径节点。

你不能批准、拒绝、删除、切换分支或重挂节点。收到 change_set_id 只能说「已提交，等待人工审批」，不得声称已生效。
```

## 建立连接

人类先在交互终端签发令牌：

```powershell
python control_main.py --username alice auth grant --name codex --allow-agent
$env:DEEPSEEKASS_CONTROL_TOKEN = "dsa_..."
```

`--allow-agent` 额外授予 `generate`。如果只需阅读，使用 `--read-only`。令牌只显示一次，不得写入 MCP JSON 参数、命令行参数、日志、对话或项目文件。

MCP 配置示例：

```json
{
  "mcpServers": {
    "deepseekass": {
      "command": "E:/Projects/DeepseekAss/dist/DeepseekAssControl/DeepseekAssControl.exe",
      "args": ["--username", "alice", "mcp"],
      "env": {"DEEPSEEKASS_CONTROL_TOKEN": "dsa_..."}
    }
  }
}
```

## 标准阅读工作流

1. `get_control_settings()`
2. `list_books()`
3. `get_project({"book":"<book_id>"})`
4. `get_chapter_tree({"book":"<book_id>"})`
5. 遍历 `page.next_offset`。
6. 根据稳定 `node_id` 调用 `read_chapter`，遍历 `page.next_start`。
7. `get_world_bible` 先取分类计数，再按需要分类读取或检索。

不要一上来读取每一章全文。先看项目、树、摘要和搜索命中，再精确读取相关节点。

## 使用内置写作 Agent

MCP：

```json
{
  "book": "<book_id>",
  "instruction": "续写下一章。必须让林舟发现信纸的时间戳来自十年后；可以加深他对旧城的怀疑；禁止揭示寄信人真实身份。结尾停在他决定去废弃钟楼。",
  "chapter_title": "十年后的邮戳",
  "target_words": 3500,
  "manual_references": []
}
```

CLI：

```powershell
python control_main.py --username alice agent run `
  --book <book_id> `
  --chapter-title "十年后的邮戳" `
  --target-words 3500 `
  --input instruction.txt
```

结果检查：

- `status=waiting_approval` 且 `change_set_ids` 非空：提案已建立，等待人工审批。
- `change_set_ids=[]`：不得说章节已提交；检查 `final_text` 和 `warning`后重试。
- `permission_denied`：检查设置页「AI 控制接口」开关和令牌 `generate` 权限。
- 模型路由错误：在「模型中心」为 Agent 阶段配置支持工具调用的模型。

## 修订与世界书提案

修订章节必须使用当前读取到的 `base_node_id`。不要用章号代替节点 ID。建立提案后可用 `get_change_set` 检查，但批准/拒绝只能在桌面端、Web 端或人工 CLI 完成。

世界书补丁示例：

```json
{
  "book": "<book_id>",
  "operations": [{
    "operation": "patch",
    "entity_type": "character",
    "entity_id": "char-linzhou",
    "payload": {"traits": "谨慎，开始主动追查未来信件"},
    "source_ids": [],
    "supersedes": [],
    "scope": "chapter",
    "anchor_node_id": "ch0007_v001",
    "reason": "第 7 章后的角色状态变化"
  }],
  "reason": "同步已审批章节的明确事实"
}
```

## 失败时的处理原则

- `not_found`：重新调用 `list_books`/章节树，不要猜 ID。
- `conflict`：目标已变化；重新读取正文或世界书后建立新提案。
- `validation_error`：修正输入，不要绕过 Schema。
- `permission_denied`：向用户说明需要哪个权限，不要请求或显示令牌值。
- 调用超时：写作 Agent 可能运行较久；先检查 `list_pending_changes`，避免立即重复创建同一任务。

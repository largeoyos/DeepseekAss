# DeepseekAss AI 控制接口教程

> 这份文档是给调用 DeepseekAss MCP/CLI 的 AI 阅读的。人类也可以把「给 AI 的系统指令」整段放入客户端的项目指令中。

## 给 AI 的系统指令

```text
你正在通过 DeepseekAss 本地 CLI 或 MCP 控制小说项目。CLI 可独立运行，不需要启动网页端或桌面端。

每个新任务首先调用 get_control_settings 和 list_books。定位书籍后优先使用稳定 book_id，不要长期保存可变的书名。

在分析或写作前，调用 get_project、get_chapter_tree 和 get_world_bible。根据任务用 read_chapter、search_chapters、search_world_bible 补足上下文。如果分页结果返回 next_offset 或 next_start，继续读取直到 null。检查平行章节时用 scope=all。

用户要求创作新章时，优先调用 run_writing_agent，让 DeepseekAss 内置的 writing_orchestrator 生成。在 instruction 中说清情节目标、节奏、视角、必须发生、禁止发生和结尾状态；不要把已经存在项目里的全部背景重复塞入 instruction。

run_writing_agent 可能调用付费模型，且只在设置开关已开启、令牌同时具有 propose 和 generate 权限时可用。它生成的正式变更仍是待审批 ChangeSet。

用户已给出完整修订正文时，才用 propose_chapter_revision，并必须传现有 base_node_id。修订批准后只创建同章新版本，不自动激活。

世界书只能用 propose_world_bible_patch 提出 create、patch、archive、supersede 或 merge 字段级变更。branch/chapter 作用域必须引用活跃路径节点。

默认令牌只能读取和提案。只有用户明确授予 approve 时，CLI 才可用 changes apply 应用经检查的提案；manage 才可建书、修改设定和切换分支。没有对应权限时等待用户审批。收到 change_set_id 不代表已生效，必须重新读取确认。删除、重挂节点不在此接口范围内。
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

### CLI 从读取到写作的完整示例

可由用户在桌面端完成初始化，也可按下文的 CLI 初始化流程创建独立账号、配置模型和建书。AI 使用环境变量中的令牌，不读取其他账号的密码和加密文件。每条命令独立运行；没有隐含的“当前选中书籍”，始终传 `--book`。

```powershell
$env:DEEPSEEKASS_USERNAME = "alice"
# 令牌由用户通过运行环境提供。
python control_main.py status
python control_main.py books list
python control_main.py project show --book "<book_id>"
python control_main.py tree show --book "<book_id>" --include-summaries --limit 100
python control_main.py chapter current --book "<book_id>" --max-chars 20000
python control_main.py chapter read --book "<book_id>" --node-id "<node_id>" --start 0 --max-chars 20000
python control_main.py chapter search --book "<book_id>" --query "钟楼" --scope all
python control_main.py world show --book "<book_id>"
python control_main.py world show --book "<book_id>" --category character --limit 100
python control_main.py world search --book "<book_id>" --query "林舟"
python control_main.py world read --book "<book_id>" --entity-id "<entity_id>"
```

`chapter current` 指活跃剧情路径的最后一章，和桌面正在浏览哪个节点无关。`world show` 不传分类时只返回概览；分类包括 `character`、`location`、`timeline`、`plot_thread`、`world_rule`、`foreshadowing`、`setting`、`dialogue`、`fact`。继续分页时原样保留其他参数，只更新返回的 `next_offset` 或 `next_start`。

如果只想看可视化树，使用 `tree show --book "<book_id>" --output tree`，星号表示活跃节点。该格式为纯文本且仍遵循分页；自动化通常应使用默认 JSON。

把写作信息保存在 UTF-8 文件 `instruction.txt`，例如：

```text
情节目标：林舟在旧城收到一封未来来信。
视角与文风：第三人称限知，克制的悬疑氛围。
人物与场景补充：本次让看守出现，但不透露他的身世。
必须发生：林舟辨认出时间戳来自十年后。
禁止发生：不能揭示寄信人的真实身份。
结束状态：林舟决定去废弃钟楼。
```

```powershell
python control_main.py write chapter --book "<book_id>" --chapter-title "十年后的邮戳" --target-words 3500 --input instruction.txt
# 简短信息也可以直接注入，两种输入互斥：
python control_main.py write chapter --book "<book_id>" --instruction "继续下一章，推进钟楼线索，保留寄信人秘密。"
# 从标准输入读取：
Get-Content -Raw -Encoding UTF8 instruction.txt | python control_main.py write chapter --book "<book_id>" --input -
python control_main.py changes list --book "<book_id>"
python control_main.py changes show --book "<book_id>" "<change_set_id>"
```

注入的信息用于本次写作，不会覆盖永久项目设定；内置 Agent 自行读取书籍背景、作者规划、章节树及世界书。`--reference` 可重复传入内部参考 ID。`agent run --input ...` 是仍可使用的兼容入口。

写作返回待审批提案后，人工可在桌面端审批，或运行 `changes approve --book "<book_id>" "<change_set_id>"`，按提示输入密码并确认变更 ID。具有用户显式授予的 `approve` 权限时，AI 可先检查完整正文，再用 `changes apply` 应用。再次读取章节树确认正式内容的状态。

所有命令默认输出 UTF-8 JSON：成功为 `{"ok":true,"schema_version":1,"data":{...}}`，失败为 `{"ok":false,"schema_version":1,"error":{"code":"...","message":"..."}}`。退出码：0 成功、2 参数/校验错误、3 权限错误、4 未找到/冲突、1 内部错误。`--pretty` 位于主命令前。不要根据退出码 0 推断提案已经应用；必须检查 `status` 和 `change_set_ids`。

### 图片识别与写作参考

在模型中心为确实支持图片输入的模型勾选「看图」。图片识别使用互动阶段模型（传入 `--book` 时尊重该书互动阶段路由）；支持看图就直接调用它，否则使用「图片 API」页的图片识别备用配置。备用服务必须支持 OpenAI Chat Completions 图片输入，生图专用模型不能用于识别。能力声明不代表远程服务已经验证，服务不支持时会报告错误。

```powershell
python control_main.py image describe --book "<book_id>" --image "scene.png" --instruction "描述建筑、服饰与光线"
python control_main.py write chapter --book "<book_id>" --input instruction.txt --image "scene.png" --image "costume.jpg"
```

两者均需要 `generate` 权限及生成开关；写作另需 `propose`。图片可重复指定，最多 4 张，单张最多 10 MB，合计最多 20 MB，支持 PNG/JPEG/WebP/GIF。GIF 的多帧识别取决于服务，推荐静态图片。所选图片将发送到选定视觉服务；只有识别文本进入写作要求，图片内容不自动成为世界书事实。原图不写进项目或审计日志。`image describe` 返回 `source=current_model` 或 `image_api`、实际 `model`、`content` 和用量；原生视觉调用失败不会再把图片发送到备用服务。

桌面端的「图片参考」和章节情节下的「从图片提取写作参考」使用同一路由，允许检查和编辑识别文本后再加入输入。当前版本不是图片聊天附件功能，后续模型接收的是提取出的文字参考。

### MCP 阅读顺序

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

`world audit --book <book_id>` 会检查实体一致性和当前活跃路径的章节快照。`missing_snapshot_node_ids` 非空表示这些章节尚未同步到世界书，不能仅凭正文已保存宣称记忆更新完成。对这些节点执行 `chapter maintain` 后重新审计；网络失败由模型网关处理重试，世界书 JSON 修复不会再次重试失败的网络请求。

修订章节必须使用当前读取到的 `base_node_id`。不要用章号代替节点 ID。建立提案后可用 `get_change_set` 检查；CLI 的 `changes show --include-content` 返回完整提案正文。默认令牌仍需人工审批，显式 `approve` 令牌可使用 `changes apply`。

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

## 纯 CLI 初始化和完整写作流程

以下管理命令是 CLI 入口，MCP 工具清单保持原有读取、提案和写作工具。原有令牌的权限不会自动扩大。

| 权限 | 用途 | 签发参数 |
| --- | --- | --- |
| read / propose | 读取与提交提案，默认权限 | 默认 |
| generate | 调用模型；还需开启生成开关 | `--allow-agent` |
| manage | 建书、永久设定、模型配置、生成开关、分支和记忆维护 | `--allow-manage` |
| approve | 应用或拒绝经检查的提案 | `--allow-approve` |

用户授权创建独立账号后，调用方通过环境变量提供自己生成的密码。不要把密码作为命令行参数。`auth register` 注册账号并返回一次令牌，默认有效期一天；已有账号使用 `auth grant`。调用方应解析 JSON，将 `data.token` 仅传入后续进程的 `DEEPSEEKASS_CONTROL_TOKEN`，不要记录认证输出。

```powershell
python control_main.py --username ai_writer auth register --password-env NEW_ACCOUNT_PASSWORD --allow-agent --allow-manage --allow-approve
# 后续进程环境：DEEPSEEKASS_USERNAME=ai_writer，DEEPSEEKASS_CONTROL_TOKEN=返回的令牌
python control_main.py models configure --input -
python control_main.py models status
python control_main.py control generation --enabled
python control_main.py project create --title "新小说" --input settings.json
python control_main.py project set --book "<book_id>" --input settings.json
```

`models configure --input -` 从标准输入接收模型 JSON，并加密保存到该账号。支持模型中心 v2 格式，也支持 `{"text":{"base_url":"https://服务地址/v1","api_key":"密钥","model":"模型名"}}`。这是替换配置操作；已有多个模型时应提供完整 v2 配置。`models status` 只返回模型、能力、路由和是否配置密钥，不返回密钥。

`settings.json` 是非空 JSON 对象，可包含 `author`、`protagonist_bio`、`background_story`、`writing_demand`、`author_plan`、`genre`、`style_tone`，值必须是字符串。`project set` 只更新传入字段，更新前创建快照。

```powershell
python control_main.py agent plan --book "<book_id>" --input instruction.txt --chapter-title "第一章" --target-words 1600
python control_main.py agent plan-show --book "<book_id>" --plan-id "<plan_id>"
python control_main.py agent runs --book "<book_id>" --limit 20
python control_main.py agent run-show --book "<book_id>" --run-id "<run_id>"
# 将返回的 rendered 计划加入本次 instruction.txt，再生成正文：
python control_main.py write chapter --book "<book_id>" --input instruction.txt --chapter-title "第一章" --target-words 1600
python control_main.py changes show --book "<book_id>" "<change_set_id>" --include-content
python control_main.py changes apply --book "<book_id>" "<change_set_id>" --operation-id "<operation_id>"
python control_main.py chapter current --book "<book_id>"
python control_main.py chapter maintain --book "<book_id>" --node-id "<node_id>"
python control_main.py world show --book "<book_id>" --category character
python control_main.py tree activate --book "<book_id>" --node-id "<node_id>"
python control_main.py tree activate --book "<book_id>" --tree-id "<tree_id>"
python control_main.py world rebuild --book "<book_id>"
```

`agent run-show` 可查看运行状态、模型用量与实际工具调用，便于确认 Agent 是否读取了项目和世界书。`agent plan` 根据活跃路径的下一章目标调用真实规划模型，保存计划但不生成或批准正文。`chapter maintain` 将摘要和世界书绑定到指定版本，需 `manage`、`generate`、`read` 和生成开关。`tree activate` 通过原章节分支服务切换活跃路径，同步摘要和世界书，并保留快照；`world rebuild` 仅从现有记录重建活跃记忆，不调用模型。

`chapter maintain` 默认补齐未完成步骤：复用已有有效摘要和完整世界书快照。摘要使用 `extraction` 路由；世界书复用桌面端 Agent 维护服务，使用 `agent` 路由，两者均遵守书籍的路由覆盖。与桌面端章节树的“强制提取世界书”不同，重复维护不必重复调用模型。

- 成功时返回 `status: completed`；`summary_reused` 和 `world_report.reused_snapshot` 表示是否复用了已完成步骤。
- 摘要已完成、世界书失败时，CLI 返回非零退出码、`ok: false` 和 `error.code: maintenance_pending`。`error.details` 保留已保存摘要、任务 ID 和维护错误；不要当作全程成功。
- 再次执行相同命令即可继续，已成功的摘要不会重做，世界书待办任务也可在桌面端重试。失败的强制刷新仍会继续刷新世界书。
- 要主动重做两步，使用 `chapter maintain --book "<book_id>" --node-id "<node_id>" --force`。失败后重试请省略 `--force`，避免再次重做摘要。
- 新快照记录正文哈希及提取完整性，正文变化或分块提取不完整时不会直接复用。旧版无哈希的摘要/快照按已有记录兼容复用；修改设定、偏好、模型或旧版正文后需要重新提取时，请使用 `--force`。

已有完整正文（用户提供或外部 AI 创作）可用 `chapter propose --book "<book_id>" --chapter-num 3 --title "第三章" --parent-id "<node_id>" --input chapter.txt` 注入新章提案，不调用模型。省略父节点时在提案创建时绑定当前路径的上一章，不会等到应用时重新猜测。拒绝错误或重复提案用 `changes discard --book "<book_id>" "<change_set_id>"`，需要 approve 权限。

应用提案前检查章号、父节点和完整正文；`--operation-id` 可重复指定，省略会应用该提案所有待应用操作。`changes apply` 复用原提案校验、冲突检查和快照机制，不跳过审批层。版本修订应用后不会自动激活，需另行 `tree activate`。

五章真实模型验收脚本及报告说明见 `scripts/LIVE_NOVEL_TEST.md`。

## 失败时的处理原则

- `not_found`：重新调用 `list_books`/章节树，不要猜 ID。
- `conflict`：目标已变化；重新读取正文或世界书后建立新提案。
- `validation_error`：修正输入，不要绕过 Schema。
- `permission_denied`：向用户说明需要哪个权限，不要请求或显示令牌值。
- 调用超时：写作 Agent 可能运行较久；先检查 `list_pending_changes`，避免立即重复创建同一任务。

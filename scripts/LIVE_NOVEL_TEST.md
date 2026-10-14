# 五章小说真实模型实测

```powershell
python scripts/live_novel_test.py
```

脚本通过已有 `control_main.py` 注册独立测试账号，自动签发一天有效的 read/propose/generate/manage/approve 令牌。所有建书、设定、规划、正文生成、提案应用、世界书和章节树操作均通过真实 CLI 子进程执行。没有模拟模型，不访问其他账号的数据。

自己生成的账号密码保存在被 .gitignore 忽略的 `users/.cli-novel-test.dpapi`，由当前 Windows 用户的 DPAPI 加密；程序再次运行会使用该账号，令牌仅放在子进程环境，不写入报告。此自动凭据存储目前只支持 Windows。

在项目 `.env` 配置 DEEPSEEK_API_KEY、DEEPSEEK_BASE_URL、DEEPSEEK_MODEL。脚本用 `models configure --input -` 将这些配置加密写入自己的测试账号。密钥不出现在报告或命令行参数里。如果账号已经在模型中心配置好，也可不提供环境密钥，直接复用该测试账号的配置。

## 流程和检查

1. `auth register/grant` 创建或登录自己的测试账号，`models configure/status` 检查模型路由，`control generation --enabled` 开启该测试账号的生成权限。
2. `project create/set` 建立带独立后缀的《最后一班潮汐邮船》，注入人物、背景、写作要求和五章作者规划。
3. `world propose-patch` 与 `changes apply` 建立初始人物和场景，`world show/read/rebuild` 验证查看和重建。
4. 每章通过 `agent plan` 调用实际规划模型，`agent plan-show` 读取已保存计划；将计划传入 `write chapter`，由内置 Agent 读取项目上下文并提交正文提案。
5. `changes show --include-content` 检查章号、父节点、正文长度；只应用匹配的新章操作，保留原 ChangeSet 校验、冲突检测和快照机制。
6. `chapter maintain` 实际生成版本摘要并提取世界书，之后 `chapter current`、`world show` 读取结果。
7. 第二章用 `chapter propose-revision` 创建修订，验证应用后没有自动切换；通过 `tree activate` 在修订版、原版、修订版间往返，检查原文保留和摘要恢复。第三章必须接在修订版之后。
8. 完成五章后逐页读取正文，导出 `完整小说.md`；保存章节树和全分类世界书，检查世界书实体搜索、正文搜索和一致性报告。
9. 实际阅读全文，检查情节闭合、人物状态、伏笔回收与章节间连续性，另写验收记录。

## 产物和恢复

默认目录 `artifacts/five-chapter-cli-test/`；用 `--output` 指定不同目录可新建另一本测试书。小说仍正常保存在应用的加密书架中，报告为可阅读的导出副本。

报告记录每条 CLI 命令及退出码，不记录认证响应、密码或密钥。`prepared` 仅表示方案准备；`running` 表示运行中；`failed` 表示未完成；`blocked_missing_model_credentials` 表示尚无可用模型配置。`generated_pending_editorial_review` 表示五章及结构检查完成，仍需阅读全文验收。不能把预先准备的剧情方案视为模型生成的正文。

人工验收后的 `completed_with_failures` 表示实测已经整理为报告，但存在未通过项；应查看 `remaining_failure`，不能解释为全部成功。`prose_completed_chapters` 与 `maintenance_completed_chapters` 分别记录正文和记忆维护进度。`world-rebuild-final.json` 和 `world-audit.json` 可交叉检查缺失快照。

再次运行相同目录会复用书籍、已保存计划、已有生成提案和已完成章节。修复审查记录 reviewed-change-N.json 可指定替代提案 ID，原模型产物保持不变。若断点记录中的已写入章节仍在当前末端，脚本只恢复该章记忆维护；第二章涉及版本切换，若中途停止则需先检查现有分支。断点与现有章节不符时停止，避免重复收费生成。失败不会删除证据，不会盲目重复调用。测试账号的生成开关保留开启，每次令牌一天后到期；令牌不会保存到磁盘。

仅准备方案，不注册账号、不调用模型：

```powershell
python scripts/live_novel_test.py --prepare-only
```

## 使用测试账号查看结果

`python scripts/test_account_cli.py books list` 自动使用测试账号运行原 CLI，每次临时令牌在命令结束后撤销。可把 books list 换成 project show、world show、agent run-show 等命令。要在桌面端登录，在本机运行 `python scripts/test_account_cli.py --show-login` 查看自己测试账号的用户名和密码；无需把它们发到聊天。

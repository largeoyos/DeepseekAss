# DeepseekAss 架构说明

## 分层

- `ui/`：PyQt 展示、输入采集和事件反馈。
- `core/app_services.py`：章节、续写、角色扮演、世界书和导入导出的应用服务。
- `core/context_assembler.py`：按预算和加载策略构建可审计的模型上下文。
- `core/workspace.py`、`core/repositories.py`：兼容旧目录的书籍工作区与仓储接口。
- `core/storage.py`：统一明文/加密原子存储。
- `core/snapshots.py`：加密、内容寻址的整书版本。
- `core/agent_tools.py`：默认关闭、无直接文件系统权限的 Agent 工具边界。

`NovelManager` 暂时保留为兼容门面。旧调用可以继续使用，新增能力通过
`BookWorkspace`、应用服务和 Repository 逐步接管。

## 数据兼容

旧书首次加载时只会新增：

```text
<book>/.deepseekass/manifest.json.enc
<book>/.deepseekass/context_policies.json.enc
<book>/.deepseekass/snapshots/
```

原有 `meta.json.enc`、章节、摘要、世界书和生成记录不会被移动或解密。
Manifest 损坏时书籍保持可读，并进入禁止覆盖的保护状态。

## 上下文策略

世界书实体支持：

- `resident`：每次生成都展开正文。
- `auto`：名称、简介或关键词命中时展开正文。
- `manual`：仅在显式引用时展开。

生成前可从小说工具栏打开“上下文预览”，检查每节来源、命中原因、预算和截断量。

## 项目版本

项目版本与章节树并存：

- 章节树表达平行剧情和同章版本。
- 项目版本原子保存章节、摘要、世界书、元数据和内部配置。

快照内容按明文 SHA-256 去重后使用当前用户密钥加密。恢复前会自动创建备份版本，
并在完整校验通过后才保留恢复结果。

## Agent 边界

`ControlledAgentTools` 默认未启用。它只提供领域操作，不提供任意文件系统入口：

- `read_only`：读取章节、搜索、读取世界书、生成上下文报告。
- `draft_write`：额外允许写入隔离草稿目录。
- `confirm_write`：允许创建待确认章节变更；确认后写入并自动保存项目版本。

固定章节生成流程仍由应用代码控制，不由 Agent 替代。

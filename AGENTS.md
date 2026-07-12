# AGENTS.md

本文件为 Codex（Codex.ai/code）在此仓库中工作时提供指引。

## 常用命令

```bash
pip install -r requirements.txt  # 安装依赖
python gui_main.py               # 启动应用
```

依赖：PyQt6, PyQt6-WebEngine, openai, python-dotenv, markdown, python-docx, cryptography。

## 项目概述

基于 PyQt6 的 DeepSeek API 聊天客户端，使用 OpenAI SDK 接入 DeepSeek API。支持角色扮演、小说写作（书架管理、章节版本控制、世界书系统）以及从已有文档续写故事。

## 架构

### 策略模式（核心设计模式）

`strategies/base_strategy.py` 定义抽象基类，`strategies/` 下有三个实现：

- **RolePlayStrategy** — 角色扮演，支持角色/旁白两种回复模式
- **NovelStrategy** — 小说写作，支持自由对话和章节写作两种模式
- **ContinuationStrategy** — 从已有 .txt/.md 文档续写

新增模式三步：在 `strategies/` 下创建策略类，在 `utils/prompts.py` 添加 System Prompt，在 `ui/main_window.py:110` 的 `STRATEGY_OPTIONS` 注册。

### 核心层（`core/`）

- **chat_client.py** — `DeepSeekChatClient` 封装 OpenAI SDK，管理对话历史，支持流式输出、取消和运行时参数调整。与策略解耦，仅调用 `BaseStrategy.get_system_prompt()`。
- **novel_manager.py** — `NovelManager` 管理书架（增删改查）、章节版本控制（多版本保留、活跃版本切换）、剧情摘要、智能前情提要算法（长篇自动压缩早期章节）和生成历史记录。所有文件可选 Fernet 加密。
- **conversation_manager.py** — `ConversationManager` 对话历史的保存/加载/删除（JSON，可选加密）。
- **world_bible.py** — `WorldBible` 数据类，存储角色（别名、关系、动机、弧光）、地点、规则、时间线事件、剧情线、伏笔和关键对话。包含 AI 提取（`extract_and_merge_world_bible`）、去重（`dedup_world_bible_characters/locations`）和 prompt 格式化（`format_world_bible_for_prompt`）。
- **auth_manager.py** — `AuthManager` 用户注册/登录，PBKDF2-SHA256（60 万次迭代）密码哈希和密钥派生，Fernet 加解密。每个用户使用 UUID 目录隔离数据。

### UI 层（`ui/`）

- **main_window.py**（约 3200 行）— 主窗口：模式/策略选择、模型选择、参数滑块（temperature/top_p/max_tokens/frequency_penalty）、QWebEngineView Markdown 渲染、书架面板、小说章节生成流程、世界书查看/编辑、对话导入/导出、文件导出（TXT/MD/HTML/DOCX）。
- **login_dialog.py** — `LoginDialog` 登录/注册对话框，管理 PBKDF2 派生加密密钥。
- **world_bible_dialog.py** — `WorldBibleDialog` 世界书标签页查看/编辑器。
- **continuation_dialogs.py** — 源文档分析、续写方向建议和参数设置对话框。

### 工具模块（`utils/`）

- **prompts.py** — 集中管理所有 System Prompt（角色扮演、旁白模式、小说写作导师、章节写作）。
- **export.py** — 单章/全书/对话导出，支持 TXT/MD/HTML/DOCX 格式，HTML 使用暗色主题模板。
- **summarize.py** — AI 语义分段（在话题转折处分隔）、逐段世界观提取、跨段落合成（`_run_synthesis`）、从提取数据生成小说设定。
- **supplement.py** — 中文字数统计（`count_cn`）和内容扩写（生成字数不足时调用 API 扩写整章）。

### 数据存储

- **bookshelf/** — 每部小说一个目录，包含 `meta.json`（元信息/章节版本）、`plot_summary.txt`（剧情摘要）、`world_bible.json`（世界书）、章节文件和 `.generation_history/`（历史记录）。启用加密时自动附加 `.enc` 后缀。
- **users/** — `users.json`（哈希/盐/dir_id 记录）和每个用户的 UUID 目录，用于隔离对话和书架数据。
- 所有 JSON/文本文件通过 `_encrypt_path()` 模式透明支持 Fernet 加密（由 NovelManager 和 ConversationManager 统一处理）。

### 关键模式

- **加密 I/O 模式**：`_encrypt_path()` 追加 `.enc`，`_read/write_encrypted_json/text` 方法透明处理加密与非加密文件。
- **流式输出**：`chat_stream()` 通过 Generator 产出 token，`StreamSignals` 通过 pyqtSignal 桥接到 UI 线程。
- **智能前情提要**：`NovelManager.load_smart_summary()` 保留最近 N 章完整详情，早期章节使用缓存的 API 压缩摘要。
- **世界书合并**：`extract_and_merge_world_bible()` 调用 LLM 从章节中提取结构化数据，按名称匹配合并到现有 WorldBible。

### Git 工作流

- **分支**：main
- **提交信息**：使用 `feat:` / `fix:` / `refactor:` 前缀，描述修改动机（而非内容），末尾添加 `Co-Authored-By: Codex Opus 4.7 <noreply@anthropic.com>`。

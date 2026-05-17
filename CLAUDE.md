# CLAUDE.md - 交互与编码规范

## 交互原则 (Communication)
- **拒绝废话**：严禁使用 "Great question!", "Certainly!", "Of course!" 等寒暄开头。直接进入正题，只给核心信息。
- **匹配复杂度**：简单问题短答，复杂任务深入。严禁用套话填充字数。
- **明确不确定性**：如果不确定事实、数据或技术细节，必须直说“我不确定”，禁止凭空捏造。
- **执行前选项**：在开始重要任务前，先提供 2-3 个方案，说明取舍，等我确认后再动手。

## 编辑与修改守则 (Editing)
- **保持范围**：只修改我明确要求的部分。禁止“顺手优化”没要求的代码或文字。
- **重大变动确认**：重写、重组或改变语气前，必须停下来说明原因并获得确认。
- **变更总结**：任务完成后，末尾必须简短列出：
  - 修改了什么
  - 保留了什么
  - 后续建议

## 编码规范 (Coding - Karpathy Protocol)
- **先问后猜**：需求或架构不明时，先提问，禁止假设。
- **KISS原则**：始终先实现最简单的可行方案，不要过度设计或添加未要求的抽象。
- **技术栈锁定**：
  - 语言：Python 3.10+
  - 框架：PyQt6 + PyQt6-WebEngine
  - 工具：DeepSeek API (OpenAI SDK)、python-dotenv、markdown
- **破坏性操作隐性锁定**：删除文件、覆盖数据库或执行部署前，必须在当前对话中获得明确的“是”或“确认”。

## 记忆与状态 (Memory)
- 维护并参考项目中的 `MEMORY.md` 记录重要决策。
- 维护并参考 `ERRORS.md` 记录失败尝试，避免重复踩坑。
- 当我说 "session end" 时，提供本次会话的简短总结。
本文件为 Claude Code (claude.ai/code) 在处理此仓库代码时提供指导。

#文档信息
## 命令

```powershell
# 安装依赖
pip install -r requirements.txt

# 运行 GUI 界面 (PyQt6)
python gui_main.py
```

## 项目概述

一个针对 **DeepSeek API**（兼容 OpenAI 的 SDK）的多功能聊天客户端。通过**策略模式**支持多种聊天模式，使用 **PyQt6 GUI**（`gui_main.py`）。

## 架构

### 入口点
- `gui_main.py` — PyQt6 GUI 入口。调用 `ui.main_window.run_gui()`。

### 核心 (`core/`)
- **`chat_client.py`** (`DeepSeekChatClient`) — 封装 `openai.OpenAI` 客户端，管理对话消息列表，支持流式（`chat_stream`）和非流式（`chat`）调用。通过 `switch_strategy()` 可在运行时切换策略。暴露 `raw_client` 供其他组件（如 `NovelManager`）直接访问 API。
- **`novel_manager.py`** (`NovelManager`) — 完整的小说生命周期：创建/删除书籍、保存章节版本（支持多版本管理和活动版本选择）、情节摘要管理、**智能摘要压缩**（当小说超过阈值时通过 API 调用自动压缩早期章节）、生成历史追踪。
- **`conversation_manager.py`** (`ConversationManager`) — 角色扮演对话的保存/加载/删除为 JSON 文件。

### 策略 (`strategies/`)
- **`base_strategy.py`** (`BaseStrategy` 抽象基类) — 提供 `get_name()`、`get_system_prompt()` 以及推荐模型/temperature/top_p/max_tokens/frequency_penalty 等可覆写属性的接口。
- **`role_play_strategy.py`** — 角色描述、故事背景、回复模式（第一人称角色 vs 第三人称叙述者）。
- **`novel_strategy.py`** — 小说标题、章节标题、主角简介、世界观设定、写作要求。章节模式标志可启用自动章节生成工作流。
- **`continuation_strategy.py`** — 续写小说模式，基于外部文档续写后续章节，复用 `NovelManager` 保管章节。

### 工具 (`utils/`)
- **`prompts.py`** (`Prompts`) — 集中管理系统提示词常量。要添加新模式？在此添加常量。

### 配置 (`config.py`)
- 从 `.env` 文件中读取 `DEEPSEEK_API_KEY` 和 `DEEPSEEK_BASE_URL`（通过 python-dotenv）。GUI 支持运行时输入 API 密钥。

### UI (`ui/main_window.py`)
- ~1900+ 行的 PyQt6 GUI，包含：深色主题、QWebEngineView Markdown 渲染、通过后台线程 + pyqtSignal 实现流式输出、左侧控制面板（模式/模型/参数）、角色扮演面板（角色/背景/回复模式）、小说面板（书架、章节生成、版本管理对话框）、续写小说面板（文件选择、续写参数）、对话历史保存/加载。

## 关键设计决策

- **策略模式** — 在运行时切换 `DeepSeekChatClient._strategy` 以改变行为和系统提示词。新策略在 `ui/main_window.py:STRATEGY_OPTIONS` 中注册。
- **流式输出** — GUI 使用 `threading.Thread` + `pyqtSignal` 实现非阻塞 API 调用。
- **章节版本管理** — 每个章节可拥有多个版本；一个活动版本驱动情节摘要。摘要压缩使用 DeepSeek API 自身来压缩早期章节。
- **智能摘要** — `NovelManager.load_smart_summary()` 对短篇小说返回完整摘要，对长篇小说压缩早期章节并保留最近 N 个章节的详细信息。
- **对话持久化** — 角色扮演对话以 JSON 格式保存在 `conversations/` 目录中。
- **书架数据** — 存储在 `bookshelf/` 目录中（已加入 gitignore）。每本小说是一个子目录，包含 `meta.json`、`plot_summary.txt`、章节 `.txt` 文件和 `.generation_history/`。

## 依赖项

- `openai>=1.0.0`（DeepSeek API 兼容 OpenAI）
- `python-dotenv`（加载 `.env` 文件）
- `PyQt6>=6.5.0` + `PyQt6-WebEngine>=6.5.0`（GUI）
- `markdown>=3.4.0`（Markdown 转 HTML 渲染）

## 识图能力 (Vision)

项目附带 `vision.js`（Node.js 脚本），利用 vision 模型识别图片内容，通过文字描述让无 vision 能力的模型"看懂"图片。

用法：
```bash
node vision.js <图片路径> [问题]
node vision.js --url <图片链接> [问题]
```

**何时触发**：当用户消息中包含本地图片路径或截图时，优先使用 `node vision.js` 读取图片内容，而不是用 Read 工具加载图片（Read 工具读取图片的视觉呈现质量不稳定）。

**配置**：环境变量 `VISION_BASE_URL`、`VISION_API_KEY`、`VISION_MODEL` 可覆盖默认值。默认连接 `https://jeniya.cn/v1`，模型 `gemini-3.1-pro-preview`。


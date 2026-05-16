# DeepSeek 多功能聊天客户端

基于 PyQt6 的 DeepSeek API 聊天客户端，支持角色扮演、小说写作与续写，配备书架管理、世界书系统、智能摘要等深度写作功能。

## 功能一览

### 🎭 角色扮演
- 自定义角色描述、故事背景
- 第一人称 / 第三人称叙述切换
- 对话历史保存、加载、导出

### 📚 小说写作
- **书架管理**：多部小说项目管理、章节版本控制
- **章节续写**：自动前情提要注入 + 智能摘要压缩
- **世界书系统**：从已生成章节自动提取角色、地点、规则、剧情线，防止设定矛盾
- **字数控制**：设定目标字数，生成不足自动补充
- **分段摘要**：对参考文档按标题/段落分段生成摘要
- **导出**：支持 TXT / MD / HTML / DOCX 格式导出单章或全书

### 📄 续写小说
- 支持源文档（.txt / .md）或文件夹
- **分析流程**：AI 自动提取核心设定、角色关系、剧情概要
- **两种续写模式**：
  - 自由续写：AI 给出 3-5 个发展方向，用户选择后生成
  - 指定续写：用户自定义剧情方向后生成

## 快速开始

```bash
# 安装依赖
pip install -r requirements.txt

# 运行
python gui_main.py
```

首次启动会提示输入 DeepSeek API Key，也可在项目根目录创建 `.env` 文件：

```
DEEPSEEK_API_KEY=your_api_key_here
DEEPSEEK_BASE_URL=https://api.deepseek.com
```

## 技术栈

| 层 | 技术 |
|---|---|
| 语言 | Python 3.10+ |
| GUI | PyQt6 + PyQt6-WebEngine |
| API | OpenAI SDK（兼容 DeepSeek API） |
| 存储 | JSON 文件（书架 / 对话 / 世界书） |

## 项目结构

```
├── gui_main.py                  # 入口
├── config.py                    # 配置（API Key / Base URL）
├── core/
│   ├── chat_client.py           # API 客户端（消息管理 + 流式输出）
│   ├── novel_manager.py         # 小说管理器（书架 / 章节 / 摘要）
│   ├── conversation_manager.py  # 对话历史管理器
│   └── world_bible.py           # 世界书系统（设定提取与合并）
├── strategies/
│   ├── base_strategy.py         # 策略抽象基类
│   ├── role_play_strategy.py    # 角色扮演策略
│   ├── novel_strategy.py        # 小说写作策略
│   └── continuation_strategy.py # 续写小说策略
├── ui/
│   ├── main_window.py           # 主窗口（~3200 行）
│   ├── world_bible_dialog.py    # 世界书编辑对话框
│   └── continuation_dialogs.py  # 续写分析/方向选择对话框
├── utils/
│   ├── prompts.py               # 集中管理的 System Prompt
│   ├── export.py                # 导出（TXT / MD / HTML / DOCX）
│   ├── summarize.py             # 分段摘要
│   └── supplement.py            # 字数不足自动补充
└── bookshelf/                   # 书架数据（自动创建，已 gitignore）
    └── <小说名>/
        ├── meta.json            # 元信息（设定 / 章节版本）
        ├── plot_summary.txt     # 剧情摘要
        ├── world_bible.json     # 世界书数据
        └── .generation_history/ # 生成历史记录
```

## 架构说明

采用**策略模式**设计，不同聊天模式作为独立策略类实现，运行时可通过 UI 下拉框切换。核心客户端 `DeepSeekChatClient` 与具体模式解耦，只依赖 `BaseStrategy` 抽象接口。

新增模式只需：
1. 在 `strategies/` 下创建新策略类（继承 `BaseStrategy`）
2. 在 `Prompts` 中添加对应的 System Prompt
3. 在 `STRATEGY_OPTIONS` 注册（`ui/main_window.py`）

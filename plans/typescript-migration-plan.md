# TypeScript 架构重构方案

> 基于 PyQt6 + Python 的 DeepSeek 聊天客户端 → Tauri v2 + React + TypeScript 桌面应用

---

## 1. 目标架构总览

```
┌──────────────────────────────────────────────────────────┐
│                    Tauri Shell                           │
│  ┌──────────────────────┐  ┌──────────────────────────┐  │
│  │   Rust Core Layer    │  │   Web Frontend (React)   │  │
│  │                      │  │                          │  │
│  │  • 文件系统操作      │◄─┤  • 聊天界面              │  │
│  │  • Fernet 加解密     │  │  • 书架管理              │  │
│  │  • PBKDF2 密钥派生   │  │  • 世界书编辑器          │  │
│  │  • 用户数据隔离      │  │  • 参数面板              │  │
│  │  • IPC 命令暴露      │  │  • Markdown 渲染         │  │
│  │                      │  │  • 导出对话框            │  │
│  └──────────────────────┘  └──────────────────────────┘  │
│                            ┌──────────────────────────┐  │
│                            │   Shared Logic (TS)      │  │
│                            │                          │  │
│                            │  • 策略模式 (strategies/)│  │
│                            │  • AI 分段 (summarize/)  │  │
│                            │  • 导出生成 (export/)    │  │
│                            │  • 世界书提取/合并       │  │
│                            │  • 智能前情提要          │  │
│                            └──────────────────────────┘  │
└──────────────────────────────────────────────────────────┘
```

### 技术栈选型

| 层 | 技术 | 选型理由 |
|----|------|---------|
| 桌面壳 | **Tauri v2** | 打包 5MB，Rust 原生性能，安全沙箱 |
| 前端框架 | **React 18** | 组件化成熟度最高，生态完善 |
| 语言 | **TypeScript 5.x** | 严格类型，与策略模式亲和 |
| 状态管理 | **Zustand** | 轻量，无样板代码，对 PyQt6 的 mutable 风格自然过渡 |
| 样式 | **Tailwind CSS** | 快速原型，设计一致性 |
| UI 组件 | **shadcn/ui + Radix** | 无障碍，可定制，不锁定设计 |
| Markdown | **react-markdown + rehype-highlight** | 替代 QWebEngineView |
| 路由 | **React Router** (可选，用于对话框) | 模态路由管理 |
| API SDK | **openai** npm 包 | 与 python openai 接口一致 |
| DOCX | **docx** npm 包 | 替代 python-docx |
| 构建 | **Vite** | 快速 HMR，Tauri 官方推荐 |

---

## 2. 目录结构设计

```
deepseek-assistant/
├── src-tauri/                    # Rust 层
│   ├── src/
│   │   ├── main.rs               # Tauri 入口
│   │   ├── lib.rs                # 命令注册
│   │   ├── crypto.rs             # Fernet 加密 + PBKDF2
│   │   ├── fs.rs                 # 文件系统命令
│   │   └── models.rs             # 数据序列化
│   ├── Cargo.toml
│   └── tauri.conf.json
│
├── src/                          # React 前端
│   ├── main.tsx                  # 入口
│   ├── App.tsx                   # 根组件 + 路由
│   ├── index.css                 # Tailwind 入口
│   │
│   ├── core/                     # 核心业务逻辑
│   │   ├── chat-client.ts        # DeepSeekChatClient
│   │   ├── novel-manager.ts      # 书架管理 + 版本控制
│   │   ├── conversation-manager.ts # 对话管理
│   │   ├── world-bible.ts        # 世界书数据类 + 提取/合并
│   │   └── auth-manager.ts       # 注册/登录/密钥管理
│   │
│   ├── strategies/               # 策略模式
│   │   ├── base-strategy.ts      # BaseStrategy 接口
│   │   ├── role-play-strategy.ts
│   │   ├── novel-strategy.ts     # 含章节写作 w/ 世界书注入
│   │   └── continuation-strategy.ts
│   │
│   ├── utils/                    # 工具模块
│   │   ├── prompts.ts            # 系统提示词
│   │   ├── export.ts             # 导出 (txt/md/html/docx)
│   │   ├── summarize.ts          # AI 分段 + 世界观提取
│   │   ├── supplement.ts         # 字数统计 + 扩写
│   │   └── genre-styles.ts       # 题材风格配置
│   │
│   ├── hooks/                    # React Hooks
│   │   ├── use-chat.ts           # 聊天流式处理
│   │   ├── use-novel.ts          # 小说写作状态
│   │   ├── use-world-bible.ts    # 世界书 CRUD
│   │   ├── use-encryption.ts     # 加解密调用
│   │   └── use-auth.ts           # 认证状态
│   │
│   ├── stores/                   # Zustand 状态
│   │   ├── chat-store.ts         # 对话状态
│   │   ├── novel-store.ts        # 书架 + 章节状态
│   │   ├── settings-store.ts     # 参数预设 + 模型选择
│   │   └── auth-store.ts         # 登录态 + 加密密钥
│   │
│   ├── components/               # UI 组件
│   │   ├── layout/
│   │   │   ├── MainLayout.tsx    # 主布局 (分栏)
│   │   │   ├── SplitPane.tsx     # 可拖拽分栏
│   │   │   └── TitleBar.tsx      # 自定义标题栏
│   │   │
│   │   ├── chat/
│   │   │   ├── ChatPanel.tsx     # 聊天区主面板
│   │   │   ├── MessageList.tsx   # 消息列表
│   │   │   ├── MessageItem.tsx   # 单条消息 (渲染)
│   │   │   ├── ChatInput.tsx     # 输入框 + 发送
│   │   │   └── MarkdownView.tsx  # Markdown 渲染
│   │   │
│   │   ├── novel/
│   │   │   ├── BookshelfPanel.tsx    # 书架列表
│   │   │   ├── BookCard.tsx          # 单本书卡片
│   │   │   ├── NewBookDialog.tsx     # 新建书对话框
│   │   │   ├── ChapterList.tsx       # 章节列表 + 版本
│   │   │   ├── ChapterEditor.tsx     # 章节写作区
│   │   │   └── GenerationHistory.tsx # 生成历史
│   │   │
│   │   ├── world-bible/
│   │   │   ├── WorldBiblePanel.tsx   # 世界书主面板
│   │   │   ├── CharacterTab.tsx      # 角色标签页
│   │   │   ├── LocationTab.tsx       # 地点标签页
│   │   │   ├── RuleTab.tsx           # 规则标签页
│   │   │   ├── TimelineTab.tsx       # 时间线标签页
│   │   │   └── PlotlineTab.tsx       # 剧情线标签页
│   │   │
│   │   ├── settings/
│   │   │   ├── ParameterPanel.tsx    # 参数滑块面板
│   │   │   ├── ModelSelector.tsx     # 模型选择
│   │   │   ├── StrategySelector.tsx  # 策略选择
│   │   │   └── PresetManager.tsx     # 预设管理
│   │   │
│   │   ├── auth/
│   │   │   ├── LoginDialog.tsx       # 登录/注册
│   │   │   └── ApiKeyDialog.tsx      # API Key 管理
│   │   │
│   │   ├── continuation/
│   │   │   ├── SourceAnalysis.tsx    # 源文档分析
│   │   │   └── DirectionSelector.tsx # 续写方向选择
│   │   │
│   │   └── common/
│   │       ├── ConfirmDialog.tsx
│   │       ├── LoadingSpinner.tsx
│   │       ├── EmptyState.tsx
│   │       └── ErrorBoundary.tsx
│   │
│   └── types/                    # TypeScript 类型定义
│       ├── strategy.ts           # 策略接口
│       ├── novel.ts              # 小说/章节/版本类型
│       ├── world-bible.ts        # 世界书类型
│       ├── chat.ts               # 对话/消息类型
│       └── tauri.ts              # Tauri IPC 类型
│
├── package.json
├── tsconfig.json
├── vite.config.ts
├── tailwind.config.ts
└── tauri.conf.json
```

### 核心概念映射

| Python (PyQt6) | TypeScript (React) |
|---------------|-------------------|
| `QMainWindow` + `QSplitter` | `MainLayout` + `SplitPane` 组件 |
| `pyqtSignal` + `threading.Thread` | `async/await` + Zustand `setState` |
| `QListWidget` + `QListWidgetItem` | React 虚拟列表 (tanstack-virtual) |
| `QTabWidget` | Radix Tabs 或 shadcn Tabs |
| `QSlider` + `QSpinBox` | `<input type="range">` + `<input type="number">` |
| `QWebEngineView.setHtml()` | `react-markdown` |
| `QMessageBox` | shadcn AlertDialog |
| `QFileDialog.getSaveFileName()` | Tauri `dialog.save()` |
| `QTextEdit` | `<textarea>` 或 CodeMirror |
| `QInputDialog` | shadcn AlertDialog + form |
| `QComboBox` | shadcn Select |
| `QGroupBox` | 带标题的 `<fieldset>` 或 shadcn Card |
| `QCheckBox` / `QRadioButton` | shadcn Checkbox / RadioGroup |
| `QButtonGroup` | 同组 Radio 的 name 属性 |
| `QStackedWidget` | 条件渲染或 React Router |
| `QScrollArea` | CSS `overflow-y: auto` |

---

## 3. 数据流架构

### 3.1 通信模型

```
┌─────────────────────────────────────────────────────┐
│                     React 渲染进程                     │
│                                                       │
│  Zustand Store ──setState()──▶ React 组件 ────▶ 用户  │
│       ▲                                    │         │
│       │                                    ▼         │
│       │                              Tauri invoke()   │
│       │                                    │         │
│       └──────── Tauri events ◄──────────────┘         │
│                                                       │
└───────────────────────┬─────────────────────────────┘
                        │ IPC (invoke / events)
┌───────────────────────▼─────────────────────────────┐
│                    Rust 核心进程                       │
│                                                       │
│  fs::read/write    fernet::encrypt/decrypt    pbkdf2  │
│                                                       │
│  用户数据目录 (app_data_dir / bookshelf/)              │
└─────────────────────────────────────────────────────┘
```

### 3.2 加密数据流

```
用户输入密码
     │
     ▼
Tauri invoke('derive_key', { password, salt })
     │
     ▼
Rust: pbkdf2::PBKDF2<Hmac<Sha256>>
     ├── 前 32 字节 → 密码哈希 (存储验证)
     └── 后 32 字节 → Fernet 加密密钥 (不离开 Rust 层)
                           │
                           ▼
              Tauri invoke('encrypt_file', { path, data })
              Tauri invoke('decrypt_file', { path })
                           │
                           ▼
              Rust: fernet::Fernet::new(key)
```

**安全约束：** 加密密钥永远不会传递给渲染进程。所有加密/解密操作在 Rust 层完成。渲染进程只接收解密后的明文数据。

### 3.3 流式 API 调用

```
用户点击发送
     │
     ▼
ChatInput emit → useChat hook
     │
     ▼
chat-client.streamCompletion(messages) → asyncGenerator
     │
     ▼
for await (const chunk of stream) {
    setState(prev => prev + chunk)   ← 直接更新，无需信号桥接
}
     │
     ▼
React 重渲染 → MarkdownView 实时更新
```

相比 Python 版：不再需要 `StreamSignals` 和 `threading.Thread`，`async/await` 天然非阻塞。

---

## 4. 策略模式映射

### Python 版

```python
class BaseStrategy(ABC):
    @abstractmethod
    def get_system_prompt(self, **kwargs) -> str: ...
    def process_response(self, text: str) -> str: return text

class NovelStrategy(BaseStrategy):
    def get_system_prompt(self, ...) -> str: ...
```

### TypeScript 版

```typescript
interface Strategy {
  getSystemPrompt(context: StrategyContext): string;
  processResponse?(text: string): string;
}

class NovelStrategy implements Strategy {
  getSystemPrompt(ctx: StrategyContext): string {
    // 注入当前章节、字数要求、世界书等
  }
}
```

严格接口保证策略实现完整性，相比 Python 的 `ABC` 有编译时检查。

---

## 5. 状态管理设计

Zustand store 按领域拆分：

```typescript
// chat-store.ts
interface ChatState {
  messages: Message[];
  isStreaming: boolean;
  strategy: StrategyType;
  sendMessage: (content: string) => Promise<void>;
  cancelStream: () => void;
  clearMessages: () => void;
}

// novel-store.ts
interface NovelState {
  books: Book[];
  activeBookId: string | null;
  activeChapterId: string | null;
  loadBookshelf: () => Promise<void>;
  createBook: (meta: BookMeta) => Promise<void>;
  saveChapter: (chapter: Chapter) => Promise<void>;
  switchVersion: (versionId: string) => Promise<void>;
}

// settings-store.ts
interface SettingsState {
  model: string;
  temperature: number;
  topP: number;
  maxTokens: number;
  frequencyPenalty: number;
  apiKey: string;
  encryptionEnabled: boolean;
}
```

---

## 6. 分阶段实施计划

### Phase 0 — 基建 (3-5 天)

- [ ] 初始化 Tauri v2 + React + TypeScript 项目
- [ ] 配置 Vite、Tailwind、ESLint、Prettier
- [ ] 搭建 shadcn/ui 组件库
- [ ] 配置 Zustand stores 骨架
- [ ] Tauri IPC 基础通信测试
- [ ] Rust 层 crypto 模块实现（PBKDF2 + Fernet）

### Phase 1 — 核心层翻译 (5-7 天)

- [ ] `core/chat-client.ts` — OpenAI SDK 封装 + 流式
- [ ] `core/auth-manager.ts` — 注册/登录/密钥派生
- [ ] `core/novel-manager.ts` — 书架 CRUD + 版本控制 + 智能摘要
- [ ] `core/world-bible.ts` — 数据模型 + AI 提取 + 合并 + 去重
- [ ] `core/conversation-manager.ts` — 对话保存/加载
- [ ] 实现所有 Tauri IPC 命令（Rust fs + crypto）

### Phase 2 — 策略层 + 工具层 (3-4 天)

- [ ] `strategies/base-strategy.ts` + 三个实现
- [ ] `utils/prompts.ts` + `genre-styles.ts`
- [ ] `utils/summarize.ts` — AI 分段 + 世界观提取
- [ ] `utils/export.ts` — 四格式导出（txt/md/html/docx）
- [ ] `utils/supplement.ts` — 字数统计 + 扩写

### Phase 3 — UI 组件 (10-14 天)

- [ ] `MainLayout` + `SplitPane` — 主布局
- [ ] `ChatPanel` + `MessageList` + `ChatInput` — 聊天
- [ ] `MarkdownView` — Markdown 渲染
- [ ] `BookshelfPanel` + `BookCard` + `NewBookDialog` — 书架
- [ ] `ChapterList` + `ChapterEditor` + `GenerationHistory` — 小说写作
- [ ] `WorldBiblePanel` + 所有标签页 — 世界书
- [ ] `ParameterPanel` + `ModelSelector` + `StrategySelector` — 设置
- [ ] `LoginDialog` + `ApiKeyDialog` — 认证
- [ ] `SourceAnalysis` + `DirectionSelector` — 续写

### Phase 4 — 集成 + 打磨 (3-5 天)

- [ ] 暗色主题统一
- [ ] 错误处理 + ErrorBoundary
- [ ] 加载状态 + 空状态
- [ ] 快捷键绑定
- [ ] 对话框流程测试
- [ ] 端到端流程验证

---

## 7. 关键决策记录

| 决策 | 选项 | 选择 | 理由 |
|------|------|------|------|
| 桌面壳 | Electron vs Tauri | **Tauri** | 项目无 DOM 重度操作，Rust 加密性能好，打包小 |
| 状态管理 | Redux vs Zustand vs Jotai | **Zustand** | 最接近 PyQt6 的可变状态风格，学习成本最低 |
| CSS 方案 | Tailwind vs CSS Modules vs styled-components | **Tailwind** | 快速迭代，shadcn/ui 原生支持 |
| UI 组件库 | shadcn/ui vs Ant Design vs MUI | **shadcn/ui** | 无锁定，自定义灵活，打包只包含用到的组件 |
| Markdown | react-markdown vs marked vs rehype | **react-markdown** | React 生态整合最好，插件丰富 |
| 加密位置 | 渲染进程 vs Rust | **Rust 层** | 密钥不暴露给渲染进程，安全性最佳 |
| 路由 | React Router vs 条件渲染 | **条件渲染** | 单页桌面应用不需要 URL 路由，增加复杂度无收益 |

---

## 8. 风险与缓解

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|---------|
| Rust 加密与 Python Fernet 不兼容 | 中 | 高 | 实施时编写双向兼容性测试，用现有 `.enc` 文件验证 |
| Tauri v2 API 稳定性 | 中 | 中 | 锁定 Tauri 版本，关注 breaking changes |
| main_window.py 组件拆分遗漏逻辑 | 高 | 高 | 逐函数审计，按 UI 区域拆分，每个组件对应原始代码的明确行号范围 |
| 无自动化测试导致回归 | 高 | 中 | 核心层 (core/, strategies/) 强制要求单元测试 |
| 用户数据迁移不完整 | 中 | 高 | 提供迁移工具，读取旧目录结构并转换 |
| 世界书编辑器复杂度超预期 | 中 | 中 | 提前拆分 world_bible_dialog.py + world_bible.py 的交互逻辑为独立模块 |

---

## 9. 原始文件与目标文件对照表

| Python 文件 | 行数 | 目标 TS/Rust 文件 | 迁移方式 |
|------------|------|-------------------|---------|
| `gui_main.py` | 10 | `src/main.tsx` | 重写 |
| `config.py` | 41 | `src/utils/config.ts` | 翻译 |
| `ui/main_window.py` | 5,190 | 全 `components/` 目录 + `stores/` | 重构 |
| `ui/login_dialog.py` | 154 | `components/auth/LoginDialog.tsx` | 重写 |
| `ui/world_bible_dialog.py` | 421 | `components/world-bible/*.tsx` | 重写 |
| `ui/continuation_dialogs.py` | 907 | `components/continuation/*.tsx` | 重写 |
| `ui/presets.py` | 14 | `components/settings/PresetManager.tsx` | 翻译 |
| `core/chat_client.py` | 324 | `core/chat-client.ts` | 翻译 |
| `core/auth_manager.py` | 226 | `core/auth-manager.ts` + `src-tauri/src/crypto.rs` | 拆分 |
| `core/novel_manager.py` | 976 | `core/novel-manager.ts` + `src-tauri/src/fs.rs` | 拆分 |
| `core/conversation_manager.py` | 225 | `core/conversation-manager.ts` | 翻译 |
| `core/world_bible.py` | 864 | `core/world-bible.ts` | 翻译 |
| `strategies/base_strategy.py` | 67 | `strategies/base-strategy.ts` | 翻译 |
| `strategies/*.py` (3 个) | 271 | `strategies/*-strategy.ts` (3 个) | 翻译 |
| `utils/prompts.py` | 115 | `utils/prompts.ts` | 直接复制 |
| `utils/export.py` | 596 | `utils/export.ts` | 翻译 |
| `utils/summarize.py` | 855 | `utils/summarize.ts` | 翻译 |
| `utils/supplement.py` | 85 | `utils/supplement.ts` | 翻译 |
| `utils/genre_styles.py` | 126 | `utils/genre-styles.ts` | 直接复制 |
| — | — | `src-tauri/src/crypto.rs` | 新建 (Rust) |
| — | — | `src-tauri/src/fs.rs` | 新建 (Rust) |
| — | — | `src/hooks/*.ts` (5 个) | 新建 |
| — | — | `src/types/*.ts` (5 个) | 新建 |

---

## 10. 验收标准

- [ ] 启动应用 → 登录/注册 → 进入主界面流程完整
- [ ] 聊天模式：选择策略 → 发送消息 → 流式输出 → Markdown 渲染
- [ ] 书架管理：创建书 → 写章节 → 多版本切换 → 删除书
- [ ] 世界书：手动编辑 → AI 自动提取 → 合并 → 显示在 prompt 中
- [ ] 续写：导入 .txt/.md → AI 分析 → 选择方向 → 生成续写
- [ ] 导出：单章/全书/对话 → TXT/MD/HTML/DOCX
- [ ] 加密：启用加密 → 重启 → 输入密码 → 数据可读
- [ ] 用户隔离：多用户数据互不可见
- [ ] 现有 `.enc` 加密文件在新应用中可正常解密读取

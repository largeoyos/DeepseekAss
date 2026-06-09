# TypeScript 架构重构方案

> 基于 PyQt6 + Python 的 DeepSeek 聊天客户端 → React + TypeScript 单页应用
> 部署目标：Vercel (主站) + GitHub Pages (镜像/子站)

---

## 1. 目标架构总览

```
用户浏览器                              Vercel
┌──────────────────────────────┐      ┌──────────────────────────┐
│      React 单页应用          │      │   Serverless Functions   │
│                              │      │                          │
│  ┌────────────────────────┐  │      │  /api/chat               │
│  │  UI 组件层             │  │      │    → DeepSeek API 代理    │
│  │  Chat / Bookshelf /    │  │      │  /api/export/docx        │
│  │  WorldBible / ...      │  │      │    → DOCX 生成           │
│  └──────────┬─────────────┘  │      │  /api/extract-world      │
│             │                │      │    → LLM 世界观提取      │
│  ┌──────────▼─────────────┐  │      └──────────────────────────┘
│  │  Zustand Store (状态)   │  │
│  └──────────┬─────────────┘  │
│             │                │
│  ┌──────────▼─────────────┐  │
│  │  IndexedDB (持久化)     │  │
│  │  书架 / 章节 / 对话    │  │
│  │  世界书 / 用户设置     │  │
│  └──────────┬─────────────┘  │
│             │                │
│  ┌──────────▼─────────────┐  │
│  │  Web Crypto API        │  │
│  │  PBKDF2 + AES-GCM     │  │
│  │  (数据加密)            │  │
│  └────────────────────────┘  │
└──────────────────────────────┘
```

### 为什么 Vercel 而不是纯静态 Pages

| 需求 | Pages 能做什么 | Vercel 额外能做 |
|------|--------------|----------------|
| DeepSeek API 调用 | ❌ CORS 限制 | ✅ Serverless Function 转发 |
| API Key 安全 | ❌ 必须暴露给前端 | ✅ 藏在环境变量 |
| DOCX 导出 | ⚠️ 浏览器端库功能不全 | ✅ Node.js `docx` 包完整版 |
| 世界观 AI 提取 | ⚠️ 直接调 API 暴露 Key | ✅ 通过 Function 代理 |
| 文件上传续写 | ✅ File API + 读取 | ✅ 同样支持 |

### 技术栈选型

| 层 | 技术 | 理由 |
|----|------|------|
| 框架 | **React 18 + TypeScript 5.x** | 成熟生态，严格类型 |
| 构建 | **Vite** | 快速 HMR，Vercel 原生支持 |
| 状态管理 | **Zustand** | 轻量，无样板代码 |
| 样式 | **Tailwind CSS** | 快速原型 |
| UI 库 | **shadcn/ui + Radix** | 无锁定，按需引入 |
| Markdown | **react-markdown + rehype-highlight** | 替代 QWebEngineView |
| 持久化 | **idb-keyval** (IndexedDB 封装) | 简单 Promise API |
| 加密 | **Web Crypto API** (浏览器原生) | PBKDF2 + AES-GCM |
| 路由 | **React Router v6** | 多页面布局 |
| 导出 | **Blob + URL.createObjectURL** | TXT/MD/HTML |
| | **docx** npm 包 (Vercel Function) | DOCX 格式 |
| API SDK | **openai** npm 包 | 与 Python 版接口一致 |

---

## 2. 布局架构（仿大型网站结构）

### 2.1 整体布局

```
┌──────┬──────────────────────────────────────────────────┐
│      │                                                  │
│ 导航  │  主内容区                                        │
│ 侧栏  │  (按模式切换)                                    │
│      │                                                  │
│ ┌──┐ │  🎭 聊天模式                                     │
│ │🎭│ │  ┌─────────────────────┬────────────────────┐   │
│ │聊│ │  │  消息列表            │  右侧面板(可折叠)   │   │
│ │天│ │  │                     │  - 参数滑块         │   │
│ ├──┤ │  │  [消息1]            │  - 模型选择         │   │
│ │📚│ │  │  [消息2]            │  - 预设方案         │   │
│ │写│ │  │  [消息3]            │  - 对话历史         │   │
│ │作│ │  │                     │                     │   │
│ ├──┤ │  │  [输入框] [发送]    │                     │   │
│ │📄│ │  └─────────────────────┴────────────────────┘   │
│ │续│ │                                                  │
│ │写│ │  📚 写作模式                                     │
│ │  │ │  ┌─────────────────────┬────────────────────┐   │
│ ├──┤ │  │  章节编辑器          │  书架面板           │   │
│ │🔑│ │  │  (富文本/Markdown)  │  - 书列表           │   │
│ │设│ │  │                     │  - 章节列表         │   │
│ │置│ │  │                     │  - 设定编辑         │   │
│ │  │ │  │                     │  - 世界书按钮       │   │
│ ├──┤ │  └─────────────────────┴────────────────────┘   │
│ │👤│ │                                                  │
│ │↓ │ │  📄 续写模式                                     │
│ └──┘ │  ┌─────────────────────┬────────────────────┐   │
│      │  │  源文档预览 + 编辑器  │  书架面板           │   │
│      │  │                     │  - 文件上传         │   │
│      │  │                     │  - 设定编辑         │   │
│      │  │                     │  - 续写要求         │   │
│      │  └─────────────────────┴────────────────────┘   │
└──────┴──────────────────────────────────────────────────┘
```

### 2.2 导航侧栏

```
┌──────────┐
│  🎭 聊天  │  ← 点击 → 主内容区显示聊天面板
├──────────┤
│  📚 写作  │  ← 点击 → 主内容区显示写作面板
├──────────┤
│  📄 续写  │  ← 点击 → 主内容区显示续写面板
├──────────┤
│  ──────  │  ← 分割线
├──────────┤
│  🔑 设置  │  ← 点击 → 弹出设置对话框（API Key/主题等）
├──────────┤
│  📊 Token │  ← 点击 → 弹出 Token 消耗日志对话框
├──────────┤
│          │  ← 弹性空间
├──────────┤
│  👤 用户  │  ← 点击 → 弹出下拉菜单
│          │     ├ 个人信息
│  头像    │     ├ 修改密码
│          │     └ 注销
└──────────┘
```

- 固定宽度 ~60px，纯图标导航
- 选中态高亮
- 设置页面使用 Dialog/Modal 弹出，不占用主内容区
- 用户菜单使用 Dropdown Menu 组件
- 所有模式切换不刷新页面，仅切换主内容区渲染的组件

### 2.3 路由设计

```
/             → 重定向到 /chat
/chat         → ChatPage（聊天模式）
/novel        → NovelPage（写作模式）
/continuation → ContinuationPage（续写模式）
```

每个页面共享左侧导航侧栏，独立管理右侧面板内容。

### 2.4 目录结构设计

```
deepseek-assistant/
├── src/
│   ├── main.tsx                    # 入口
│   ├── App.tsx                     # 根组件 (Layout + Router)
│   ├── index.css                   # Tailwind 入口
│   │
│   ├── core/                       # 核心业务逻辑
│   │   ├── chat-client.ts          # DeepSeekChatClient (调 /api/chat)
│   │   ├── novel-manager.ts        # 书架管理 + 版本控制 (IndexedDB)
│   │   ├── conversation-manager.ts # 对话管理 (IndexedDB)
│   │   ├── world-bible.ts          # 世界书数据类 + 提取/合并
│   │   └── crypto.ts               # Web Crypto PBKDF2 + AES-GCM
│   │
│   ├── strategies/                 # 策略模式
│   │   ├── base-strategy.ts
│   │   ├── role-play-strategy.ts
│   │   ├── novel-strategy.ts
│   │   └── continuation-strategy.ts
│   │
│   ├── utils/                      # 工具模块
│   │   ├── prompts.ts              # 系统提示词
│   │   ├── export.ts               # 前端导出 (txt/md/html)
│   │   ├── summarize.ts            # AI 分段
│   │   ├── supplement.ts           # 字数统计 + 扩写
│   │   └── genre-styles.ts         # 题材风格配置
│   │
│   ├── stores/                     # Zustand 状态
│   │   ├── chat-store.ts           # 对话消息 + 流式状态
│   │   ├── novel-store.ts          # 书架 + 章节 + 版本
│   │   ├── settings-store.ts       # 参数预设 + 模型选择
│   │   ├── auth-store.ts           # 登录态 + 加密密钥
│   │   └── token-log-store.ts      # Token 消耗日志记录
│   │
│   ├── hooks/                      # 自定义 Hooks
│   │   ├── use-chat.ts
│   │   ├── use-novel.ts
│   │   ├── use-world-bible.ts
│   │   ├── use-indexed-db.ts       # IndexedDB 读写封装
│   │   └── use-crypto.ts           # 加密解密
│   │
│   ├── components/                 # UI 组件
│   │   ├── layout/                 # 全局布局
│   │   │   ├── AppLayout.tsx       # 导航侧栏 + 主内容区 + 路由
│   │   │   ├── Sidebar.tsx         # 左侧图标导航栏 (~60px)
│   │   │   └── UserDropdown.tsx    # 用户头像 + 下拉菜单
│   │   │
│   │   ├── chat/                   # 🎭 聊天模式
│   │   │   ├── ChatPage.tsx        # 聊天模式页面容器
│   │   │   ├── MessageList.tsx     # 消息列表
│   │   │   ├── MessageItem.tsx     # 单条消息 (Markdown 渲染)
│   │   │   ├── ChatInput.tsx       # 输入框 + 发送/停止按钮
│   │   │   ├── MarkdownView.tsx    # Markdown → HTML 渲染
│   │   │   └── ChatSidebar.tsx     # 聊天右侧面板 (参数/历史)
│   │   │
│   │   ├── novel/                  # 📚 写作模式
│   │   │   ├── NovelPage.tsx       # 写作模式页面容器
│   │   │   ├── ChapterEditor.tsx   # 章节编辑器 (富文本)
│   │   │   ├── GenerationHistory.tsx # 生成历史
│   │   │   └── NovelSidebar.tsx    # 写作右侧面板 (书架/设定)
│   │   │       ├── BookshelfPanel.tsx    # 书架列表 + 新建/删除
│   │   │       ├── ChapterTree.tsx       # 树形章节预览 + 导航
│   │   │       ├── ChapterTreeNode.tsx   # 递归树节点组件
│   │   │       ├── SettingsEditor.tsx    # 主角/世界观/写作要求
│   │   │       ├── GenreSelector.tsx    # 题材 + 风格下拉框
│   │   │       └── WorldBibleButton.tsx # 世界书入口
│   │   │
│   │   ├── continuation/           # 📄 续写模式
│   │   │   ├── ContinuationPage.tsx     # 续写模式页面容器
│   │   │   ├── FileUploader.tsx         # 上传 .txt/.md
│   │   │   ├── SourcePreview.tsx        # 源文档预览
│   │   │   ├── ContinuationEditor.tsx   # 续写编辑器
│   │   │   └── ContinuationSidebar.tsx  # 续写右侧面板
│   │   │       ├── BookshelfPanel.tsx   # 书架 (复用)
│   │   │       ├── ContinuationControls.tsx # 续写要求/字数/剧情
│   │   │       └── AnalysisPanel.tsx    # 分析结果展示
│   │   │
│   │   ├── settings/               # 🔑 设置
│   │   │   ├── SettingsDialog.tsx  # 设置对话框
│   │   │   ├── ApiKeySection.tsx   # API Key 管理
│   │   │   ├── ThemeSection.tsx    # 主题切换
│   │   │   └── DataSection.tsx     # 数据管理 (导出/清除)
│   │   │
│   │   ├── auth/                   # 认证
│   │   │   ├── LoginPage.tsx       # 登录/注册页面 (全屏)
│   │   │   ├── PasswordChange.tsx  # 修改密码
│   │   │   └── ProfilePage.tsx     # 个人信息页面
│   │   │
│   │   ├── world-bible/            # 📖 世界书 (6 标签页 + 解析器)
│   │   │   ├── WorldBibleDialog.tsx  # 主对话框 + 保存逻辑
│   │   │   ├── CharacterTab.tsx      # 角色标签页 + 格式化/解析
│   │   │   ├── LocationTab.tsx       # 地点标签页 + 格式化/解析
│   │   │   ├── RuleTab.tsx           # 规则标签页
│   │   │   ├── TimelineTab.tsx       # 时间线标签页 + 格式化/解析
│   │   │   ├── PlotlineTab.tsx       # 剧情线标签页 + 格式化/解析
│   │   │   └── WorldbuildingTab.tsx  # 设定与伏笔标签页 + 格式化/解析
│   │   │
│   │   ├── chapter-manager/        # 🌳 树形章节管理（类 Git）
│   │   │   ├── ChapterManagerDialog.tsx  # 主对话框：树预览+操作
│   │   │   ├── ChapterTreeNode.tsx       # 递归树节点组件
│   │   │   ├── ChapterRegenerationDialog.tsx  # 重写/润色弹窗
│   │   │   └── ChapterContextPreview.tsx # 上下文组装预览
│   │   │
│   │   └── common/                 # 通用组件
│   │       ├── ConfirmDialog.tsx
│   │       ├── LoadingSpinner.tsx
│   │       ├── EmptyState.tsx
│   │       ├── ErrorBoundary.tsx
│   │       ├── Modal.tsx           # 通用模态框壳
│   │       └── ResizablePanel.tsx  # 可拖拽侧栏
│   │   │
│   │   └── token-log/              # 📊 Token 消耗日志
│   │       ├── TokenLogDialog.tsx  # 日志对话框（列表 + 详情）
│   │       ├── TokenLogItem.tsx    # 单条日志条目
│   │       └── TokenLogStore.ts    # 日志数据存储
│   │
│   └── types/
│       ├── strategy.ts
│       ├── novel.ts
│       ├── world-bible.ts
│       ├── chat.ts
│       └── api.ts
│
├── api/                            # Vercel Serverless Functions
│   ├── chat.ts                     # POST /api/chat → DeepSeek 流式代理
│   ├── export/
│   │   └── docx.ts                 # POST /api/export/docx
│   └── extract-world.ts            # POST /api/extract-world → LLM 提取
│
├── public/
│   └── icon.svg
│
├── package.json
├── tsconfig.json
├── vite.config.ts
├── tailwind.config.ts
├── vercel.json
└── README.md
```

### 核心概念映射

| Python (PyQt6) | TypeScript (React + Web) |
|---------------|------------------------|
| `QMainWindow` + `QSplitter` | `AppLayout` + `Sidebar` (60px 图标栏) + `ResizablePanel` |
| `QStackedWidget` 面板切换 | React Router (`/chat`, `/novel`, `/continuation`) |
| `pyqtSignal` + `threading.Thread` | `async/await` + Zustand `setState` |
| `QListWidget` / `QTabWidget` | React 组件 + shadcn |
| `QSlider` + `QSpinBox` | `<input type="range">` |
| `QWebEngineView.setHtml()` | `react-markdown` |
| `QMessageBox` / `QInputDialog` | shadcn AlertDialog |
| `QFileDialog.getSaveFileName()` | `<input type="file">` / Blob download |
| `QFileDialog.getOpenFileName()` | `<input type="file">` + FileReader |
| `os.listdir` / `os.makedirs` 等 | IndexedDB 操作 |
| `json.load` / `json.dump` | IndexedDB get/set |
| `cryptography.fernet` | Web Crypto API (AES-GCM) |
| `threading.Thread` + 信号 | `fetch()` + `ReadableStream` |
| `shutil.rmtree` | IndexedDB delete |
| 左侧 QScrollArea 控制面板 | `ChatSidebar` / `NovelSidebar` / `ContinuationSidebar`（可折叠右侧面板） |

---

## 3. 数据持久化设计（IndexedDB）

### 数据库结构

```
DB: deepseek-assistant
├── accounts/            # 用户认证（纯本地，可多账号）
│   └── [username]/
│       ├── salt         # PBKDF2 salt (16 bytes)
│       └── verifier     # SHA-256(派生密钥) 用于密码验证
│
├── data/                # 所有数据，按用户隔离
│   └── [username]/
│       ├── bookshelf/
│       │   └── [bookId]/
│       │       ├── meta           # 加密后 → { title, author, genre, rootChapterId, activePath[], ... }
│       │       ├── chapters/
│       │       │   ├── index      # 所有章节 ID 列表
│       │       │   └── [chapterId]/
│       │       │       ├── node   # 加密后 → ChapterNode { id, title, summary, userDirection, generationParams, parentId, childrenIds[], ... }
│       │       │       └── content # 加密后 (章节全文)
│       │       ├── plotSummary       # 加密后 (全书剧情摘要)
│       │       ├── worldBible        # 加密后
│       │       └── generationHistory # 加密后
│       │
│       ├── conversations/
│       │   └── [convId]: 加密后 { title, messages[], parameters }
│       │
│       └── settings/     # 非敏感设置不加密
│           ├── theme
│           ├── lastModel
│           └── presets[]
│
└── global/              # 全局（不加密，无敏感信息）
    ├── lastLoginUser    # 上次登录的用户名
    └── version          # 数据版本号
```

### IndexedDB vs 文件系统的操作对照

| Python 操作 | IndexedDB 等价操作 |
|------------|-------------------|
| `os.listdir("bookshelf/")` | `db.getAllKeys('bookshelf')` |
| `json.load(open("meta.json"))` | `db.get(['bookshelf', bookId, 'meta'])` |
| `os.makedirs(path)` | 自动创建（无需操作） |
| `shutil.rmtree(path)` | `db.delete(['bookshelf', bookId])` |
| `os.rename(old, new)` | `db.set(newKey, data); db.delete(oldKey)` |
| 加密写文件 | `encrypt(data) → db.set(key, ciphertext)` |
| 加密读文件 | `ciphertext = db.get(key) → decrypt(ciphertext)` |

使用 `idb-keyval` 库（~1KB）封装 IndexedDB 操作，比直接使用 IndexedDB API 简洁得多。

---

## 4. 加密与本地认证方案

### 4.1 无需云端登录

本应用**没有服务器端用户系统**。所有用户数据存储在浏览器 IndexedDB 中，用户密码仅用于派生加密密钥，对本地数据上锁。

```
首次使用                          后续访问
───────                          ──────
输入用户名 + 密码                  输入用户名 + 密码
       │                                │
       ▼                                ▼
生成随机 salt              从 IndexedDB 读取 salt
       │                                │
       ▼                                ▼
PBKDF2(密码, salt, 600000)   PBKDF2(输入密码, salt, 600000)
       │                                │
       ├── SHA-256(密钥) → verifier     ├── SHA-256(密钥) → 比对 verifier
       │    存到 IndexedDB               │    └─ 匹配 → 解锁 (密钥存内存)
       │                                │      不匹配 → 拒绝
       └── 密钥存内存，用于加解密         └── 密钥存内存，用于加解密
```

### 4.2 完整流程图

```
┌──────────────── 首次注册 ──────────────────┐
│                                             │
│  user input: username + password            │
│    ↓                                        │
│  salt = crypto.getRandomValues(16)          │
│    ↓                                        │
│  key = PBKDF2(password, salt, 600000)       │  ← 派生 32 字节
│    ↓                                        │
│  ├─ verifier = SHA-256(key)                 │  ← 用于下次登录验证
│  │  db.set(['accounts', username], {        │
│  │    salt, verifier                        │
│  │  })                                      │
│  │                                          │
│  └─ globalKey = key (保留在内存)            │  ← 后续所有加解密用
│                                              │
└──────────────────────────────────────────────┘

┌──────────────── 登录 ──────────────────┐
│                                         │
│  user input: username + password        │
│    ↓                                    │
│  account = db.get(['accounts', username])
│    ↓                                    │
│  key = PBKDF2(password, account.salt)   │
│    ↓                                    │
│  if SHA-256(key) !== account.verifier:  │
│    → "密码错误"                          │
│  else:                                  │
│    globalKey = key (保留在内存)          │
│    → 加载该用户的书架/对话等数据          │
│                                         │
└──────────────────────────────────────────┘
```

### 4.3 数据加解密

```typescript
// 写数据 (所有 bookshelf/conversations 数据写入前调用)
async function encryptData(plaintext: string): Promise<StoredCiphertext> {
  const iv = crypto.getRandomValues(new Uint8Array(12));   // AES-GCM IV
  const encoded = new TextEncoder().encode(plaintext);
  const ciphertext = await crypto.subtle.encrypt(
    { name: 'AES-GCM', iv },
    globalKey,
    encoded
  );
  return { iv: Array.from(iv), data: Array.from(new Uint8Array(ciphertext)) };
}

// 读数据 (所有 bookshelf/conversations 数据读取后调用)
async function decryptData(stored: StoredCiphertext): Promise<string> {
  const iv = new Uint8Array(stored.iv);
  const ciphertext = new Uint8Array(stored.data);
  const plaintext = await crypto.subtle.decrypt(
    { name: 'AES-GCM', iv },
    globalKey,
    ciphertext
  );
  return new TextDecoder().decode(plaintext);
}
```

### 4.4 多账号数据隔离

```
accounts: [
  { username: "alice", salt: [...], verifier: [...] },
  { username: "bob",   salt: [...], verifier: [...] }
]

data/alice/bookshelf/...   ← alice 的密钥才能解密
data/bob/bookshelf/...     ← bob 的密钥才能解密
```

选择不同账号登录时，加载对应 username 前缀下的数据。注销时从内存丢弃 `globalKey`。

### 4.5 安全边界

- 密钥在内存，刷新页面后丢失（需要重新登录）
- 注销或会话超时 → 手动清除内存中的 key 引用
- 密码遗忘 → 数据无法恢复（无找回功能，这是设计约束）

#### 4.6 桌面版数据迁移（导出-再导入方案）

由于 Web Crypto AES-256-GCM 与 Python Fernet (AES-128-CBC + HMAC) 格式不兼容，**无法直接读取桌面版遗留的 `.enc` 文件**。推荐手动迁移路径：

1. **在 Python 桌面版中**：使用现有导出功能将各书章节导出为 TXT/MD 明文，对话导出为 JSON。
2. **在 Web 版中重新导入**：
   - 章节：使用续写模式上传 `.txt/.md` 文件，保存为新书条目
   - 对话：通过设置页面的 JSON 导入功能读取
   - 世界书/设定：结构差异较大，建议手动重建
3. **可选迁移脚本**：`scripts/migrate-from-desktop.ts` 可读取桌面版目录结构并解密 Fernet 文件，输出可导入的 JSON（需用户输入密码）。

**优先级**：低（仅影响现有桌面用户，新用户无需关心）。

---

## 5. Vercel API 设计

### 路由表

```
POST /api/chat             → DeepSeek 流式代理        (必填: messages, model)
POST /api/export/docx      → DOCX 生成                (必填: content, title)
POST /api/extract-world    → LLM 世界观提取           (必填: chapters)
POST /api/summarize        → AI 语义分段              (必填: text)
POST /api/supplement       → 字数不足扩写             (必填: text, targetWords)
```

### chat.ts 核心逻辑

```typescript
// api/chat.ts
import OpenAI from 'openai';

export async function POST(req: Request) {
  const { messages, model } = await req.json();
  const openai = new OpenAI({
    apiKey: process.env.DEEPSEEK_API_KEY,  // 环境变量，不暴露给前端
    baseURL: process.env.DEEPSEEK_BASE_URL,
  });

  const stream = await openai.chat.completions.create({
    model: model || 'deepseek-chat',
    messages,
    stream: true,
  });

  return new Response(stream.toReadableStream(), {
    headers: { 'Content-Type': 'text/event-stream' },
  });
}
```

浏览器端直接 `fetch('/api/chat', { body: JSON.stringify({ messages }) })`，不需要担心 CORS。

### 为什么部分逻辑放前端而非后端

| 逻辑 | 位置 | 理由 |
|------|------|------|
| DeepSeek API 代理 | Vercel Function | 隐藏 API Key |
| DOCX 生成 | Vercel Function | Node.js `docx` 包完整 |
| 世界观提取/分段/扩写 | Vercel Function | 需要调 LLM，隐藏 Key |
| **书架 CRUD** | **前端 IndexedDB** | 无服务器状态，低延迟 |
| **章节版本控制** | **前端 IndexedDB** | 用户本地数据 |
| **世界书编辑** | **前端 IndexedDB** | 用户本地数据 |
| **对话历史** | **前端 IndexedDB** | 用户本地数据 |
| **加密/解密** | **前端 Web Crypto** | 密钥不离开内存 |

原则：用户数据留在用户本地，只有需要 LLM 或 Node.js 特定能力的请求才走 Vercel。

---

## 6. 分阶段实施计划（含详细实现策略）

---

### Phase 0 — 基建 (3-5 天)

**目标**：搭建可运行的 React + TypeScript 项目骨架，配置所有构建工具链，实现 Vercel 代理 API 和本地加密基础。

#### 6.0.1 项目初始化

```bash
npm create vite@latest deepseek-assistant -- --template react-ts
cd deepseek-assistant
npm install
```

#### 6.0.2 依赖安装

```bash
# 核心
npm install react-router-dom zustand idb-keyval openai

# UI
npm install tailwindcss @tailwindcss/vite
npx shadcn@latest init    # 选择: New York style, slate base color
npx shadcn@latest add button dialog dropdown-menu input select slider tabs textarea sheet

# Markdown
npm install react-markdown remark-gfm rehype-highlight rehype-raw

# 工具
npm install uuid
npm install -D @types/uuid
```

#### 6.0.3 Vite 配置

```typescript
// vite.config.ts
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';
import path from 'path';

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { '@': path.resolve(__dirname, './src') },
  },
  server: {
    proxy: {
      '/api': 'http://localhost:3000',
    },
  },
});
```

#### 6.0.4 Tailwind 暗色主题基准

```css
/* src/index.css */
@import "tailwindcss";

@theme {
  --color-bg-primary: #0f0f1a;
  --color-bg-secondary: #1a1a2e;
  --color-bg-tertiary: #222238;
  --color-bg-hover: #2a2a44;
  --color-text-primary: #e0e0e8;
  --color-text-secondary: #8888a0;
  --color-text-muted: #555568;
  --color-accent: #569cd6;
  --color-accent-hover: #69b5ff;
  --color-accent-dim: rgba(86, 156, 214, 0.12);
  --color-danger: #e05050;
  --color-success: #4ec9a0;
  --color-warning: #d4a04a;
  --color-purple: #9a6abc;
  --color-orange: #d87a4c;
}
```

#### 6.0.5 vercel.json

```json
{
  "functions": {
    "api/chat.ts": { "maxDuration": 60 },
    "api/extract-world.ts": { "maxDuration": 30 },
    "api/summarize.ts": { "maxDuration": 30 },
    "api/supplement.ts": { "maxDuration": 30 }
  },
  "rewrites": [{ "source": "/(.*)", "destination": "/index.html" }]
}
```

#### 6.0.6 api/chat.ts — 流式代理实现策略

**关键设计**：使用 OpenAI SDK 的 `stream.toReadableStream()` 直接转发 SSE 流。

```typescript
// api/chat.ts — Vercel Edge-compatible 流式代理
import OpenAI from 'openai';

export const config = { runtime: 'edge' };

export async function POST(req: Request) {
  const { messages, model } = await req.json();
  const openai = new OpenAI({
    apiKey: process.env.DEEPSEEK_API_KEY!,
    baseURL: process.env.DEEPSEEK_BASE_URL || 'https://api.deepseek.com',
  });

  const stream = await openai.chat.completions.create({
    model: model || 'deepseek-chat',
    messages,
    stream: true,
  });

  return new Response(stream.toReadableStream(), {
    headers: {
      'Content-Type': 'text/event-stream',
      'Cache-Control': 'no-cache',
      'Connection': 'keep-alive',
    },
  });
}
```

**前端消费策略**：

```typescript
// 流式事件类型：普通内容块 or 最终 usage
type StreamEvent =
  | { type: 'chunk'; content: string }
  | { type: 'usage'; data: { prompt_tokens: number; completion_tokens: number; total_tokens: number } };

// 前端流式读取模式
async function* streamChat(messages: Message[], model: string): AsyncGenerator<StreamEvent> {
  const response = await fetch('/api/chat', {
    method: 'POST',
    body: JSON.stringify({ messages, model }),
  });
  if (!response.ok) throw new Error(`API error: ${response.status}`);

  const reader = response.body!.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split('\n');
    buffer = lines.pop() || '';

    for (const line of lines) {
      if (line.startsWith('data: ')) {
        const data = line.slice(6);
        if (data === '[DONE]') return;
        const parsed = JSON.parse(data);
        // 最后一条 chunk：choices 为空但有 usage
        if (parsed.usage) {
          yield { type: 'usage', data: parsed.usage };
        } else if (parsed.choices?.[0]?.delta?.content) {
          yield { type: 'chunk', content: parsed.choices[0].delta.content };
        }
      }
    }
  }
}
```

**错误处理策略**：
- 网络错误 → 重试 1 次，间隔 2 秒
- 401/403 → 清除 API Key 并提示用户重新配置
- 429/Rate Limit → 显示"请求过于频繁，请稍后重试"
- 500+ → 显示"服务器错误，请稍后重试"
- 所有错误在 UI 层以消息气泡形式展示（非弹窗），不中断对话流程

#### 6.0.7 core/crypto.ts — Web Crypto 加密实现策略

```typescript
// 核心类型
interface StoredCiphertext {
  iv: number[];       // AES-GCM IV (12 bytes)
  data: number[];     // 密文
}

interface Account {
  salt: number[];     // PBKDF2 salt (16 bytes)
  verifier: number[]; // SHA-256(derivedKey) — 用于登录验证
}

// 注册流程
async function register(username: string, password: string): Promise<CryptoKey> {
  const salt = crypto.getRandomValues(new Uint8Array(16));
  const key = await deriveKey(password, salt);
  const verifier = await sha256(key);
  await db.set(['accounts', username], {
    salt: Array.from(salt),
    verifier: Array.from(new Uint8Array(verifier)),
  });
  return key;
}

// 登录流程
async function login(username: string, password: string): Promise<CryptoKey | null> {
  const account: Account = await db.get(['accounts', username]);
  if (!account) return null;
  const key = await deriveKey(password, new Uint8Array(account.salt));
  const verifier = await sha256(key);
  if (arraysEqual(new Uint8Array(verifier), new Uint8Array(account.verifier))) {
    return key;
  }
  return null;
}
```

**关键限制**：
- `globalKey` 存储在 Zustand auth store 的内存中，刷新页面后必须重新登录
- 加密/解密函数在 `globalKey === null` 时直接 throw
- AES-GCM 的 IV 必须每次随机生成，与密文一起存储

#### 6.0.8 数据持久化封装 — hooks/use-indexed-db.ts

```typescript
// 基于 idb-keyval 的封装
import { get, set, del, createStore, keys } from 'idb-keyval';

function userStore(username: string) {
  return createStore(`deepseek-${username}`, 'user-data');
}

async function saveData<T>(store: IDBStore, key: string, data: T): Promise<void> {
  await set(key, data, store);
}

async function loadData<T>(store: IDBStore, key: string): Promise<T | undefined> {
  return get(key, store);
}

async function deleteData(store: IDBStore, key: string): Promise<void> {
  await del(key, store);
}

async function listKeys(store: IDBStore): Promise<string[]> {
  return keys(store) as Promise<string[]>;
}
```

---

### Phase 0.5 — 测试基础设施 (1-2 天)

**目标**：搭建完整测试体系，覆盖单元测试、组件测试、E2E 测试。

#### 6.0.9 测试框架安装

```bash
npm install -D vitest @testing-library/react @testing-library/jest-dom @testing-library/user-event
npm install -D jsdom @types/jsdom @playwright/test
npx playwright install chromium
```

#### 6.0.10 测试目录结构

```
src/
├── __tests__/                  # 集成测试
│   ├── chat-client.test.ts
│   ├── novel-manager.test.ts
│   └── world-bible.test.ts
├── components/
│   ├── chat/MessageList.test.tsx   # 组件测试与组件同目录
│   ├── novel/ChapterTree.test.tsx
│   └── ...
├── stores/chat-store.test.ts
└── utils/
    ├── export.test.ts
    ├── supplement.test.ts
    └── summarize.test.ts
```

#### 6.0.11 Vitest 配置

```typescript
// vitest.config.ts
export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test-setup.ts'],
    coverage: { provider: 'v8', thresholds: { lines: 70, functions: 65, branches: 60 } },
  },
  resolve: { alias: { '@': path.resolve(__dirname, './src') } },
});
```

#### 6.0.12 组件测试模式

每个核心组件覆盖四种状态：loading → empty → error → ready。

```typescript
describe('MessageList', () => {
  it('renders spinner on loading');
  it('renders empty state');
  it('renders error with retry button');
  it('renders message bubbles');
  it('auto-scrolls on new message');
});
```

#### 6.0.13 E2E 测试 (Playwright)

```
e2e/
├── auth.spec.ts        # 登录/注册
├── chat.spec.ts        # 发消息 → 流式输出 → 停止
├── novel.spec.ts       # 创建书 → 生成章节 → 切换分支
├── export.spec.ts      # 下载 TXT/MD/HTML
└── continuation.spec.ts # 上传文件 → 分析 → 续写
```

---

### Phase 1 — 核心层翻译 (5-7 天)

**目标**：将 5 个 core 模块翻译为 TypeScript，核心差异是文件系统 → IndexedDB。

#### 6.1.1 core/chat-client.ts — 聊天客户端

**数据流**：

```
Zustand ChatStore
  .messages: Message[]          ← 当前对话消息列表
  .streaming: boolean           ← 是否正在流式输出
  .streamContent: string        ← 当前流式输出的累积内容
  .abortController: AbortController | null  ← 用于取消请求

sendMessage(userText) →
  1. appendMessage({ role: 'user', content: userText })
  2. set({ streaming: true, abortController: new AbortController() })
  3. appendMessage({ role: 'assistant', content: '' })
  4. for await (const event of streamChat(messages, model)):
       if (event.type === 'chunk') updateStreamContent(event.content)
  5. set({ streaming: false, abortController: null })

stopGeneration() →
  abortController.abort()
  set({ streaming: false, abortController: null })
```

**实现要点**：
- 使用 `AbortController` 替代 Python 版的 `stop_flag`
- 流式输出时，最后一条 assistant 消息的 content 是实时追加的
- 每次发送前自动保存当前对话到 IndexedDB

#### 6.1.2 core/novel-manager.ts — 树形章节管理（类 Git 架构）

**核心设计思想**：章节组织从线性列表改为**树形结构**，每个节点代表一章，分支代表同一章节的不同改写版本。系统维护一条"活跃路径"（类似 Git HEAD），从根节点沿指定分支指向当前最新章节。

**数据类型定义**：

```typescript
// types/novel.ts
interface ChapterNode {
  id: string;
  bookId: string;
  title: string;
  displayNumber: string;          // 显示用编号，如 "1" / "2a" / "2b" / "3"
  content: string;                // 章节全文（加密存储）
  summary: string;                // 本章剧情概要（用于上下文拼接）
  userDirection: string;          // 用户输入的创作方向/剧情要求（人类可读，可编辑）
  generationParams: string;       // AI 生成参数快照 (JSON: model, temperature, topP, freqPenalty, strategyId)
  parentId: string | null;        // 父节点 ID（根节点为 null）
  childrenIds: string[];          // 子节点 ID 列表（改写产生的同级分支）
  siblingOrder: number;           // 同级兄弟节点间的排序
  createdAt: number;
  updatedAt: number;
}

interface BookMeta {
  id: string;
  title: string;
  author: string;
  genre: string;
  style: string;
  createdAt: number;
  updatedAt: number;
  rootChapterId: string | null;    // 树根节点 ID
  activePath: string[];            // 活跃路径 ID 数组 [root, ch2, ch3, ...]
  characterSetting: string;        // 主角设定（全局）
  worldSetting: string;            // 世界观（全局）
}
```

**IndexedDB 键结构**：

```
keys:
  'bookshelf:index'                            → BookMeta[]
  'bookshelf:{bookId}:meta'                    → BookMeta (含 activePath)
  'bookshelf:{bookId}:chapters:all'            → string[] (所有章节 ID，用于遍历)
  'bookshelf:{bookId}:chapters:{chId}'         → ChapterNode (完整章节数据，content 加密)
  'bookshelf:{bookId}:chapters:{chId}:content' → string (拆分存储的全文，加密)
  'bookshelf:{bookId}:plotSummary'             → string (全书剧情摘要，AI 生成)
  'bookshelf:{bookId}:worldBible'              → WorldBible (加密)
  'bookshelf:{bookId}:generationHistory'       → GenerationRecord[]
```

**活跃路径算法**（决定当前展示哪一章）：

```typescript
// 从 BookMeta.activePath 获取当前章节
function getActiveChapter(book: BookMeta, chapters: Map<string, ChapterNode>): ChapterNode | null {
  if (book.activePath.length === 0) return null;
  const lastId = book.activePath[book.activePath.length - 1];
  return chapters.get(lastId) ?? null;
}

// 切换到兄弟节点（选择不同分支）
function switchToSibling(book: BookMeta, siblingId: string): BookMeta {
  const path = [...book.activePath];
  // 找到兄弟节点所在的层级，替换该层
  const sibling = chapters.get(siblingId);
  while (path.length > 0) {
    const node = chapters.get(path[path.length - 1]);
    if (node?.parentId === sibling?.parentId) {
      path[path.length - 1] = siblingId;
      break;
    }
    path.pop(); // 回溯到上一级
  }
  return { ...book, activePath: path };
}
```

**上下文组装策略**（替代旧版 Smart Summary）：

```typescript
// 从根节点到当前章节，沿 activePath 串联所有 summary + userDirection
function assembleContext(book: BookMeta, chapters: Map<string, ChapterNode>): ContextAssembly {
  const segments: ContextSegment[] = [];
  let totalTokens = 0;

  for (const chId of book.activePath) {
    const ch = chapters.get(chId);
    if (!ch) continue;

    segments.push({
      chapterId: ch.id,
      chapterTitle: ch.title,
      summary: ch.summary,
      prompt: ch.userDirection,
    });

    totalTokens += estimateTokens(ch.summary) + estimateTokens(ch.userDirection);
  }

  return {
    bookInfo: { title: book.title, characterSetting: book.characterSetting, worldSetting: book.worldSetting },
    segments,
    totalTokens,
  };
}

// 组装后的上下文格式（发送给 AI）
function formatContextForPrompt(assembly: ContextAssembly): string {
  let context = `【小说设定】\n主角：${assembly.bookInfo.characterSetting}\n世界观：${assembly.bookInfo.worldSetting}\n\n`;
  context += `【剧情概要】\n`;

  for (const seg of assembly.segments) {
    context += `第${seg.chapterTitle}章概要：${seg.summary}\n`;
    if (seg.prompt) {
      context += `  写作要求：${seg.prompt}\n`;
    }
  }

  context += `\n---\n请基于以上剧情脉络，续写下一章内容。\n`;
  return context;
}
```

**生成新章节流程**（重写 vs 润色）：

```
用户对某章节选择"重写"：
  1. 沿 activePath 从根到该章节父节点，组装上下文
  2. 弹出 ChapterRegenerationDialog，显示：
     - 上下文预览（从 root 到前驱章节的所有概要）
     - 本章原来的 userDirection（可编辑）
  3. 用户修改提示词后确认
  4. 调用 AI：formatContextForPrompt(assembly) + 用户修改后的 prompt
  5. AI 返回新章节全文
  6. 创建新 ChapterNode：
     parentId = 父章节.id
     childrenIds = []
     siblingOrder = 父章节现有子节点数 + 1
  7. 将新节点 ID 加入父节点的 childrenIds
  8. 生成本章 summary（调用 /api/summarize 对全文提炼）

用户对某章节选择"润色"：
  1. 保留原上下文不变
  2. 弹出对话框，用户输入润色要求（风格调整、细节补充等）
  3. 调用 AI：原章节全文 + 用户润色要求
  4. AI 返回润色后的章节全文
  5. 创建新 ChapterNode（同上，作为原节点的兄弟）
  6. 摘要：使用润色后的全文重新生成

生成下一章（非重写）：
  1. 沿 activePath 从根到当前章节，组装上下文
  2. + 当前章节的 summary + userDirection
  3. 弹出对话框让用户输入本章剧情要求
  4. 调用 AI 生成
  5. 创建新节点作为当前章节的子节点
  6. 更新 activePath
```

**树形预览算法**：

```typescript
function buildTreeData(chapters: Map<string, ChapterNode>, rootId: string): TreeNode {
  const root = chapters.get(rootId)!;
  return {
    id: root.id,
    label: `${root.displayNumber}. ${root.title}`,
    summary: root.summary,
    isActive: false,  // 由 activePath 标记
    children: root.childrenIds
      .map(id => chapters.get(id))
      .filter(Boolean)
      .map(ch => buildTreeData(chapters, ch!.id)),
  };
}

// 在树中标记活跃路径
function markActivePath(node: TreeNode, activePath: Set<string>): void {
  node.isActive = activePath.has(node.id);
  for (const child of node.children) {
    markActivePath(child, activePath);
  }
}
```

**版本控制策略变更**（从线性版本 → 树形分支）：
- 不再保留 `versions:index` 和 `versions:{vId}`（旧版多版本存储废弃）
- 替代为：同级的 children 节点自然构成版本历史
- 每个节点一经创建不可修改（immutable），修改 = 创建新兄弟节点
- 删除节点时递归删除其所有子节点
- 树深度限制：最多 200 层，超出时给出警告

#### 6.1.3 core/world-bible.ts — 世界书（完整翻译自 Python 864 行）

世界书系统负责从已生成的章节中提取核心设定、角色、地点、规则、剧情线索，并持久化为结构化数据供后续章节生成时参考，防止设定矛盾。

---

**所有数据结构（完整翻译自 Python dataclass）**：

```typescript
// types/world-bible.ts

interface Relationship {
  target: string;
  type: string;           // friend/enemy/family/master/student/ally/rival/lover
  description: string;
}

interface CharacterEntry {
  id: string;
  name: string;
  aliases: string[];
  traits: string;             // 性格、外貌、能力（500 字内）
  relationships: Relationship[];
  status: 'alive' | 'dead' | 'missing' | 'transformed';
  importance: 'major' | 'normal' | 'minor';
  first_appearance: number;   // 章节编号
  notes: string;
  key_details: string[];      // 原文引用的角色关键描述（每段 100 字内）
  key_dialogues: string[];    // 原文引用的角色重要台词（每句 100 字内）
  motivation: string;         // 核心动机/目标（100 字内）
  arc: string;                // 成长弧线/变化趋势（100 字内）
}

interface LocationEntry {
  id: string;
  name: string;
  description: string;        // 外观、氛围、布局（300 字内）
  significance: string;       // 在故事中的重要性/象征意义（200 字内）
  first_appearance: number;
  key_details: string[];      // 原文引用的地点重要描写
  atmosphere: string;         // 氛围描述（200 字内）
}

interface TimelineEntry {
  id: string;
  chapter: number;
  event: string;              // 核心事件详细描述（200 字内）
  significance: string;       // 事件影响/意义（200 字内）
  key_passages: string[];     // 原文引用的事件重要段落
  foreshadowing_hints: string[];  // 该事件中埋下的伏笔（每条 50 字内）
}

interface PlotThread {
  id: string;
  name: string;
  status: 'active' | 'resolved' | 'dormant';
  importance: 'major' | 'normal' | 'minor';
  involved_characters: string[];
  description: string;            // 该线索的详细描述（300 字内）
  key_details: string[];          // 原文引用的剧情线重要内容
  foreshadowing_related: string[]; // 该线关联的前期伏笔（每条 50 字内）
}

interface WorldBible {
  characters: CharacterEntry[];
  locations: LocationEntry[];
  rules: string[];                    // 世界观规则列表
  timeline: TimelineEntry[];          // 时间线事件
  active_plot_threads: PlotThread[];  // 剧情线（活跃/已解决/休眠）
  last_updated_chapter: number;
  key_worldbuilding_passages: WorldbuildingPassage[];  // [{chapter, passage, topic}]
  global_foreshadowing: ForeshadowingHint[];            // [{hint, relates_to}]
  global_key_dialogues: KeyDialogue[];                  // [{speaker, dialogue, context}]
}

interface WorldbuildingPassage {
  topic: string;
  passage: string;    // 原文引用（300 字内）
  chapter: number;
}

interface ForeshadowingHint {
  hint: string;       // 伏笔内容（50 字内）
  relates_to: string; // 关联剧情线或角色（20 字内）
}

interface KeyDialogue {
  speaker: string;
  dialogue: string;    // 原文引用
  context: string;     // 对话背景（30 字内）
}
```

---

**序列化/反序列化**：

```typescript
// 对应 Python _filter_fields + _from_dict
// 过滤 dict 只保留接口中定义的字段，兼容 schema 变化
function filterFields<T>(data: Record<string, unknown>, allowedKeys: Set<string>): Partial<T> {
  const result: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(data)) {
    if (allowedKeys.has(k)) result[k] = v;
  }
  return result as Partial<T>;
}

// 递归反序列化（处理 Relationship 等嵌套对象）
function fromDict<T>(cls: new (...args: unknown[]) => T, data: Record<string, unknown>): T {
  if (cls === CharacterEntry) {
    const rels = (data.relationships as any[])?.map(r => new Relationship(
      filterFields<Relationship>(r, new Set(['target', 'type', 'description']))
    )) || [];
    return new CharacterEntry({
      ...filterFields<CharacterEntry>(data, CHARACTER_KEYS),
      relationships: rels,
    });
  }
  // 类似处理 WorldBible（递归反序列化所有子数组）
  return new cls(filterFields<T>(data, new Set(Object.keys(data))));
}

function worldBibleToDict(bible: WorldBible): Record<string, unknown> {
  return JSON.parse(JSON.stringify(bible)); // 简单深拷贝（所有字段可序列化）
}

function dictToWorldBible(data: Record<string, unknown>): WorldBible {
  return fromDict(WorldBible, data);
}

const CHARACTER_KEYS = new Set([
  'name', 'aliases', 'traits', 'relationships', 'status', 'importance',
  'first_appearance', 'notes', 'key_details', 'key_dialogues', 'motivation', 'arc'
]);
```

---

**Prompt 注入格式化** — `formatWorldBibleForPrompt()`（对应 Python `format_world_bible_for_prompt`）：

用途：每次生成新章节时，将世界书格式化为紧凑文本注入到 system prompt 中。

```typescript
function formatWorldBibleForPrompt(bible: WorldBible, maxEntries: number = 10): string {
  const parts: string[] = [];

  // 1. 角色（按重要性排序 major → normal → minor）
  if (bible.characters.length > 0) {
    parts.push('【已登场的角色】');
    const sorted = [...bible.characters].sort(
      (a, b) => IMPORTANCE_RANK[a.importance] - IMPORTANCE_RANK[b.importance]
    );
    for (const ch of sorted.slice(0, maxEntries)) {
      let line = `- ${ch.name}：${ch.traits.slice(0, 100)}`;
      if (ch.motivation) line += ` | 动机：${ch.motivation.slice(0, 60)}`;
      if (ch.arc) line += ` | 弧光：${ch.arc.slice(0, 60)}`;
      const rels = ch.relationships.slice(0, 3).map(r => `${r.type}(${r.target})`).join('; ');
      if (rels) line += ` | 关系：${rels}`;
      if (ch.status !== 'alive') line += ` [${ch.status}]`;
      if (ch.key_details.length) line += ` | ${ch.key_details.slice(0, 2).join(' | ')}`;
      if (ch.key_dialogues.length) line += ` | 台词：${ch.key_dialogues[0]}`;
      parts.push(line);
    }
    if (bible.characters.length > maxEntries) {
      parts.push(`  ...以及另 ${bible.characters.length - maxEntries} 个角色`);
    }
  }

  // 2. 地点
  if (bible.locations.length > 0) {
    parts.push('\n【重要地点】');
    for (const loc of bible.locations.slice(0, maxEntries)) {
      let line = `- ${loc.name}：${loc.description.slice(0, 80)}`;
      if (loc.atmosphere) line += `（${loc.atmosphere.slice(0, 40)}）`;
      if (loc.significance) line += ` | 意义：${loc.significance.slice(0, 60)}`;
      if (loc.key_details.length) line += ` | ${loc.key_details[0]}`;
      parts.push(line);
    }
  }

  // 3. 世界观规则
  if (bible.rules.length > 0) {
    parts.push('\n【世界观规则】');
    for (const rule of bible.rules.slice(0, maxEntries)) {
      parts.push(`- ${rule.slice(0, 150)}`);
    }
  }

  // 4. 活跃/非活跃剧情线
  const active = bible.active_plot_threads.filter(p => p.status === 'active');
  const nonActive = bible.active_plot_threads.filter(p => p.status !== 'active');
  if (active.length > 0) {
    parts.push('\n【活跃剧情线】');
    for (const p of active.slice(0, maxEntries)) {
      let line = `- ${p.name}：${p.description.slice(0, 100)}`;
      if (p.involved_characters.length) line += ` | 角色：${p.involved_characters.slice(0, 4).join(', ')}`;
      if (p.foreshadowing_related.length) line += ` | 伏笔：${p.foreshadowing_related[0]}`;
      parts.push(line);
    }
  }
  if (nonActive.length > 0) {
    parts.push('\n【待回收剧情线】');
    for (const p of nonActive.slice(0, 4)) {
      parts.push(`- ${p.name} [${p.status}]：${p.description.slice(0, 80)}`);
    }
  }

  // 5. 近期事件（从 timeline 尾部取最近条目）
  if (bible.timeline.length > 0) {
    const recent = bible.timeline.slice(-maxEntries);
    parts.push('\n【近期事件】');
    for (const t of recent) {
      let line = `- 第${t.chapter}章：${t.event.slice(0, 80)}`;
      if (t.significance) line += `（${t.significance.slice(0, 40)}）`;
      if (t.foreshadowing_hints.length) line += ` 🔮${t.foreshadowing_hints[0]}`;
      parts.push(line);
    }
  }

  // 6. 关键设定与伏笔（3-4 条）
  const extras: string[] = [];
  for (const item of (bible.key_worldbuilding_passages || []).slice(0, 3)) {
    extras.push(`- 设定·${item.topic}：${item.passage.slice(0, 100)}`);
  }
  for (const item of (bible.global_foreshadowing || []).slice(0, 3)) {
    extras.push(`- 伏笔·${item.hint.slice(0, 60)}`);
  }
  if (extras.length > 0) {
    parts.push('\n【关键设定与伏笔】');
    parts.push(...extras);
  }

  return parts.join('\n');
}

const IMPORTANCE_RANK: Record<string, number> = { major: 0, normal: 1, minor: 2 };
```

---

**AI 提取 System Prompt**（对应 Python `EXTRACT_PROMPT` 常量）：

```typescript
// utils/prompts.ts — 世界书提取模板（完整翻译 Python 版）
const WORLD_BIBLE_EXTRACT_PROMPT = `你是一个小说信息深度提取专家。请严格根据以下章节内容，深度提取其中的角色、地点、世界观规则、事件和剧情线索。

约束：
- 严格基于原文，不要添加社会学分析、心理描写分析或道德评判
- 对于标注了【原文引用】的字段，直接从原文复制原文，不要改写或概括
- 对于未标注【原文引用】的字段，可以适当概括但保留所有关键信息
- 宁多勿少，不确定该不该提取的信息请提取出来

请严格按照以下 JSON 格式输出，不包含任何其他文字：

{
  "characters": [
    {
      "name": "角色名",
      "aliases": ["别名", "别称"],
      "traits": "【500字内】性格描写、外貌特征、能力特长——尽可能详细地从原文提取",
      "relationships": [
        {"target": "关系对象", "type": "friend/enemy/family/master/student/ally/rival/lover", "description": "关系描述（30字内）"}
      ],
      "status": "alive/dead/missing/transformed",
      "importance": "major/normal/minor",
      "key_details": ["【原文引用】从原文中直接复制关于该角色的重要描述片段（每段100字内）"],
      "key_dialogues": ["【原文引用】从原文中直接复制该角色说出的重要台词（每句100字内）"],
      "motivation": "该角色的核心动机/目标（100字内）",
      "arc": "该角色的成长弧线/变化趋势（100字内）"
    }
  ],
  "locations": [
    {
      "name": "地点名",
      "description": "【300字内】地点的外观、氛围、布局等详细描述",
      "significance": "【200字内】该地点在故事中的重要性/象征意义",
      "key_details": ["【原文引用】从原文中直接复制关于该地点的重要描写片段"],
      "atmosphere": "【200字内】该地点的氛围/给人的感觉"
    }
  ],
  "rules": ["世界观规则1（完整保留原文描述）", "规则2"],
  "timeline": [
    {
      "event": "【200字内】核心事件的详细描述",
      "significance": "【200字内】该事件的影响/意义",
      "key_passages": ["【原文引用】从原文中直接复制该事件中最重要的一段描写"],
      "foreshadowing_hints": ["该事件中埋下的伏笔或暗示（50字内）"]
    }
  ],
  "plot_threads": [
    {
      "name": "剧情线索名",
      "status": "active/resolved/dormant",
      "importance": "major/normal/minor",
      "involved_characters": ["角色名"],
      "description": "【300字内】该线索的详细描述",
      "key_details": ["【原文引用】关于该剧情线的重要原文片段"],
      "foreshadowing_related": ["该剧情线涉及的前期伏笔（50字内）"]
    }
  ],
  "key_worldbuilding": [
    {"topic": "设定主题", "passage": "【原文引用】从原文中直接复制重要的世界观设定段落（300字内）"}
  ],
  "global_key_dialogues": [
    {"speaker": "说话者", "dialogue": "【原文引用】重要对话原文", "context": "对话背景（30字内）"}
  ],
  "global_foreshadowing": [
    {"hint": "伏笔内容（50字内）", "relates_to": "可能相关的剧情线或角色（20字内）"}
  ]
}

如果没有某项内容，用空数组 []。确保 JSON 合法。`;
```

---

**提取与合并 API** — `POST /api/extract-world`：

```typescript
// api/extract-world.ts — 对应 Python extract_and_merge_world_bible()
// 分析章节内容 → 提取结构化数据 → 与现有世界书合并

export async function POST(req: Request) {
  const {
    chapterContent,   // 章节正文（截断前 40000 字符）
    chapterNum,       // 当前章节编号
    existingBible,    // 现有世界书（或 null）
    storyContext,     // 前文摘要（批量导入时逐章积累）
    backgroundStory,  // 世界观设定背景
    protagonistBio,   // 主角描述
    writingDemand,    // 写作要求
  } = await req.json();

  // 构建 prompt 前缀（故事背景上下文）
  const ctxParts: string[] = [];
  if (backgroundStory || protagonistBio || storyContext || writingDemand) {
    ctxParts.push('【故事背景】');
    if (backgroundStory) ctxParts.push(`世界观设定：${backgroundStory.slice(0, 500)}`);
    if (protagonistBio) ctxParts.push(`主角描述：${protagonistBio.slice(0, 500)}`);
    if (storyContext) ctxParts.push(`前情提要：${storyContext.slice(0, 1000)}`);
    if (writingDemand) ctxParts.push(`写作要求：${writingDemand.slice(0, 300)}`);
  }
  const promptPrefix = ctxParts.length > 0 ? ctxParts.join('\n') + '\n\n' : '';

  // 调用 LLM（见下方调用逻辑）
  const data = await callExtractLLM(promptPrefix + WORLD_BIBLE_EXTRACT_PROMPT + chapterContent.slice(0, 40000));

  // 合并到 existingBible（见下方合并策略）
  const merged = mergeIntoBible(existingBible || new WorldBible(), data, chapterContent, chapterNum);

  // AI 去重（可选调用）
  await dedupCharacters(merged);
  await dedupLocations(merged);

  merged.lastUpdatedChapter = chapterNum;
  return Response.json({ success: true, data: merged });
}
```

**调用逻辑（含 JSON 修复 + 重试）**：

```typescript
async function callExtractLLM(userContent: string, maxRetries = 2): Promise<any> {
  let maxTokens = 16384;

  for (let attempt = 0; attempt < maxRetries; attempt++) {
    const response = await openai.chat.completions.create({
      model: 'deepseek-chat',
      messages: [{ role: 'user', content: userContent }],
      max_tokens: maxTokens,
      temperature: 0.1,
      response_format: { type: 'json_object' },  // 强制 JSON
    });

    const raw = response.choices[0].message.content || '';
    let jsonStr = raw.trim();
    if (jsonStr.includes('```json')) {
      jsonStr = jsonStr.split('```json')[1].split('```')[0].trim();
    } else if (jsonStr.includes('```')) {
      jsonStr = jsonStr.split('```')[1].split('```')[0].trim();
    }

    // 尝试修复常见 JSON 错误
    const repairSteps = [
      jsonStr,
      repairJson(jsonStr),              // 修复中文标点
      repairJson(repairTruncatedJson(jsonStr)),  // 修复截断
    ];

    for (const step of repairSteps) {
      try { return JSON.parse(step); } catch { continue; }
    }

    // 全部修复失败 → 增大 maxTokens 重试
    if (attempt === 0) {
      maxTokens = 32768;
      userContent += '\n\n注意：请确保输出完整、合法的 JSON，不要被截断。';
    }
  }

  throw new Error('世界书提取 JSON 解析失败');
}

// JSON 修复工具函数
function repairJson(text: string): string {
  return text
    .replace(/[""]/g, '"').replace(/[""]/g, '"')   // 中文引号 → ASCII
    .replace(/，/g, ',').replace(/：/g, ':')
    .replace(/；/g, ';').replace(/（/g, '(').replace(/）/g, ')')
    .replace(/\(\s*("(?:[^"\\]|\\.)*"\s*:)/g, '{$1');  // (key → {key
}

function repairTruncatedJson(text: string): string {
  const stack: string[] = [];
  let lastGoodEnd = -1;
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (ch === '{' || ch === '[') stack.push(ch);
    else if (ch === '}' || ch === ']') {
      const open = stack.pop();
      if (!open) return text;  // 不匹配，无法修复
      if (stack.length === 0) lastGoodEnd = i;
    }
  }
  return lastGoodEnd > 0 ? text.slice(0, lastGoodEnd + 1) : text;
}
```

---

**合并策略**（对应 Python `extract_and_merge_world_bible` 中的逐段合并）：

```typescript
function mergeIntoBible(
  bible: WorldBible,
  data: any,
  chapterContent: string,
  chapterNum: number
): WorldBible {
  // === 角色合并 ===
  const existingNames = new Set(bible.characters.map(c => c.name));
  for (const chData of data.characters || []) {
    const name = chData.name?.trim();
    if (!name) continue;

    if (existingNames.has(name)) {
      const existing = bible.characters.find(c => c.name === name)!;
      if (chData.traits) existing.traits = chData.traits.slice(0, 500);
      if (chData.status && ['alive','dead','missing','transformed'].includes(chData.status))
        existing.status = chData.status;
      if (chData.aliases) {
        for (const alias of chData.aliases) {
          if (alias && !existing.aliases.includes(alias)) existing.aliases.push(alias);
        }
      }
      existing.importance = higherImportance(existing.importance, chData.importance || 'normal');
      mergeListDedup(existing.key_details, chData.key_details?.map((kd: string) => verifyVerbatim(kd, chapterContent)) || []);
      mergeListDedup(existing.key_dialogues, chData.key_dialogues?.map((kd: string) => verifyVerbatim(kd, chapterContent)) || []);
      if (chData.motivation) existing.motivation = chData.motivation.slice(0, 200);
      if (chData.arc) existing.arc = chData.arc.slice(0, 200);
      // 关系合并（按 target 去重）
      for (const r of chData.relationships || []) {
        if (!existing.relationships.some(rel => rel.target === r.target)) {
          existing.relationships.push(filterRelationshipFields(r));
        }
      }
    } else {
      bible.characters.push(new CharacterEntry({
        name, aliases: chData.aliases || [],
        traits: (chData.traits || '').slice(0, 500),
        status: chData.status || 'alive',
        importance: chData.importance || 'normal',
        first_appearance: chapterNum,
        key_details: chData.key_details?.map((kd: string) => verifyVerbatim(kd, chapterContent)) || [],
        key_dialogues: chData.key_dialogues?.map((kd: string) => verifyVerbatim(kd, chapterContent)) || [],
        motivation: (chData.motivation || '').slice(0, 200),
        arc: (chData.arc || '').slice(0, 200),
        relationships: chData.relationships?.map(filterRelationshipFields) || [],
      }));
      existingNames.add(name);
    }
  }

  // === 地点合并（逻辑与角色合并类似） ===
  const existingLocs = new Set(bible.locations.map(l => l.name));
  for (const locData of data.locations || []) {
    const name = locData.name?.trim();
    if (!name) continue;
    if (existingLocs.has(name)) {
      const existing = bible.locations.find(l => l.name === name)!;
      if (locData.description) existing.description = locData.description.slice(0, 300);
      if (locData.significance) existing.significance = locData.significance.slice(0, 200);
      mergeListDedup(existing.key_details, locData.key_details?.map((kd: string) => verifyVerbatim(kd, chapterContent)) || []);
      if (locData.atmosphere) existing.atmosphere = locData.atmosphere.slice(0, 200);
    } else {
      bible.locations.push(new LocationEntry({
        name, description: (locData.description || '').slice(0, 300),
        significance: (locData.significance || '').slice(0, 200),
        first_appearance: chapterNum,
        key_details: locData.key_details?.map((kd: string) => verifyVerbatim(kd, chapterContent)) || [],
        atmosphere: (locData.atmosphere || '').slice(0, 200),
      }));
      existingLocs.add(name);
    }
  }

  // === 规则合并（去重追加） ===
  for (const rule of data.rules || []) {
    if (rule.trim() && !bible.rules.includes(rule.trim())) {
      bible.rules.push(rule.trim());
    }
  }

  // === 时间线合并（每次追加新条目） ===
  for (const tData of data.timeline || []) {
    if (tData.event?.trim()) {
      bible.timeline.push(new TimelineEntry({
        chapter: chapterNum,
        event: tData.event.slice(0, 200),
        significance: (tData.significance || '').slice(0, 200),
        key_passages: tData.key_passages?.map((kp: string) => verifyVerbatim(kp, chapterContent)) || [],
        foreshadowing_hints: tData.foreshadowing_hints?.map((fh: string) => fh.slice(0, 50)) || [],
      }));
    }
  }

  // === 剧情线合并（按名称匹配去重） ===
  const existingThreads = new Set(bible.active_plot_threads.map(p => p.name));
  for (const ptData of data.plot_threads || []) {
    const name = ptData.name?.trim();
    if (!name) continue;
    if (existingThreads.has(name)) {
      const existing = bible.active_plot_threads.find(p => p.name === name)!;
      if (ptData.status && ['active','resolved','dormant'].includes(ptData.status))
        existing.status = ptData.status;
      if (ptData.description) existing.description = ptData.description.slice(0, 300);
      for (const char of ptData.involved_characters || []) {
        if (char && !existing.involved_characters.includes(char))
          existing.involved_characters.push(char);
      }
      existing.importance = higherImportance(existing.importance, ptData.importance || 'normal');
      mergeListDedup(existing.key_details, ptData.key_details?.map((kd: string) => verifyVerbatim(kd, chapterContent)) || []);
      mergeListDedup(existing.foreshadowing_related, ptData.foreshadowing_related?.map((fr: string) => fr.slice(0, 50)) || []);
    } else {
      bible.active_plot_threads.push(new PlotThread({ name, ... }));
      existingThreads.add(name);
    }
  }

  // === 顶层字段合并：key_worldbuilding / global_foreshadowing / global_key_dialogues ===
  // 按 topic/dialogue/hint 去重追加（策略同上）

  return bible;
}

// 工具函数
function higherImportance(a: string, b: string): string {
  const rank: Record<string, number> = { major: 3, normal: 2, minor: 1 };
  return (rank[b] || 0) > (rank[a] || 0) ? b : a;
}

function mergeListDedup(target: string[], source: string[]): void {
  const seen = new Set(target);
  for (const item of source) {
    if (item && !seen.has(item)) { target.push(item); seen.add(item); }
  }
}

function verifyVerbatim(text: string, source: string): string {
  // 将 LLM 输出的引用文本与源文本做模糊匹配，替换为精确原文
  if (!text || !source) return text;
  if (source.includes(text)) return text;
  // difflib 最佳匹配（Python 版使用 SequenceMatcher）
  const bestStart = source.indexOf(text.slice(0, 20));
  if (bestStart >= 0) return source.slice(bestStart, bestStart + text.length);
  return text;
}

function filterRelationshipFields(r: any): Relationship {
  return { target: r.target || '', type: r.type || '', description: r.description || '' };
}
```

---

**AI 重复检测 + 合并去重**（对应 Python `dedup_world_bible_characters / _detect_duplicate_characters / _merge_character_group`）：

```typescript
// 调用 AI 检测重复角色
async function detectDuplicateCharacters(
  bible: WorldBible
): Promise<string[][]> {
  if (bible.characters.length < 2) return [];

  const charLines = bible.characters.map(c =>
    `- ${c.name} (别名: ${c.aliases.join('、') || '无'}, 描述: ${(c.traits || '').slice(0, 80)})`
  );

  const response = await openai.chat.completions.create({
    model: 'deepseek-chat',
    messages: [{
      role: 'user',
      content: `以下是一部小说的角色列表，请判断哪些角色指向同一个人物：

${charLines.join('\n')}

请将指向同一人物的角色名分组，输出JSON格式：
{"groups": [["角色A", "角色B"], ["角色C", "角色D", "角色E"]]}

规则：
- 只有确定指向同一人物时才归为一组
- 每个角色名只能出现在一个组中
- 不属于任何组的角色不要列出
- 别名不算独立角色，无需合并
- 仅当角色名不同但实际相同才需合并`,
    }],
    max_tokens: 2000,
    temperature: 0.1,
  });

  // 解析 JSON
  const raw = response.choices[0].message.content || '{}';
  const jsonStr = raw.includes('```') ? raw.split('```')[1].split('```')[0].trim() : raw;
  try {
    const data = JSON.parse(jsonStr);
    return data.groups || [];
  } catch { return []; }
}

// 合并一组重复角色（通过拼接，不压缩）
function mergeCharacterGroup(characters: CharacterEntry[], groupNames: string[]): CharacterEntry {
  const matched = characters.filter(c => groupNames.includes(c.name));
  if (matched.length === 0) throw new Error('No matching characters');

  // 按信息完整度排序，最完整的作为 base
  const completeness = (c: CharacterEntry) =>
    c.traits.length + c.key_details.length * 50 + c.key_dialogues.length * 30 + c.relationships.length * 20;
  matched.sort((a, b) => completeness(b) - completeness(a));

  const base = { ...matched[0] };
  for (const other of matched.slice(1)) {
    // 合并别名
    for (const alias of other.aliases) {
      if (!base.aliases.includes(alias)) base.aliases.push(alias);
    }
    if (other.name && !base.aliases.includes(other.name)) base.aliases.push(other.name);

    // 合并 traits（去重行）
    if (other.traits) {
      const baseLines = new Set(base.traits.split('\n'));
      const newLines = other.traits.split('\n').filter(l => l.trim() && !baseLines.has(l));
      if (newLines.length > 0) base.traits = [...base.traits.split('\n'), ...newLines].join('\n');
    }

    // 合并重要性（取高者）、状态（非 alive 优先）、首登场（取最早）
    base.importance = higherImportance(base.importance, other.importance);
    if (other.status !== 'alive') base.status = other.status;
    if (other.first_appearance > 0 && (base.first_appearance === 0 || other.first_appearance < base.first_appearance))
      base.first_appearance = other.first_appearance;

    mergeListDedup(base.key_details, other.key_details);
    mergeListDedup(base.key_dialogues, other.key_dialogues);

    if (other.motivation && !base.motivation.includes(other.motivation))
      base.motivation = base.motivation ? `${base.motivation}；${other.motivation}` : other.motivation;
    if (other.arc && !base.arc.includes(other.arc))
      base.arc = base.arc ? `${base.arc}；${other.arc}` : other.arc;

    // 关系合并（按 target 去重累加描述）
    for (const rel of other.relationships) {
      const existing = base.relationships.find(r => r.target === rel.target);
      if (existing) {
        if (rel.description && !existing.description.includes(rel.description))
          existing.description += `；${rel.description}`;
      } else base.relationships.push(rel);
    }
    if (other.notes) base.notes = [base.notes, other.notes].filter(Boolean).join('\n');
  }

  return base;
}

// 全量去重入口
async function dedupCharacters(bible: WorldBible): Promise<WorldBible> {
  const groups = await detectDuplicateCharacters(bible);
  if (groups.length === 0 || !groups.some(g => g.length > 1)) return bible;

  const toRemove = new Set<number>();
  for (const group of groups) {
    if (group.length < 2) continue;
    const merged = mergeCharacterGroup(bible.characters, group);
    const indicesToRemove = group.map(name => bible.characters.findIndex(c => c.name === name)).filter(i => i > 0);
    indicesToRemove.forEach(i => toRemove.add(i));
    bible.characters = bible.characters.filter((_, i) => !toRemove.has(i) || i === indicesToRemove[0]);
    bible.characters[indicesToRemove[0]] = merged;
  }
  return bible;
}

// 地点去重逻辑与角色类似（略）

#### 6.1.4 core/conversation-manager.ts — 对话管理

**IndexedDB 键结构**：

```
'conversations:index'       → ConvMeta[] (标题 + 时间戳)
'conversations:{convId}'    → Conversation (消息列表 + 参数)
```

**自动保存策略**：
- 每条消息发出后自动保存当前对话
- 切换对话时保存当前，加载目标
- 流式结束后立即保存（不实时写入以避免频繁 I/O）

#### 6.1.5 Vercel API 实现策略

**所有 API 采用统一模式**：

```typescript
// api/summarize.ts
import OpenAI from 'openai';

export async function POST(req: Request) {
  const { text } = await req.json();
  const openai = new OpenAI({
    apiKey: process.env.DEEPSEEK_API_KEY!,
    baseURL: process.env.DEEPSEEK_BASE_URL,
  });
  const completion = await openai.chat.completions.create({
    model: 'deepseek-chat',
    messages: [
      { role: 'system', content: SUMMARIZE_PROMPT },
      { role: 'user', content: text },
    ],
    response_format: { type: 'json_object' },
  });
  const result = JSON.parse(completion.choices[0].message.content!);
  return Response.json(result);
}
```

**统一响应格式**：

```typescript
type ApiResponse<T> =
  | { success: true; data: T }
  | { success: false; error: string; code: 'RATE_LIMITED' | 'INVALID_REQUEST' | 'INTERNAL_ERROR' };
```

#### api/supplement.ts — 内容扩写端点

```typescript
// api/supplement.ts — Vercel Serverless Function
import OpenAI from 'openai';

export const config = { runtime: 'edge' };

export async function POST(req: Request) {
  const {
    originalContent, targetChars, actualChars,
    protagonistBio, backgroundStory, writingDemand,
    worldBibleText, plotContent, historySummary,
    model, temperature,
  } = await req.json();

  const openai = new OpenAI({
    apiKey: process.env.DEEPSEEK_API_KEY!,
    baseURL: process.env.DEEPSEEK_BASE_URL || 'https://api.deepseek.com',
  });

  const prompt = buildSupplementPrompt(
    originalContent, targetChars, actualChars,
    { protagonistBio, backgroundStory, writingDemand, worldBibleText, plotContent, historySummary }
  );

  const completion = await openai.chat.completions.create({
    model: model || 'deepseek-chat',
    messages: [{ role: 'user', content: prompt }],
    max_tokens: Math.min(targetChars * 2, 32768),
    temperature: temperature ?? 0.7,
  });

  const result = completion.choices[0].message.content || '';
  // 校验：结果不得比原文短一半以上
  const cnCount = countCN(result);
  if (cnCount < actualChars * 0.5) {
    return Response.json({ success: false, error: 'Generated content too short', code: 'INVALID_REQUEST' });
  }
  return Response.json({ success: true, data: { content: result, charCount: cnCount } });
}

function buildSupplementPrompt(original: string, target: number, actual: number, ctx: any): string {
  const parts = [
    '你是一位文笔细腻的长篇小说作家。',
    `下面是一章小说的当前版本（当前${actual}字，目标${target}字），字数不足需要扩写。`,
    '',
    '【要求】',
    '1. 保留所有现有情节走向和已写内容，不可删减',
    '2. 丰富细节描写——环境光线/声音/气味、角色神态/动作/微表情、对话语气/肢体语言、内心活动',
    '3. 保持人物性格、语言风格和世界观设定一致',
    '4. 直接输出扩写后的完整章节正文，不添加任何解释或前言',
    '',
  ];
  if (ctx.protagonistBio) parts.push(`【人物设定】\n${ctx.protagonistBio}`);
  if (ctx.backgroundStory) parts.push(`【世界观/背景】\n${ctx.backgroundStory}`);
  if (ctx.writingDemand) parts.push(`【写作要求】\n${ctx.writingDemand}`);
  if (ctx.worldBibleText) parts.push(`【世界书】\n${ctx.worldBibleText}`);
  if (ctx.plotContent) parts.push(`【本章已定情节】\n${ctx.plotContent}`);
  if (ctx.historySummary) parts.push(`【历史生成参考】\n${ctx.historySummary}`);
  parts.push(`【当前章节正文】\n${original}`);
  parts.push('请基于以上设定扩写本章节正文，保留所有现有内容并丰富之。直接输出扩写后的完整章节。');
  return parts.join('\n');
}

function countCN(text: string): number {
  return (text.match(/[一-鿯]/g) || []).length;
}
```

同时更新 `vercel.json` 添加 `"api/supplement.ts": { "maxDuration": 30 }`。

---

### Phase 2 — 策略层 + 工具层 (3-4 天)

**目标**：完成策略模式和工具函数的前端翻译。

#### 6.2.1 策略模式（Interface + 注册表）

```typescript
// strategies/base-strategy.ts
export interface BaseStrategy {
  readonly id: string;
  readonly name: string;
  getSystemPrompt(params: StrategyParams): string;
  getDefaultParameters(): GenerationParams;
  getContextMessages?(bible?: WorldBible, summary?: string): Message[];
}
```

**三种策略实现**：

| 策略 | id | 特点 |
|------|----|------|
| RolePlayStrategy | `roleplay` | 角色/旁白两种回复模式 |
| NovelStrategy | `novel` | 自由对话和章节写作两种模式 |
| ContinuationStrategy | `continuation` | 从源文档续写 |

**注册表模式**（对应 Python `STRATEGY_OPTIONS`）：

```typescript
// strategies/index.ts
export const STRATEGY_REGISTRY: Record<string, BaseStrategy> = {
  roleplay: new RolePlayStrategy(),
  novel: new NovelStrategy(),
  continuation: new ContinuationStrategy(),
};

export function getStrategy(id: string): BaseStrategy {
  const s = STRATEGY_REGISTRY[id];
  if (!s) throw new Error(`Unknown strategy: ${id}`);
  return s;
}
```

**新增策略流程**（与 Python 版一致）：
1. 在 `strategies/` 下创建 `xxx-strategy.ts`，实现 `BaseStrategy` 接口
2. 在 `utils/prompts.ts` 添加对应 System Prompt 模板
3. 在 `strategies/index.ts` 注册到 `STRATEGY_REGISTRY`
4. UI 层的模式选择器自动出现（遍历 registry keys）


#### 6.2.2 System Prompts — utils/prompts.ts（直接复制自 Python 版 115 行）

**4 个策略的 System Prompt 完整翻译**：

| Prompt 名 | 用途 | 核心指令 |
|-----------|------|---------|
| `ROLE_PLAY` | 角色扮演（角色身份） | 第一人称扮演，沉浸式，OOC 协议 |
| `ROLE_PLAY_NARRATOR` | 角色扮演（旁白模式） | 第三人称叙述，对话用「」包裹 |
| `NOVEL_WRITING` | 小说写作辅助 | 构思指导/润色/续写/瓶颈突破 |
| `NOVEL_CHAPTER_WRITING` | 章节生成引擎 | 直接输出正文，字数达标策略 |

**ROLE_PLAY** — 角色扮演核心 prompt：
```
你是一位顶级的角色扮演专家。你将完全沉浸于用户指定的角色之中。
===== 核心规则 =====
1. 【角色身份】：始终以被扮演角色的身份、口吻、知识背景来回复
2. 【人称视角】：使用第一人称"我"
3. 【描写技巧】：加入动作、神态、语气描写（用 * 或（）标注）
4. 【一致性】：严格保持角色性格一致
5. 【主动性】：主动推进剧情
6. 【OOC 协议】：「」括起内容视为 OOC 提问
7. 【拒绝崩皮】：以角色方式拒绝不符合角色的话
===== 输出风格 =====
- 每段回复包含行动、对话和情感三层信息
- 善用环境描写，对话符合角色身份
- 适当留白给用户接话空间
```

**ROLE_PLAY_NARRATOR** — 旁白模式 prompt：
```
你是一位文笔细腻的叙事者，以第三人称旁白视角描述角色的行动、情感与对话。
===== 核心规则 =====
1. 【叙述视角】：始终使用第三人称旁白
2. 【对话格式】：角色台词用「」包裹，附动作/神态说明
3. 【描写层次】：场景环境→角色动作→对话→心理/氛围
4. 【画面感】：五感描写
5. 【节奏控制】：短句加快节奏，长句营造舒缓
6. 【连续推进】：每一段旁白都要推动情节或深化人物
```

**NOVEL_WRITING** — 小说写作导师 prompt：
```
你是一位资深小说写作导师与创意伙伴。
===== 核心能力 =====
1. 【故事构思】：世界观搭建、人物弧光、情节架构、伏笔
2. 【写作指导】：叙事视角、节奏、对话、描写
3. 【文笔润色】：具体修改建议或直接改写
4. 【续写能力】：以匹配的文风续写
5. 【瓶颈突破】：多种方向的突破思路
===== 交流风格 =====
- 给出具体建议而非抽象评价
- 必要时直接示范改写段落
```

**NOVEL_CHAPTER_WRITING** — 章节生成引擎（核心 prompt）：
```
你是一位文笔细腻的长篇小说作家，擅长创作连贯的连载章节。

===== 写作核心要求 =====
1. 【直接输出】：直接输出小说正文，不添加任何解释或作者的话
2. 【章节结构】：开头快速切入→中段推进→结尾留悬念
3. 【描写手法】：五感描写、对话贴合角色、动作与内心交替
4. 【多章连贯】：延续伏笔、保持性格一致、世界观统一
5. 【严格遵守设定】：不得改变已定的人物关系或世界观规则

===== 字数达标策略（重要）=====
1. 【场景细节化】：环境+光线+声音+角色神态
2. 【对话+交互】：动作、表情、停顿、心理活动
3. 【内心世界】：情绪变化和决策权衡
4. 【过渡充实】：沿途见闻和思绪起伏
5. 【密度控制】：每段至少 2-3 层信息
```

---

#### 6.2.3 题材与风格配置 — utils/genre-styles.ts（直接复制自 Python 版 126 行）

**双维度设计**：题材（内容边界）+ 风格基调（文笔气质）

**13 种题材配置**：每项含 `styleInstruction` 和可选 `temperature`/`frequencyPenalty` 覆盖默认参数：

| key | displayName | temperature | freq_penalty | 风格指令摘要 |
|-----|------------|-------------|-------------|------------|
| `xianhuan` | 玄幻/仙侠 | 0.85 | 0.4 | 修炼体系完整，意境描写，功法对决 |
| `qihuan` | 奇幻 | 0.85 | 0.4 | 魔法种族神话自洽，冒险感 |
| `sci_fi` | 科幻 | 0.75 | 0.5 | 科技自洽，逻辑严谨，未来感 |
| `history` | 历史/架空 | 0.70 | 0.6 | 尊重时代背景，历史厚重感 |
| `urban` | 都市/现代 | 0.80 | 0.5 | 贴近现实，对话自然 |
| `suspense` | 悬疑/惊悚 | 0.70 | 0.3 | 节奏紧凑，伏笔回收，反转 |
| `wuxia` | 武侠 | 0.80 | 0.4 | 招式细腻，侠义精神，门派恩怨 |
| `romance` | 言情 | 0.90 | 0.3 | 情感细腻，心理描写，CP 互动 |
| `mo_app` | 末世/生存 | 0.80 | 0.4 | 生存压力，资源管理，人性抉择 |
| `horror` | 恐怖 | 0.75 | 0.2 | 氛围营造，心理压迫，暗示 |
| `light_novel` | 轻小说 | 0.90 | 0.3 | 轻松诙谐，对话占比高，反差萌 |
| `erotic` | 色情 | 0.90 | 0.2 | 情感铺垫，感官描写 |
| `none` | 无特定风格 | null | null | （空） |

**7 种风格基调**：

| key | displayName | 风格指令 |
|-----|------------|---------|
| `default` | 默认 | （空） |
| `light` | 轻快 | 行文节奏明快，对话俏皮，避免沉重 |
| `serious` | 严肃 | 行文庄重克制，注重逻辑和现实感 |
| `literary` | 文青/文艺 | 文字优美，善用比喻意象，注重留白 |
| `dark` | 暗黑 | 基调压抑，人性阴暗面刻画 |
| `passionate` | 热血 | 情绪激昂，节奏紧凑，富有张力 |
| `erotic` | 色情 | 情感铺垫到位，感官描写细腻 |

**TypeScript 类型**：

```typescript
interface GenreConfig {
  key: string;
  displayName: string;
  styleInstruction: string;
  temperature: number | null;
  frequencyPenalty: number | null;
}

interface ToneConfig {
  key: string;
  displayName: string;
  styleInstruction: string;
}

const GENRES: GenreConfig[] = [ /* 13 项 */ ];
const STYLE_TONES: ToneConfig[] = [ /* 7 项 */ ];

// 查询函数
function getGenreByKey(key: string): GenreConfig | undefined;
function getGenreByDisplay(name: string): GenreConfig | undefined;
function getToneByKey(key: string): ToneConfig | undefined;
function getToneByDisplay(name: string): ToneConfig | undefined;
const GENRE_DISPLAY_NAMES: string[];
const TONE_DISPLAY_NAMES: string[];
```

**注入策略**：用户选定题材+风格后，`styleInstruction` 拼接到 `NOVEL_CHAPTER_WRITING` system prompt 尾部。题材的 `temperature`/`frequencyPenalty` 覆盖滑块默认值。

---

#### 6.2.4 AI 分段与世界书提取 — utils/summarize.ts（核心功能，翻译自 Python 版 855 行）

**4 个核心 Prompt 模板**：

**SEGMENT_PROMPT** — AI 语义分段：
```
分析以下文本的话题转折点，在转折处插入分隔标记。
规则：
- 只在话题/场景/时间发生明显转折时插入
- 在转折处插入：<!--BREAK-->
  并在下一行用 ## 写小标题（10字以内）
- 不要改动原文其他任何文字
- 如果全文一气呵成不需要分段，回复：无需分段
```

**EXTRACT_PROMPT** — 逐段世界观提取（同 6.1.3 但含 `{title}` 和 `{dedupContext}` 占位符）：
```
你是一个小说信息深度提取专家。
文本标题：{title}
约束：严格基于原文，宁多勿少
{dedupContext}
[输出 JSON 格式同 6.1.3 的 EXTRACT_PROMPT]
```

**SYNTHESIS_PROMPT** — 跨段落合成去重：
```
你是一个小说信息合成专家。从同一作品的多个段落中分别提取的世界观信息。
核心任务：
1. 合并同名角色，累加 aliases、key_details、key_dialogues
2. 交叉识别跨段落同一人物/地点/剧情线
3. 去重规则、事件、关键细节
4. 重要性更高的版本优先
5. 识别全局伏笔和关键世界观
6. 保留所有原文引用字段
[输出 JSON，同 6.1.3]
```

**BACKGROUND_PROMPT** — 从世界书生成小说设定：
```
你是一位小说设定整理助手。根据结构化世界观信息生成三份参考文本。
约束：严格基于已有设定，不做创造性扩展。
输入：characters / locations / rules / plot_threads / timeline
输出 JSON：
{
  "background_story": "核心设定（300-500字）",
  "protagonist_bio": "人物背景（200-300字）",
  "writing_demand": "3-5条写作指导"
}
```

**核心函数**（TypeScript 翻译策略）：

```typescript
// 入口：AI 语义分段
async function segmentByAI(text: string): Promise<Segment[]> {
  // 发 SEGMENT_PROMPT → 解析 <!--BREAK--> 标记 → 提取 ## 标题
  // Fallback：按 3000 字均匀切块 + AI 取标题
}

// 入口：逐段提取世界观
async function extractWorldBibleFromSegments(
  segments: Segment[], existingBible?: WorldBible
): Promise<WorldBible> {
  // 每段调用 AI + 逐项合并（同名更新/新名追加）
  // >= 3 段时触发 _runSynthesis()
  // 按信息量重算重要等级
}

// 跨段落合成
async function runSynthesis(merged: WorldBible): Promise<void> {
  // 构建累积摘要 → AI 去重合并 → 追加回原数据
}

// 生成小说设定
async function generateNovelSettings(worldData: WorldBible): Promise<NovelSettings> {
  // 格式化数据 → AI(BACKGROUND_PROMPT) → { background_story, protagonist_bio, writing_demand }
}

// 辅助工具
function safeFormat(template: string, vars: Record<string, string>): string;
function parseJSON(text: string): any;  // 解析 JSON（处理 ```json 包裹）
async function callAPI(messages: Message[], model: string): Promise<string>;  // 3 次重试+指数退避

// 旧接口（兼容）
function hasProperSections(text: string): boolean;  // 检测 # 标题
function detectSections(text: string): Segment[];   // 按 # 解析段落
```

---

#### 6.2.5 字数统计与扩写 — utils/supplement.ts（翻译自 Python 版 85 行）

```typescript
// 中文字符统计
function countCN(text: string): number {
  return (text.match(/[一-龿]/g) || []).length;
}

// 内容扩写（当生成字数不足时调用 API 扩写整章）
async function supplementContent(
  originalContent: string,
  targetChars: number,
  actualChars: number,
  context: SupplementContext
): Promise<string> {
  const prompt = buildSupplementPrompt(originalContent, targetChars, actualChars, context);
  const result = await callLLM(prompt, context.model, context.temperature);
  // 校验：结果不得比原文短一半以上
  return countCN(result) >= actualChars * 0.5 ? result : '';
}
```

**扩写 Prompt 结构**：
```
你是一位长篇小说作家。
下面是一章小说的当前版本（当前{actualChars}字，目标{targetChars}字），字数不足需要扩写。

【要求】
1. 保留所有现有情节走向和已写内容，不可删减
2. 丰富细节描写——环境光线/声音/气味、角色神态/动作/微表情、对话语气/肢体语言、内心活动
3. 保持人物性格、语言风格和世界观设定一致
4. 直接输出扩写后的完整章节正文，不添加任何解释或前言

【人物设定】{protagonistBio}
【世界观/背景】{backgroundStory}
【写作要求】{writingDemand}
【世界书】{worldBibleText}
【本章已定情节】{plotContent}
【历史生成参考】{historySummary}
【当前章节正文】{originalContent}

请基于以上设定扩写本章节正文，保留所有现有内容并丰富之。
直接输出扩写后的完整章节。
```

---

#### 6.2.6 导出模块 — utils/export.ts（翻译自 Python 版 596 行）

**三种导出类型 × 四种格式**：

| 导出类型 | TXT | MD | HTML | DOCX |
|---------|-----|----|------|------|
| 单章 `exportChapter()` | ✅ 前端Blob | ✅ 前端Blob | ✅ 前端Blob | Vercel Function |
| 全书 `exportBook()` | ✅ 前端Blob | ✅ 前端Blob | ✅ 前端Blob | Vercel Function |
| 对话 `exportConversation()` | ✅ 前端Blob | ✅ 前端Blob | ✅ 前端Blob | Vercel Function |

**HTML 暗色主题模板**：
```typescript
const HTML_STYLE = `<style>
  body { font-family: -apple-system,"Segoe UI","Noto Sans SC","Microsoft YaHei",sans-serif;
         background:#1a1a2e; color:#e0e0e0; padding:40px; max-width:900px; margin:0 auto; line-height:1.8; }
  h1 { color:#569cd6; border-bottom:2px solid rgba(86,156,214,0.3); }
  h2 { color:#569cd6; border-bottom:1px solid rgba(86,156,214,0.15); }
  h3 { color:#dcdcaa; }
  .user-msg { background:rgba(86,156,214,0.08); border-left:3px solid #569cd6; }
  .assistant-msg { background:rgba(212,220,170,0.06); border-left:3px solid #dcdcaa; }
  pre { background:#0d0d1a; border-radius:8px; padding:14px; }
  code { background:rgba(86,156,214,0.12); border-radius:4px; padding:2px 7px; color:#dcdcaa; }
  blockquote { border-left:3px solid #569cd6; background:rgba(86,156,214,0.05); }
  table { border-collapse:collapse; width:100%; }
  th,td { border:1px solid rgba(255,255,255,0.1); padding:8px 12px; }
  th { background:#0d0d1a; color:#569cd6; }
  .footer { color:#555; font-size:12px; text-align:center; margin-top:50px; }
</style>`;

const HTML_WRAPPER = `<!DOCTYPE html>
<html><head><meta charset="utf-8">{style}</head><body>
{content}
<div class="footer">由 DeepSeek 多功能聊天客户端生成</div>
</body></html>`;
```

**核心导出函数**（Web 版使用 Blob 下载，替代 Python 文件 I/O）：

```typescript
function downloadFile(content: string | Blob, filename: string): void {
  const blob = typeof content === 'string'
    ? new Blob([content], { type: 'text/plain;charset=utf-8' })
    : content;
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url; a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

async function exportChapter(chapter: ChapterNode, format: 'txt'|'md'|'html'|'docx'): Promise<void> {
  switch (format) {
    case 'txt':  downloadFile(chapterToTxt(chapter), filename); break;
    case 'md':   downloadFile(chapterToMd(chapter), filename); break;
    case 'html': downloadFile(chapterToHtml(chapter), filename); break;
    case 'docx': await downloadFromAPI('/api/export/docx', { content: chapter.content, title }); break;
  }
}

async function exportBook(book: BookMeta, chapters: ChapterNode[], format: ExportFormat): Promise<void> {
  // 全书拼接 + 设定 + 目录
}

async function exportConversation(conv: Conversation, format: ExportFormat): Promise<void> {
  // 消息历史 + 元信息
}

// 辅助
function escapeHtml(text: string): string { /* &<> 转义 */ }
function safeFilename(name: string): string { /* 去掉非法字符 */ }

const EXPORT_FORMATS = ['txt', 'md', 'html', 'docx'] as const;
const FORMAT_LABELS: Record<string, string> = {
  txt: '纯文本 (.txt)', md: 'Markdown (.md)', html: 'HTML (.html)', docx: 'Word (.docx)',
};
```

---

### Phase 3 — UI 组件 (10-14 天)

**目标**：实现所有 UI 组件，每个组件覆盖 loading / empty / error / normal 四种状态。

#### 6.3.0 通用状态约定

```typescript
type AsyncState<T> =
  | { status: 'loading' }
  | { status: 'empty' }
  | { status: 'error'; message: string }
  | { status: 'ready'; data: T };
```

#### 6.3.1 布局核心 ⭐

**App.tsx 路由结构**：

```tsx
function App() {
  const { isLoggedIn } = useAuthStore();
  if (!isLoggedIn) return <LoginPage />;
  return (
    <AppLayout>
      <Routes>
        <Route path="/chat" element={<ChatPage />} />
        <Route path="/novel" element={<NovelPage />} />
        <Route path="/continuation" element={<ContinuationPage />} />
        <Route path="/" element={<Navigate to="/chat" />} />
      </Routes>
    </AppLayout>
  );
}
```

**AppLayout**：
```tsx
function AppLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex h-screen bg-bg-primary">
      <Sidebar />
      <div className="flex flex-1 overflow-hidden">
        <div className="flex-1 flex flex-col overflow-hidden">{children}</div>
        <RightPanel />
      </div>
      <SettingsDialog />
    </div>
  );
}
```

**Sidebar 实现策略**：60px flex-col，顶部导航图标组 → 分割线 → 设置按钮 → 📊 Token 按钮 → flex-1 → 底部用户头像。导航状态通过 `useLocation()` 匹配当前路由高亮。

**UserDropdown**：shadcn/ui DropdownMenu，选项：个人信息 / 修改密码 / 注销。

**RightPanel 实现策略**：320px 宽可折叠，内容根据当前路由切换（ChatSidebar / NovelSidebar / ContinuationSidebar），< 900px 自动隐藏。

#### 6.3.1.1 📊 TokenLogDialog — Token 消耗日志

**功能位置**：左侧导航栏设置按钮下方，📊 图标按钮，点击弹出模态对话框。

**数据来源**：DeepSeek API 流式响应的最后一个 chunk 中携带 `usage` 字段：

```typescript
// API 最终返回的 chunk 格式（SSE 最后一条）
data: {
  "choices": [...],
  "usage": {
    "prompt_tokens": 245,
    "completion_tokens": 823,
    "total_tokens": 1068
  }
}
```

**数据类型定义**：

```typescript
// types/token-log.ts
interface TokenLogEntry {
  id: string;
  timestamp: number;           // Date.now() — 请求发起时间
  direction: 'send' | 'receive';
  strategy: string;            // 当前策略：roleplay / novel / continuation
  model: string;               // 使用的模型
  contentPreview: string;      // 内容片段：发送内容前 60 字 / 回复内容前 60 字
  promptTokens: number;
  completionTokens: number;
  totalTokens: number;
}

interface TokenLogStore {
  entries: TokenLogEntry[];    // 内存中（最新在前）
  totalPromptTokens: number;   // 累积统计
  totalCompletionTokens: number;
  totalTokens: number;
  addEntry: (entry: TokenLogEntry) => void;
  clearLog: () => void;
  loadFromDB: () => Promise<void>;
}
```

**Token 捕获流程**：

```
在 chat-client.ts 的流式读取中：

let fullContent = '';
for await (const event of streamChat(messages, model)):
  if (event.type === 'chunk') {
    fullContent += event.content;
  } else if (event.type === 'usage') {  ← 最后一条 event 携带 usage
    tokenLogStore.addEntry({
      id: uuid(),
      timestamp: Date.now(),
      direction: 'receive',
      strategy: currentStrategy,
      model: currentModel,
      contentPreview: fullContent.slice(0, 60) + (fullContent.length > 60 ? '...' : ''),
      promptTokens: event.data.prompt_tokens,
      completionTokens: event.data.completion_tokens,
      totalTokens: event.data.total_tokens,
    });
    // 异步写入 IndexedDB
    saveTokenLogToDB(tokenLogStore.entries);
  }

// 用户发送的消息也在发送时记录（prompt_tokens 在收到回复后才可知）
// 策略：收到 usage event 时，一并记录"发送"和"接收"两条日志
// 发送日志在此时补齐 promptTokens 信息
```

**持久化存储**（IndexedDB）：

```
'token-log:entries' → TokenLogEntry[]  (不加密，不含敏感内容)
```

- 上限保留最近 1000 条记录，超出时删除最旧的
- 日志内容只存 60 字预览片段，不存完整消息
- 不加密（仅存储预览片段和数字，无敏感信息）

**TokenLogDialog UI 实现**：

```
┌─────────────────────────────────────────┐
│ 📊 Token 消耗日志              [✕] 关闭 │
├─────────────────────────────────────────┤
│ 总计: 12,345 prompt / 67,890 completion │
│ = 80,235 tokens                        │
├─────────────────────────────────────────┤
│ 🔍 [搜索日志...]          [🗑 清空]    │
├─────────────────────────────────────────┤
│ ┌─────────────────────────────────────┐ │
│ │ 14:32:15  📤 发送  🎭角色扮演      │ │
│ │ "帮我写一首关于秋天的诗..."          │ │
│ │ prompt: 245  │  completion: 823     │ │
│ │ 总计: 1,068 tokens                 │ │
│ ├─────────────────────────────────────┤ │
│ │ 14:32:18  📥 接收  🎭角色扮演      │ │
│ │ "秋风起，黄叶落，一池碧水泛微波..." │ │
│ │ prompt: 245  │  completion: 823     │ │
│ │ 总计: 1,068 tokens  │  model: v4   │ │
│ ├─────────────────────────────────────┤ │
│ │ 14:28:02  📤 发送  📚小说写作       │ │
│ │ "生成下一章：主角进入遗迹..."        │ │
│ │ prompt: 3,210 │  completion: 2,456  │ │
│ │ 总计: 5,666 tokens                 │ │
│ └─────────────────────────────────────┘ │
├─────────────────────────────────────────┤
│                           [关闭]        │
└─────────────────────────────────────────┘
```

**设计要点**：
- 对话框高度 70vh，宽度 580px，可滚动
- 每条日志显示：时间（精确到秒）、方向图标（📤发送/📥接收）、策略标签、内容预览（60 字截断）、token 消耗三列
- 列表按时间倒序（最新在上）
- 顶部搜索框：按内容预览文本模糊过滤
- 底部统计栏：累计 prompt / completion / total
- 清空按钮带 ConfirmDialog 确认
- 点击外部区域或按 Escape 关闭

#### 6.3.2 ChatPage — 聊天模式

**组件树**：

```
ChatPage
├── PageHeader (title + strategy badge)
├── MessageList
│   └── MessageItem[]
│       ├── MessageAvatar
│       └── MessageBubble (MarkdownView / PlainText + Timestamp)
├── ChatInput (textarea + send/stop button)
└── ChatSidebar (右侧面板)
    ├── ModelSelector
    ├── PresetSelector
    ├── ParameterSliders (temperature, top_p, freq_p, max_tokens)
    ├── ConversationHistory (下拉 + 保存/加载/删除)
    └── ActionButtons (清除/导出)
```

**MessageList 自动滚动**：`useEffect` 监听 messages.length 和 streamContent，`scrollIntoView({ behavior: 'smooth' })`。

**MessageList 虚拟滚动（性能优化）**：

消息超过 200 条时，必须使用虚拟滚动避免渲染过多 DOM 节点。

```typescript
// 使用 @tanstack/react-virtual (npm install @tanstack/react-virtual)
function MessageListVirtual({ messages }: { messages: Message[] }) {
  const parentRef = useRef<HTMLDivElement>(null);
  const virtualizer = useVirtualizer({
    count: messages.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => 120,  // 估计行高
    overscan: 10,
  });
  return (
    <div ref={parentRef} className="overflow-auto h-full">
      <div style={{ height: `${virtualizer.getTotalSize()}px`, position: 'relative' }}>
        {virtualizer.getVirtualItems().map((vi) => (
          <div key={messages[vi.index].id} style={{ position: 'absolute', top: 0, left: 0, width: '100%', transform: `translateY(${vi.start}px)` }}>
            <MessageItem message={messages[vi.index]} />
          </div>
        ))}
      </div>
    </div>
  );
}
```

**阈值**：200 条以下正常渲染（无虚拟化开销）。**自动滚动**：仅当用户已位于底部 150px 范围内时滚动到底。

**ChatInput**：textarea 自动高度（48-120px），Ctrl+Enter 发送，Enter 换行，streaming 时显示停止按钮。

**消息气泡**：user 右对齐蓝色渐变，assistant 左对齐暗色背景 + Markdown 渲染。

**时间戳策略**：
- 每条消息在发送/接收完成时记录 `timestamp: number`（`Date.now()`），存入消息数据结构
- 展示格式：今天显示 `HH:mm:ss`，昨天显示 `昨天 HH:mm:ss`，更早显示 `MM/DD HH:mm:ss`
- 时间戳显示在消息气泡内部右下角，使用 `text-xs text-text-muted` 样式，与其他文本颜色区分
- 流式输出期间，最后一条 assistant 消息不显示时间戳（仍在生成中），待 streaming 结束后才渲染
- 对话历史加载时，从 IndexedDB 读取原始 timestamp 并重新计算相对时间
- 时间戳不参与消息内容的加密，但作为消息对象的一部分整体加密存储

```typescript
// Message 数据结构
interface Message {
  id: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  timestamp: number;  // Date.now()
}

// 时间戳格式化函数（精确到秒）
function formatTimestamp(ts: number): string {
  const date = new Date(ts);
  const now = new Date();
  const isToday = date.toDateString() === now.toDateString();
  const isYesterday = new Date(now.getTime() - 86400000).toDateString() === date.toDateString();
  const time = date.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  if (isToday) return time;
  if (isYesterday) return `昨天 ${time}`;
  return `${date.getMonth() + 1}/${date.getDate()} ${time}`;
}
```

**预设方案表**：

| 预设 | temperature | top_p | freq_penalty |
|------|-------------|-------|-------------|
| 保守 | 0.3 | 0.85 | 0.3 |
| 中庸 | 0.7 | 0.90 | 0.0 |
| 狂野 | 1.2 | 0.95 | -0.2 |

#### 6.3.3 NovelPage — 写作模式

**组件树**：

```
NovelPage
├── PageHeader (title + active chapter badge + tree depth indicator)
├── NovelToolbar (生成下一章 / 保存 / 🌳 章节树管理 / 世界书 / 字数统计)
│   └── 生成下一章 → 弹出 ChapterRegenerationDialog（输入本章剧情要求）
├── ChapterEditor (textarea)
└── NovelSidebar (右侧面板)
    ├── BookshelfPanel (选择/新建/重命名/删除)
    ├── ChapterTree (迷你树预览，显示 activePath 高亮)
    │   └── ChapterTreeNode (递归组件，展开/折叠/点击切换章节)
    ├── SettingsEditor (主角/世界观/写作要求)
    ├── GenreSelector (题材/风格)
    └── WorldBibleButton
```

**生成下一章流程**（树形上下文）：

```
点击"生成下一章"
  → 保存当前章节
  → assembleContext(book, chapters)  // 沿 activePath 从根到当前组装
  → 弹出 ChapterRegenerationDialog
     ├── 上下文预览（全书设定 + 每章概要 + 原提示词）
     ├── 用户编辑本章剧情要求（userDirection）
     └── 点击确认
  → formatContextForPrompt(assembly) + 用户输入的 prompt
  → streamChat → 流式输出到编辑器
  → 生成完成
  → 创建新 ChapterNode { parentId: 当前章Id, childrenIds: [], siblingOrder: N }
  → 更新父节点 childrenIds
  → 追加到 activePath
  → 调用 /api/summarize 生成本章概要（summary）
  → 更新 BookMeta.activePath
  → 树预览刷新
```

**重写/润色流程**：

```
右键/点击章节 → 弹出操作菜单：
  ├── 📝 润色本章
  │   → 弹出 ChapterRegenerationDialog（模式=润色）
  │   → 显示当前章节全文（只读预览）
  │   → 用户输入润色要求
  │   → AI: 原全文 + 润色要求 → 返回新全文
  │   → 创建新节点作为本节点的兄弟（同 parentId）
  │   → 自动 summary 提炼
  │
  ├── 🔄 重写本章
  │   → 弹出 ChapterRegenerationDialog（模式=重写）
  │   → 显示从根到父节点的上下文概要（只读）
  │   → 显示本章原 userDirection（可编辑）
  │   → 用户修改提示词
  │   → AI: 上下文 + 新 prompt → 返回新章节
  │   → 创建新节点作为本节点的兄弟
  │   → 自动 summary 提炼
  │
  ├── ➕ 插入中间章
  │   → 在当前节点和父节点之间插入新章节
  │   → 重新设置 parentId 关系
  │
  └── 🗑 删除本章（含所有子节点）
      → ConfirmDialog 确认
      → 递归删除子树
```

**章节切换**（沿树导航）：

```
点击 ChapterTree 中的某个节点：
  1. 如果当前章节有未保存内容 → 提示保存
  2. 加载目标章节到编辑器
  3. 从该节点回溯到根，构造新的 activePath
  4. 更新 BookMeta.activePath
  5. 更新编辑器和树高亮

快捷键：
  Alt+↑ / Alt+↓     → 在同级兄弟间切换
  Alt+←              → 切换到父章节
  Alt+→              → 切换到活跃子章节
```

**ChapterTree 组件设计**（右侧面板）：

```
📚 星辰之旅           ← 书名
┌──────────────────┐
│ 🌳 章节树          │
│                   │
│ 📘 第1章·启程 ◀─── │  ← 根节点（activePath[0]）
│ ├── 📘 第2章·雨夜  │  ← 原路线
│ │   ├── 📘 第3章   │     ← 活跃（activePath[2]）
│ │   └── 📄 第3章   │     ← 其他分支（灰色）
│ └── 📄 第2章·改    │  ← 改写版（兄弟节点）
│     └── 📄 第3章   │
│                   │
│ [+ 生成下一章]     │  ← 按钮：在当前活跃节点下创建子节点
└──────────────────┘

📘 = 活跃路径上的节点（蓝色高亮）
📄 = 非活跃分支（灰色）
▶ = 可展开（有子节点）
```

**自动保存**：内容变化后 30 秒无操作自动保存（`useDebounce`）。

#### 6.3.4 ContinuationPage — 续写模式

**组件树**：

```
ContinuationPage
├── PageHeader
├── ContToolbar (上传/分析/生成)
├── ContSplitView (源文档预览 | 续写编辑器)
└── ContinuationSidebar
    ├── BookshelfPanel (复用)
    ├── ContinuationControls (要求/剧情/字数/扩写)
    └── AnalysisPanel (分析结果)
```

**文件上传**：`<input type="file" accept=".txt,.md">` → `FileReader.readAsText()`。

**分析流程**：`/api/summarize`（语义分段）+ `/api/extract-world`（提取世界观）→ 展示在 AnalysisPanel → 用户确认续写方向。

#### 6.3.5 通用对话框

**SettingsDialog**（模态，非页面）：API Key 修改 / 主题 / 加密状态 / 数据导出。

**TokenLogDialog**（模态，非页面）：见 6.3.1.1 详细设计。

**WorldBibleDialog**（📖 6 标签页，完整翻译 Python 版 421 行）：

**UI 结构**：

```
┌─────────────────────────────────────────┐
│ 📖 世界书 - 已建立的设定与世界观  [✕]   │
├─────────────────────────────────────────┤
│ 以下是从已生成章节中自动提取的世界观设定  │
│ 修改后点击保存生效。                      │
├─────────────────────────────────────────┤
│ ┌──────┬──────┬──────┬──────┬──────┬──┐ │
│ │ 角色 │ 地点 │ 规则 │时间线│剧情线│..│ │ ← 6 个标签页
│ ├──────┴──────┴──────┴──────┴──────┴──┤ │
│ │                                      │ │
│ │  纯文本编辑区域 (QTextEdit)            │ │
│ │                                      │ │
│ │  【角色名】                           │ │
│ │    别名：xxx                          │ │
│ │    重要性：重要                        │ │
│ │    描述：性格描写...                   │ │
│ │    ...                                │ │
│ │                                      │ │
│ └──────────────────────────────────────┘ │
│                          [💾 保存] [关闭] │
└─────────────────────────────────────────┘
```

**6 个标签页的内容格式化**（只读 → 用户可编辑的纯文本）：

| 标签页 | 格式化方法 | 内容示例 |
|--------|-----------|---------|
| 角色 | `formatCharacters()` | `【林深】\n  别名：无\n  重要性：重要\n  描述：28岁考古学家...\n  状态：alive\n  动机：寻找星辰之门...\n  首登场：第1章` |
| 地点 | `formatLocations()` | `【遗迹大厅】\n  描述：宏伟的地下空间...\n  重要度：高\n  氛围：庄严神秘\n  首登场：第1章` |
| 规则 | `rules.join('\n')` | `# 规则列表\n魔法不能凭空产生...` |
| 时间线 | `formatTimeline()` | `- 第1章：林深接受考古任务（引出主线）\n  📄 原文段落：...\n  🔮 伏笔：遗迹与星辰之门有关` |
| 剧情线 | `formatPlotThreads()` | `【寻找星辰之门】（active）\n  重要性：重要\n  描述：主角寻找传说中的星辰之门...\n  涉及角色：林深、陈雨` |
| 设定与伏笔 | `formatWorldbuilding()` | `## 世界观设定段落\n【星辰之门】\n  远古文明的传送装置...\n  （第1章）` |

**每个标签页的解析策略**（保存时将纯文本解析回数据结构）：

**角色解析** — `parseCharactersFromText(text)`：
```
逐行扫描：
  【名称】 → 新角色开始，记录 name
  别名： → split("、") → aliases[]
  重要性： → "重要"→"major" / "普通"→"normal" / "次要"→"minor"
  描述： → traits
  状态： → status
  动机： → motivation
  成长弧线： → arc
  备注： → notes
  📌 关键细节： → key_details[]
  💬 关键台词： → key_dialogues[]
  首登场： → first_appearance (提取数字)
  关系：friend(目标名) → Relationship { type, target }
```

**地点解析** — `parseLocationsFromText(text)`：
```
  【名称】 → 新地点
  描述： → description
  重要度： → significance
  氛围： → atmosphere
  📌 关键描写： → key_details[]
  首登场： → first_appearance
```

**时间线解析** — `parseTimelineFromText(text)`：
```
  - 第N章：事件描述（意义）
  📄 原文段落： → key_passages[]
  🔮 伏笔： → foreshadowing_hints[]
```

**剧情线解析** — `parsePlotThreadsFromText(text)`：
```
  【名称】（status）
  重要性： → importance
  描述： → description
  涉及角色： → involved_characters (split "、")
  📌 关键细节： → key_details[]
  🔮 关联伏笔： → foreshadowing_related[]
```

**设定与伏笔解析** — `parseWorldbuildingFromText(text)`：
```
  ## 世界观设定段落 → section=passages
  ## 全局伏笔 → section=foreshadowing
  ## 关键对话 → section=dialogues
  【topic】→ 新设定主题
  （第N章）→ 当前设定的 chapter
  🔮 hint → 关联：relates_to → foreshadowing[]
  💬 speaker：dialogue（context） → dialogues[]
```

**保存逻辑**：

```typescript
function onSave(): void {
  // 6 个标签页分别解析
  const charText = charEditor.getText();
  if (charText && !charText.includes('尚未提取到')) {
    bible.characters = parseCharactersFromText(charText);
  }

  const locText = locEditor.getText();
  if (locText && !locText.includes('尚未提取到')) {
    bible.locations = parseLocationsFromText(locText);
  }

  // 规则直接按行分割
  const ruleText = ruleEditor.getText();
  if (ruleText && !ruleText.includes('尚未提取到')) {
    bible.rules = ruleText.split('\n')
      .filter(l => l.trim() && !l.startsWith('#'));
  }

  // 时间线/剧情线/设定伏笔 → 对应解析函数
  bible.timeline = parseTimelineFromText(timelineEditor.getText());
  bible.active_plot_threads = parsePlotThreadsFromText(plotEditor.getText());
  const [passages, foreshadowing, dialogues] = parseWorldbuildingFromText(wbEditor.getText());
  bible.key_worldbuilding_passages = passages;
  bible.global_foreshadowing = foreshadowing;
  bible.global_key_dialogues = dialogues;

  // 调用 saveCallback 或 accept()
  saveCallback?.(bible);
}
```

**对话框 Props 接口**：

```typescript
interface WorldBibleDialogProps {
  bible: WorldBible;
  saveCallback?: (bible: WorldBible) => void;
}

// 使用方式：
const dialog = new WorldBibleDialog({ bible, saveCallback });
dialog.open(); // => 返回 Promise<WorldBible> (用户点击保存后 resolve)
```

**与 Python 版的关键差异**：
- Python 版继承 `QDialog`，使用 `QTabWidget` + `QTextEdit`（原生 Qt 控件）
- Web 版使用 shadcn/ui Tabs + Textarea 组件
- Web 版保存时直接调用 `saveCallback` 而不是通过信号槽
- 解析逻辑完全一致（正则 + 行扫描），直接翻译

**ChapterManagerDialog**（🌳 全屏树形管理）：

```
┌─────────────────────────────────────────────┐
│ 🌳 章节树管理 · 星辰之旅          [✕] 关闭  │
├─────────────────────────────────────────────┤
│ ┌──────────────┐ ┌─────────────────────────┐│
│ │  章节树      │ │  章节详情               ││
│ │              │ │                         ││
│ │ 📘 第1章·启程│ │  章节编号: 第3章         ││
│ │ ├── 📘 第2章 │ │  标题: 星辰之门的守护者   ││
│ │ │   ├── 📘 3│ │  字数: 6,432             ││
│ │ │   ├── 📄 3│ │  创建: 2026/06/09 14:32  ││
│ │ │   └── 📄 3│ │  最后修改: 14:35          ││
│ │ └── 📄 第2章│ │                         ││
│ │     └── 📄 3│ │  📋 本章概要:            ││
│ │              │ │  林深进入遗迹，遇到...   ││
│ │  [+ 新章]    │ │                         ││
│ │  [🗑 删除]   │ │  📝 生成提示词:          ││
│ │              │ │  "主角进入遗迹后遇到...  ││
│ └──────────────┘ └─────────────────────────┘│
├─────────────────────────────────────────────┤
│ 活跃路径: 第1章 → 第2章 → 第3章 (当前)      │
│ [切换分支] [设为首章] [导出全书]             │
└─────────────────────────────────────────────┘
```

**设计要点**：
- 左侧树形导航 + 右侧详情面板（split 布局）
- 树节点图标：📘=活跃路径  📄=分支  📝=当前编辑中
- 点击树节点 → 右侧显示章节详情（概要、提示词、元信息）
- 右键树节点 → 上下文菜单（润色/重写/插入/删除）
- 底部显示当前活跃路径 breadcrumb
- 拖拽节点可调整兄弟节点顺序（siblingOrder）
- [切换分支] → 选中某个树节点，以它为终点更新 activePath
- 章节概要（summary）在每次保存/生成完成后自动调用 `/api/summarize` 提炼

**ChapterTreeNode 递归组件实现**：

```tsx
function ChapterTreeNode({ node, activePath, depth }: Props) {
  const isActive = activePath.includes(node.id);
  const children = node.childrenIds.map(id => chapters.get(id)!);

  return (
    <div style={{ marginLeft: depth * 20 }}>
      <div className={`tree-node ${isActive ? 'active' : ''}`}
           onClick={() => switchToChapter(node.id)}
           onContextMenu={(e) => showContextMenu(e, node)}>
        <span>{isActive ? '📘' : '📄'} {node.title}</span>
        <span className="text-muted">{node.displayNumber}</span>
      </div>
      {isExpanded && children.map(child =>
        <ChapterTreeNode node={child} activePath={activePath} depth={depth + 1} />
      )}
    </div>
  );
}
```

**ChapterTree 虚拟滚动（性能优化）**：

章节超过 100 可见行时，先展平树为行数组再虚拟化：

```typescript
function flattenTree(nodes: ChapterNode[], expanded: Set<string>, depth = 0): FlattenedRow[] {
  const result: FlattenedRow[] = [];
  for (const node of nodes) {
    result.push({ id: node.id, title: node.title, depth, node });
    if (expanded.has(node.id)) {
      const children = node.childrenIds.map(id => chapters.get(id)).filter(Boolean) as ChapterNode[];
      result.push(...flattenTree(children, expanded, depth + 1));
    }
  }
  return result;
}
```

使用 `@tanstack/react-virtual` 对展平后的数组做虚拟化渲染，阈值 100 行。

**ChapterRegenerationDialog**（类 Git 提交信息编辑器风格）：

```
┌────────────────────────────────────────────┐
│ 🔄 重写章节 · 第3章 · 星辰之门的守护者      │
├────────────────────────────────────────────┤
│                                            │
│ 📋 上下文预览（沿活跃路径 → 不可编辑）       │
│ ┌────────────────────────────────────────┐ │
│ │ 【全书设定】                             │ │
│ │ 主角：林深，28岁，考古学家...            │ │
│ │ 世界观：近未来架空世界...                │ │
│ │                                         │ │
│ │ 【剧情概要】                             │ │
│ │ 第1章概要：林深接受考古任务...            │ │
│ │ 第2章概要：林深抵达遗迹入口...            │ │
│ │   → 原写作要求：描述遗迹的宏伟壮观         │ │
│ └────────────────────────────────────────┘ │
│                                            │
│ 📝 本章剧情要求（可编辑）                    │
│ ┌────────────────────────────────────────┐ │
│ │ 主角进入遗迹后遇到星辰之门的守护者，      │ │
│ │ 展开一场关于远古文明的对话...             │ │
│ └────────────────────────────────────────┘ │
│                                            │
│ 🎯 生成方式                                │
│ ○ 润色 — 基于当前全文，按需求调整           │
│ ● 重写 — 基于上下文 + 概要，重新创作        │
│                                            │
│ [💾 保存并生成]  [取消]                     │
└────────────────────────────────────────┘
```

**润色模式界面差异**：
- 上下文预览区域 → 显示当前章节全文（只读）
- 可编辑区域 → "润色要求"（非剧情要求）
- 示例："增强战斗场面的紧张感，补充环境描写"

**ConfirmDialog**：通用确认弹窗，支持 danger/default 变体。

#### 6.3.6 认证页面

**LoginPage**：全屏居中，登录/注册双模式切换。用户名 + 密码（+ 确认密码），密码强度检测（>= 6 位，含字母+数字）。

**ProfilePage**：显示用户名，修改密码，注销。

---

### Phase 4 — 集成 + 部署 (4-6 天)

**目标**：整合所有模块，搭建 CI/CD，部署到 Vercel 生产环境。

#### 6.4.1 路由整合

```tsx
function App() {
  const { isLoggedIn } = useAuthStore();
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route element={<ProtectedRoute />}>
          <Route element={<AppLayout />}>
            <Route path="/chat" element={<ChatPage />} />
            <Route path="/novel" element={<NovelPage />} />
            <Route path="/continuation" element={<ContinuationPage />} />
          </Route>
          <Route path="/profile" element={<ProfilePage />} />
        </Route>
        <Route path="*" element={<Navigate to="/chat" />} />
      </Routes>
      <Toaster />
    </BrowserRouter>
  );
}
```

#### 6.4.2 暗色主题统一
- Tailwind 自定义 CSS 变量，所有组件使用主题色
- shadcn/ui 使用默认暗色主题

#### 6.4.3 错误处理边界
- API 调用错误 → toast 通知
- 加密错误 → 强制登出（密钥失效）
- IndexedDB 错误 → "数据存储异常，请检查浏览器设置"
- React 渲染错误 → ErrorBoundary 降级 UI

#### 6.4.4 TypeScript 严格模式

```json
{
  "compilerOptions": {
    "strict": true,
    "noUncheckedIndexedAccess": true,
    "noUnusedLocals": true,
    "noUnusedParameters": true
  }
}
```

#### 6.4.5 部署流程

```bash
npm run build              # → dist/
npx vercel deploy --prod   # → Vercel 生产
```

#### 6.4.6 Vercel 环境变量

| Name | Value |
|------|-------|
| `DEEPSEEK_API_KEY` | `sk-...` |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com` |

本地开发在 `.env.local` 中设置。

#### 6.4.7 CI/CD 流水线 (GitHub Actions)

```yaml
# .github/workflows/deploy.yml
name: Deploy
on:
  push: { branches: [main] }
  pull_request: { branches: [main] }
jobs:
  quality:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with: { node-version: 20 }
      - run: npm ci
      - run: npm run lint
      - run: npm run typecheck        # tsc --noEmit
      - run: npm run test             # vitest run
      - run: npm run build
  deploy:
    needs: quality
    if: github.ref == 'refs/heads/main'
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: amondnet/vercel-action@v25
        with:
          vercel-token: \${{ secrets.VERCEL_TOKEN }}
          vercel-org-id: \${{ secrets.VERCEL_ORG_ID }}
          vercel-project-id: \${{ secrets.VERCEL_PROJECT_ID }}
```

package.json 新增 scripts：`"typecheck": "tsc --noEmit"`, `"lint": "eslint src/ --ext .ts,.tsx"`, `"test": "vitest run"`。

#### 6.4.8 代码分割 (Code Splitting)

页面级组件使用 `React.lazy`：

```typescript
const ChatPage = React.lazy(() => import('./components/chat/ChatPage'));
const NovelPage = React.lazy(() => import('./components/novel/NovelPage'));
const ContinuationPage = React.lazy(() => import('./components/continuation/ContinuationPage'));

// 路由中包裹 Suspense
<Suspense fallback={<LoadingSpinner />}>
  <Routes>...</Routes>
</Suspense>
```

重弹窗（WorldBibleDialog、ChapterManagerDialog、TokenLogDialog、SettingsDialog）也做 lazy load。

vite.config.ts 配置 manualChunks 分离 vendor 包：

```typescript
build: {
  rollupOptions: {
    output: {
      manualChunks: {
        vendor: ['react', 'react-dom', 'react-router-dom'],
        markdown: ['react-markdown', 'remark-gfm', 'rehype-highlight'],
        state: ['zustand'],
      },
    },
  },
}
```

#### 6.4.9 构建分析

生产构建后使用 `rollup-plugin-visualizer` 生成分析报告：

```typescript
// vite.config.ts (production only)
import { visualizer } from 'rollup-plugin-visualizer';
plugins: [visualizer({ filename: 'dist/stats.html', open: true })];
```

#### 6.4.10 错误监控

```typescript
// src/hooks/use-error-monitoring.ts
window.addEventListener('error', (e) => {
  console.error('[App Error]', e.error);
  // 可选：上报到自建 Sentry 或简单记录到 localStorage
});
window.addEventListener('unhandledrejection', (e) => {
  console.error('[Unhandled Promise]', e.reason);
});
```

---

### 6.5 整体数据流图

```
用户操作 (点击/输入)
    │
    ▼
UI 组件 → dispatch → Zustand Store
    │                      ├──→ re-render (UI 更新)
    │                      ├──→ IndexedDB 持久化 (加密)
    │                      └──→ fetch('/api/xxx') → Vercel Function → DeepSeek API
    │                                                                    │
    └──────────────────────────── SSE Stream / JSON Response ←────────────┘
```

### 6.6 关键交互流程

**聊天发消息**：
1. 用户输入 → 点击发送 → chatStore.sendMessage(text)
2. 用户消息追加到 messages[]，创建空 assistant 占位
3. streaming = true, fetch POST /api/chat
4. for await (chunk of stream) → 追加到末尾 assistant 消息
5. streaming = false, 自动保存到 IndexedDB

**小说生成下一章**：
1. 保存当前章节 → 获取 system prompt + 前情提要 + 世界书
2. streamChat → 流式输出到 currentContent
3. 完成后创建新章节记录 → 写入 IndexedDB → 更新章节列表

**续写完整流程**：
1. 上传源文档 → FileReader → store.sourceContent
2. POST /api/summarize → 分段结果
3. POST /api/extract-world → WorldBible 结构化数据
4. 用户确认续写方向 → streamChat → 编辑 → 保存到书架

---

## 7. 与桌面版的关键差异（阅读原始代码时注意）

| 差异 | Python 版做法 | Web 版做法 | 影响 |
|------|-------------|-----------|------|
| 数据存储 | 文件系统 (bookshelf/用户名/目录) | IndexedDB (按 key 存取) | 核心层大部分函数签名要改 |
| 用户系统 | 多用户目录隔离 (UUID 目录 + users.json) | 浏览器多账号 (IndexedDB 按 username 隔离) | 无需服务器，注册即在本机创建账号记录 |
| 登录认证 | PBKDF2 验证 + 加密密钥派生 (64 字节分两段) | PBKDF2 派生密钥 → SHA-256(密钥) 做验证器 | 流程简化一半，不分段派生 |
| API Key | 用户在界面输入，保存在配置文件 | **无** — API Key 配置在 Vercel 环境变量 | 用户无需关心 API Key |
| 续写源文档 | 读取本地文件路径 | 用户上传文件 | 增加 FileUploader 组件 |
| 文件导出 | 保存到本地目录 | 浏览器下载 (Blob) | 导出逻辑简化，不再需要选择路径 |
| 加密 | Fernet (AES-128-CBC + HMAC) | AES-256-GCM (Web Crypto) | 格式不兼容，无法读取旧加密文件 |
| Markdown 渲染 | QWebEngineView 完整浏览器引擎 | react-markdown | 某些 CSS 效果可能有差异 |
| 流式输出 | threading + pyqtSignal | async/await + ReadableStream | 代码大量简化 |

---

## 8. 部署配置

### vercel.json

```json
{
  "functions": {
    "api/chat.ts": { "maxDuration": 60 },
    "api/extract-world.ts": { "maxDuration": 30 }
  },
  "rewrites": [
    { "source": "/(.*)", "destination": "/index.html" }
  ]
}
```

### 环境变量 (Vercel Dashboard)

| 变量 | 说明 |
|------|------|
| `DEEPSEEK_API_KEY` | DeepSeek API Key |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com` |

### GitHub Pages 镜像

如果用 Vercel 做主站，Pages 可以放重定向（可选）：
```html
<!-- largeoyos.github.io 的 index.html -->
<meta http-equiv="refresh" content="0; url=https://your-app.vercel.app">
```

---

## 9. 关键决策记录

| 决策 | 选项 | 选择 | 理由 |
|------|------|------|------|
| 部署平台 | Vercel vs Pages vs 自建 | **Vercel** | Serverless Functions 解决 CORS 和 DOCX 问题 |
| 持久化 | IndexedDB vs localStorage vs OPFS | **IndexedDB (idb-keyval)** | 容量大 (~GB 级)，支持结构化数据 |
| 状态管理 | Zustand vs Redux vs Jotai | **Zustand** | 最接近 Python 版的可变状态风格 |
| 加密 | Web Crypto vs libsodium-wasm | **Web Crypto** | 浏览器原生，无额外包体积 |
| 布局 | PyQt6 左侧 QScrollArea + QStackedWidget | **导航侧栏 (60px) + 主内容区 + 可折叠右侧面板** | 仿 Discord/Notion 布局，模式切换由 React Router 驱动 |
| 前端路由 | 无（QStackedWidget 切换面板） | **React Router v6** | 每个模式独立 URL，支持浏览器前进后退 |
| 右侧面板 | 所有控件的固定 QGroupBox | **按模式切换的独立 Sidebar 组件** | 每个模式只显示相关的面板，UI 更清爽 |
| API 集成 | Serverless Functions vs 第三方网关 | **Serverless Functions** | 同一部署，无需额外服务 |
| UI 库 | shadcn/ui vs Ant Design vs MUI | **shadcn/ui** | 按需引入，风格自由 |
| CSS | Tailwind vs CSS Modules vs styled | **Tailwind** | 快速迭代，shadcn 原生支持 |

---

## 10. 原始文件与目标文件对照表

| Python 文件 | 行数 | 目标 TS 文件 | 迁移方式 |
|------------|------|-------------|---------|
| `gui_main.py` | 10 | `src/main.tsx` | 重写 |
| `config.py` | 41 | — (环境变量 + 常量) | 分散到各模块 |
| `ui/main_window.py` | 5,190 | 全 `components/` + `stores/` | 重构为组件树 |
| `ui/login_dialog.py` | 154 | `components/auth/LoginDialog.tsx` | 重写 |
| `ui/world_bible_dialog.py` | 421 | `components/world-bible/*.tsx` | 重写 |
| `ui/continuation_dialogs.py` | 907 | `components/continuation/*.tsx` | 重写 |
| `ui/presets.py` | 14 | `components/settings/PresetManager.tsx` | 翻译 |
| `core/chat_client.py` | 324 | `core/chat-client.ts` | 翻译 (调 `/api/chat`) |
| `core/auth_manager.py` | 226 | `core/crypto.ts` | 简化为 Web Crypto |
| `core/novel_manager.py` | 976 | `core/novel-manager.ts` | 翻译 (文件IO → IndexedDB) |
| `core/conversation_manager.py` | 225 | `core/conversation-manager.ts` | 翻译 |
| `core/world_bible.py` | 864 | `core/world-bible.ts` | 翻译 |
| `strategies/base_strategy.py` | 67 | `strategies/base-strategy.ts` | 翻译 |
| `strategies/*.py` (3个) | 271 | `strategies/*-strategy.ts` (3个) | 翻译 |
| `utils/prompts.py` | 115 | `utils/prompts.ts` | 直接复制 |
| `utils/export.py` | 596 | `utils/export.ts` + `api/export/docx.ts` | 拆分前后端 |
| `utils/summarize.py` | 855 | `utils/summarize.ts` + `api/summarize.ts` | 拆分前后端 |
| `utils/supplement.py` | 85 | `utils/supplement.ts` | 翻译 |
| `utils/genre_styles.py` | 126 | `utils/genre-styles.ts` | 直接复制 |
| — | — | `api/chat.ts` | 新建 (Vercel Function) |
| — | — | `api/extract-world.ts` | 新建 (Vercel Function) |
| — | — | `hooks/*.ts` (5个) | 新建 |
| — | — | `types/*.ts` (5个) | 新建 |

---

## 11. 验收标准

- [ ] `vercel dev` 启动，访问 localhost
- [ ] 登录/注册 → 进入主界面
- [ ] 聊天：选策略 → 发消息 → 流式输出 → Markdown 渲染
- [ ] 书架：创建书 → 写章节 → 多版本切换 → 删除书
- [ ] 世界书：手动编辑 → AI 提取 → 合并 → 注入 prompt
- [ ] 续写：上传 .txt/.md → AI 分析 → 选择方向 → 生成续写
- [ ] 导出：TXT / MD / HTML (前端 Blob) + DOCX (API)
- [ ] 加密：设置密码 → 刷新 → 输入密码 → 数据可读
- [ ] 收藏/切换模型/调参数/预设管理
- [ ] `vercel --prod` 部署后可正常使用

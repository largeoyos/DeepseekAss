# UI 架构与交互逻辑详细文档（Python 桌面版）

> 参考源：`ui/main_window.py`（5190 行）、`ui/login_dialog.py`（154 行）、
> `ui/world_bible_dialog.py`（421 行）、`ui/continuation_dialogs.py`（907 行）、
> `ui/presets.py`（14 行）

---

## 1. 整体布局

```
┌──────────────────────────────────────────────────────────────┐
│                        QMainWindow                           │
│  标题: "DeepSeek 多功能聊天客户端"  尺寸: 1200×780           │
├──────────────────┬───────────────────────────────────────────┤
│  左侧面板        │  右侧面板                                  │
│  (QScrollArea)   │  (QWidget, VBox)                           │
│                  │                                            │
│  固定宽度: 280   │  QSplitter (450:750)                       │
│  可滚动          │                                            │
│                  │  ┌─────────────────────────────────────┐   │
│  ┌────────────┐  │  │  QWebEngineView                    │   │
│  │ 聊天模式   │  │  │  Markdown 渲染显示区                │   │
│  │ 模型选择   │  │  │  (stretch=1)                        │   │
│  │ 生成参数   │  │  │                                      │   │
│  │ 预设方案   │  │  └─────────────────────────────────────┘   │
│  │ 操作按钮   │  │  ┌─────────────────────────────────────┐   │
│  │ 对话历史   │  │  │  底部输入区 (QFrame)                │   │
│  │ 状态信息   │  │  │                                      │   │
│  │            │  │  │  [输入框: InputTextEdit] [发送] [停] │   │
│  └────────────┘  │  └─────────────────────────────────────┘   │
│  ┌────────────┐  │                                            │
│  │ QStackedW  │  │                                            │
│  │ idx0:角色  │  │                                            │
│  │ idx1:小说  │  │                                            │
│  │ idx2:续写  │  │                                            │
│  └────────────┘  │                                            │
└──────────────────┴───────────────────────────────────────────┘
```

---

## 2. 左侧面板（固定面板元素）

### 2.1 📌 聊天模式 — `QGroupBox`

| 控件 | 类型 | ID | 交互 | 影响 |
|------|------|----|------|------|
| 模式下拉框 | `QComboBox` | `_mode_combo` | `currentTextChanged → _on_mode_changed` | 切换策略、QStackedWidget 面板、清空/保存对话 |

**选项来源：** `STRATEGY_OPTIONS = {"角色扮演": RolePlayStrategy, "小说写作": NovelStrategy, "续写小说": ContinuationStrategy}`

**_on_mode_changed 完整逻辑：**
1. 如果 `_streaming == True` → 拒绝切换，弹回 `_last_mode`
2. 如果当前对话有内容（user/assistant 消息）且不在加载中 → 弹出三选一对话框：
   - "保存并切换" → 调用 `_on_save_conversation` 后继续
   - "不保存，直接切换"
   - "取消" → 弹回 `_last_mode`
3. 更新 `_last_mode`，调用 `_client.switch_strategy(strategy)`
4. 清空 `_current_conversation_id`（非加载时）
5. 同步模型下拉框到新策略的推荐模型
6. 切换 `QStackedWidget` 到对应面板（idx 0/1/2）
7. 仅角色扮演模式显示"操作"和"对话历史"面板
8. 小说/续写模式自动刷新书架
9. 在显示区渲染对应策略的欢迎消息

### 2.2 🧠 模型选择 — `QGroupBox`

| 控件 | 类型 | ID | 交互 |
|------|------|----|------|
| 模型下拉框 | `QComboBox` | `_model_combo` | `currentTextChanged → _on_model_changed` |

**选项来源：** `MODEL_OPTIONS = ["deepseek-v4-flash", "deepseek-v4-pro"]`

**_on_model_changed：** 调用 `_client.switch_model(model)` → `_update_status()`

### 2.3 ⚙️ 生成参数 — `QGroupBox`

#### 预设方案行

| 控件 | 类型 | ID | 交互 |
|------|------|----|------|
| 标签 | `QLabel` | "预设方案" | 固定宽度 60 |
| 预设下拉框 | `QComboBox` | `_preset_combo` | `currentTextChanged → _on_preset_changed` |

**选项来源：** `COMBO_ITEMS = ["自定义", "保守", "中庸", "狂野"]`

**_on_preset_changed：**
1. 如果 text == "自定义" → 直接返回
2. 从 `PRESETS` 字典查找并设置所有四个滑块值（`_preset_applying = True` 防止滑块事件改回"自定义"）
3. `_update_status()`

**PRESETS 定义：**
- "保守": temp=30, top_p=50, fp=30, max_tokens=32768
- "中庸": temp=70, top_p=90, fp=0, max_tokens=32768
- "狂野": temp=90, top_p=100, fp=10, max_tokens=32768

#### 温度滑块

| 控件 | 类型 | ID | 交互 |
|------|------|----|------|
| 标签 | `QLabel` | "温度" | 固定宽度 36 |
| 滑块 | `QSlider` (H) | `_temp_slider` | 范围 0-200，刻度间隔 50，初始 = client.recommended_temperature × 100 |
| 数值 | `QLabel` | `_temp_value` | 固定宽度 36，居中 |

**_on_temp_changed：** value/100 → `_client.set_temperature()` → 更新标签 → `_update_status()` → 如果不是自定义预设 → 切回"自定义"

#### top_p 滑块

| 控件 | 类型 | ID | 交互 |
|------|------|----|------|
| 标签 | `QLabel` | "top_p" | 固定宽度 36 |
| 滑块 | `QSlider` (H) | `_top_p_slider` | 范围 0-100，刻度间隔 25 |
| 数值 | `QLabel` | `_top_p_value` | 固定宽度 36 |

> 交互逻辑同温度滑块

#### frequency_penalty 滑块

| 控件 | 类型 | ID | 交互 |
|------|------|----|------|
| 标签 | `QLabel` | "freq_p" | 固定宽度 36 |
| 滑块 | `QSlider` (H) | `_fp_slider` | 范围 -200 到 200，刻度间隔 50 |
| 数值 | `QLabel` | `_fp_value` | 固定宽度 36 |

#### max_tokens 数字输入

| 控件 | 类型 | ID | 交互 |
|------|------|----|------|
| 标签 | `QLabel` | "max_tk" | 固定宽度 36 |
| 数字框 | `QSpinBox` | `_mt_spin` | 范围 1-300000，步进 512，ToolTip 提示 20000 中文字需 ≥40000 |

> 任何滑块/数字变化且当前预设不是"自定义"时，自动切回"自定义"。

### 2.4 操作按钮 — `QGroupBox`（仅角色扮演模式可见）

| 按钮 | 文本 | ID | 交互 | 风格 |
|------|------|----|------|------|
| 清除对话 | 🗑️ 清除对话 | — | `clicked → _on_clear` | 红色系背景 |
| 修改 API Key | 🔑 修改 API Key | — | `clicked → _on_change_api_key` | 蓝色系背景 |

**_on_clear：** `_client.clear_context()` + 重置显示到 INITIAL_HTML + 清除 `_current_conversation_id`

**_on_change_api_key：**
1. 弹出 `QInputDialog` 输入新 Key（显示当前 Key 的前 12 位 + 后 4 位）
2. 如果未变更 → 提示"API Key 未变更"
3. 调用 `_verify_api_key(key)` 验证（通过 `OpenAI.models.list()` 轻量调用）
4. 验证通过 → 加密保存 → 更新 `_client.raw_client.api_key`
5. 验证失败 → 弹窗报错

### 2.5 💬 对话历史 — `QGroupBox`（仅角色扮演模式可见）

| 控件 | 类型 | ID | 交互 |
|------|------|----|------|
| 保存按钮 | `QPushButton` | — | `clicked → _on_save_conversation` |
| 历史下拉框 | `QComboBox` | `_history_combo` | `currentIndexChanged → _on_history_selection_changed` |
| 加载按钮 | `QPushButton` | "📂 加载" | ToolTip: "加载选中的对话历史" |
| 删除按钮 | `QPushButton` | "🗑" | 最大宽度 40 |
| 状态标签 | `QLabel` | `_history_status_label` | 自动换行，显示"暂无已保存对话"或预览 |
| 导出格式下拉框 | `QComboBox` | `_hist_export_format_combo` | 选项: FORMAT_LABELS |
| 导出对话按钮 | `QPushButton` | "📤 导出对话" | |

**_on_save_conversation：**
1. 获取消息列表，过滤出 user/assistant 消息
2. 如果为空 → 警告
3. 弹出 `QInputDialog` 输入标题（预填 `_current_conversation_title`）
4. 如果已有 `_current_conversation_id` → 弹出"更新已有 vs 另存为新 vs 取消"三选一
5. 根据策略类型保存额外字段（角色描述、故事背景、回复模式）
6. 调用 `_conversation_manager.save_conversation()`
7. 更新 `_current_conversation_id` 和 `_current_conversation_title`
8. 刷新历史列表，选中刚保存的条目

**_on_load_conversation：**
1. 获取选中的 conversation_id
2. 加载对话记录
3. 如果消息超过 50 条，只取最近 50 条
4. 尝试自动切换策略/模式（读取记录中的 strategy 字段，旧文件通过角色描述推断）
5. 设置 `_loading_conversation = True` 防止切换时弹出保存提示
6. 调用 `_client.import_messages(messages)`
7. 恢复角色面板的角色描述、故事背景、回复方式、单选框状态
8. 同步模型和滑块
9. 重新渲染完整对话

**_on_delete_conversation：** 确认对话框 → `_conversation_manager.delete_conversation()` → 刷新列表

**_on_history_selection_changed：** 获取预览文本 → 更新状态标签

### 2.6 📋 状态 — `QGroupBox`

| 控件 | 类型 | ID |
|------|------|----|
| 状态标签 | `QLabel` | `_status_label` |
| 流式计数标签 | `QLabel` | `_stream_count_label` |

`_update_status()` 显示：模式、模型、温度、top_p、freq_p、max_tk
`_stream_count_label` 仅在流式输出时可见，显示"⏳ 已接收 N 字符"

---

## 3. QStackedWidget 面板

### 3.0 面板切换规则

| 策略 | 面板索引 | 面板 ID |
|------|---------|---------|
| 角色扮演 | 0 | `_role_play_panel` |
| 小说写作 | 1 | `_novel_panel` |
| 续写小说 | 2 | `_continuation_panel` |

### 3.1 🎭 角色扮演面板 (idx 0) — `_build_role_play_panel()`

| 控件 | 类型 | 交互 | 说明 |
|------|------|------|------|
| 角色描述 | `QTextEdit` | `textChanged → _on_role_char_changed` | 最大高度 100，最小 70，Placeholder 描述角色 |
| 故事背景 | `QTextEdit` | `textChanged → _on_role_bg_changed` | 最大高度 100，最小 70 |
| 回复方式: 角色回答 | `QRadioButton` | 默认选中 | id=0 |
| 回复方式: 旁白描述 | `QRadioButton` | — | id=1 |
| 单选框组 | `QButtonGroup` | `idClicked → _on_reply_mode_changed` | 切换立即更新 system prompt，不重置对话 |
| 应用设定按钮 | `QPushButton` | `clicked → _on_apply_role_settings` | 绿色渐变背景 |

**_on_apply_role_settings：**
1. 同步角色描述、故事背景、回复方式到 strategy
2. 调用 `_client.update_system_prompt()`
3. 在显示区追加一条 system-msg 通知，不覆盖已有对话

### 3.2 📚 小说写作面板 (idx 1) — `_build_novel_panel()`

#### 3.2.1 书架行

| 控件 | 类型 | ID | 交互 |
|------|------|----|------|
| 书架下拉框 | `QComboBox` | `_bookshelf_combo` | `currentTextChanged → _on_book_selected`，最小宽度 120 |
| 新建按钮 | `QPushButton` | — | "➕ 新建"，最小宽度 70 |
| 删除按钮 | `QPushButton` | — | "🗑 删除"，最小宽度 70 |
| 重命名按钮 | `QPushButton` | — | "✏️ 重命名"，最小宽度 70 |

**_on_create_book：** `QInputDialog` 输入标题 → 检查重名 → `_novel_manager.create_book()` → 刷新书架 → 选中新书

**_on_delete_book：** 确认对话框 → `_novel_manager.delete_book()` → 刷新 → 选中第一本

**_on_rename_book：** `QInputDialog` 输入新标题（预填当前名）→ 检查重名 → `_novel_manager.rename_book()`

**_on_book_selected：**
1. 设置标题输入框文本
2. 从 meta.json 加载 protagonist_bio、background_story、writing_demand → 填入对应编辑框（blockSignals）
3. 显示"已有 N 章，下一章编号: 第M章"
4. 同步题材/风格下拉框
5. 同步题材/风格到续写面板的对应下拉框
6. 同步所有设定到 strategy 对象

#### 3.2.2 小说标题与章节标题

| 控件 | 类型 | ID | 交互 |
|------|------|----|------|
| 标题输入 | `QLineEdit` | `_novel_title_edit` | `textChanged → _on_novel_title_changed` |
| 章节标题输入 | `QLineEdit` | `_chapter_title_edit` | `textChanged → _on_chapter_title_changed` |
| 章节信息标签 | `QLabel` | `_chapter_info_label` | 自动换行 |

#### 3.2.3 章节模式开关

| 控件 | 类型 | ID | 交互 |
|------|------|----|------|
| 章节续写模式 | `QCheckBox` | `_chapter_mode_check` | `toggled → _on_chapter_mode_toggled` |

**_on_chapter_mode_toggled：** 设置 `strategy.chapter_mode`，在显示区追加通知消息

#### 3.2.4 🎨 题材与风格（独立 QGroupBox）

| 控件 | 类型 | ID | 交互 |
|------|------|----|------|
| 题材下拉框 | `QComboBox` | `_novel_genre_combo` | `currentTextChanged → _on_novel_genre_changed` |
| 风格下拉框 | `QComboBox` | `_novel_tone_combo` | `currentTextChanged → _on_novel_tone_changed` |

**_on_novel_genre_changed：**
1. 从 `genre_styles.py` 获取配置
2. 同步到 strategy 的 genre 字段
3. 调用 `_apply_genre_params()` 设置推荐温度/frequency_penalty，并将预设切回"自定义"
4. 同步到续写面板的下拉框
5. 如果当前有选中书名 → 持久化到 meta.json

**_on_novel_tone_changed：** 同步到 strategy → 同步到续写面板下拉框 → 持久化

#### 3.2.5 设定编辑区

| 控件 | 类型 | 高度 | Placeholder |
|------|------|------|-------------|
| 主角设定 | `QTextEdit` | 最大 80/最小 60 | "描述主角背景、性格、外貌..." |
| 世界观/背景 | `QTextEdit` | 最大 80/最小 60 | "描述世界观、时代背景、核心设定..." |
| 写作要求 | `QTextEdit` | 最大 60/最小 48 | "本章具体写作要求（风格、节奏、必须包含的元素...）" |
| 全局提示词按钮 | `QPushButton` | — | "🌐 编辑全局偏好提示词" |

**🌐 编辑全局偏好提示词：**
- 弹出模态 `QDialog`（550×350）
- 包含说明文字 + `QTextEdit` + "取消" / "确定" 按钮
- 确定后保存到 `_client.global_user_prompt` 并通过 `_save_global_user_prompt()` 加密持久化

#### 3.2.6 本章情节与字数

| 控件 | 类型 | 范围/默认 | 说明 |
|------|------|-----------|------|
| 本章情节输入 | `QTextEdit` | 最大高度 120/最小 80 | Placeholder: 填写关键情节，AI 扩展 |
| 字数 | `QSpinBox` | 100-100000, 默认 10000, 步进 500, 后缀" 字" |
| 自动扩写 | `QCheckBox` | 默认选中 | "字数不足时自动扩写" |

#### 3.2.7 🚀 生成下一章按钮

| 控件 | 类型 | 高度 | 交互 |
|------|------|------|------|
| 生成下一章 | `QPushButton` | 最小 40 | `clicked → _on_generate_chapter` |

**_on_generate_chapter 完整逻辑：**
1. 检查 `_streaming` 和 `_chapter_finalized` → 任一不满足则返回
2. 验证标题和章节标题存在（为空时自动生成）
3. 设置 `_chapter_finalized = False`，禁用生成按钮
4. 重置取消状态，显示停止按钮
5. 禁用模式切换，设置 `_streaming = True`
6. 同步 UI 值到 strategy 对象
7. 保存当前设定到 meta.json
8. 追加用户消息
9. 在主线程捕获 plot_content 和 target_words（避免后台线程访问 QWidget）
10. 启动后台线程 `_run_chapter_generation`

**章节生成后台流程 (`_run_chapter_generation`)：**
1. 构建 System Prompt（小说写作导师） + strategy.build_system_messages()
2. 调用 `_build_chapter_prompt()` 构造 User Prompt（含智能前情提要、世界书、历史记录、设定）
3. 调用 API（非流式）
4. 通过信号发送章节内容到显示区
5. 确定版本号（新章节 v1，已有章节 v+1）
6. 保存章节版本 → 信号通知路径
7. 如果已有旧版本 → 提示用户选择版本
8. 保存生成历史记录（含 prompt、参数、剧情走向）
9. 如果是新章节 → 提炼剧情摘要 → 追加到 plot_summary
10. 字数补充检查 → 不足则自动扩写（调用 `supplement_content`），扩写版本存为新版本并设为活跃
11. 更新世界书（调用 `extract_and_merge_world_bible`）
12. 刷新章节信息显示
13. 发送 finished 信号

#### 3.2.8 保存/加载设定

| 按钮 | 文本 | 交互 |
|------|------|------|
| 保存小说设定 | 💾 保存小说设定 | `clicked → _on_save_novel_settings` |
| 加载小说设定 | 📂 加载小说设定 | `clicked → _on_load_novel_settings` |

**_on_save_novel_settings：** 验证标题 → 收集所有输入框值 → `_novel_manager.create_book()` + `save_meta()` → 刷新书架

**_on_load_novel_settings：** 根据当前选中书 → 从 meta.json 加载 → 填入编辑框

#### 3.2.9 章节管理与世界书

| 按钮 | 文本 | 交互 |
|------|------|------|
| 章节管理 | ⚙ 章节管理（查看 / 删除 / 选择版本） | `clicked → _on_manage_chapters` |
| 世界书 | 📖 世界书 | `clicked → _on_world_bible` |

**_on_manage_chapters：** 打开 `ChapterManagerDialog`（内嵌类，详见第 5 节）

**_on_world_bible：** `_novel_manager.load_world_bible()` → 打开 `WorldBibleDialog` → 保存

#### 3.2.10 导出

| 控件 | 类型 | 说明 |
|------|------|------|
| 导出格式下拉框 | `QComboBox` | `_export_format_combo`，选项来自 `EXPORT_FORMATS` |
| 导出当前章节 | `QPushButton` | 导出最新一章 |
| 导出全书 | `QPushButton` | 导出全部章节 |

**_on_export_chapter：** 获取最新一章 → `QFileDialog.getSaveFileName()` → `export_chapter()` → 提示结果

**_on_export_book：** `QFileDialog.getSaveFileName()` → `export_book()` → 提示结果

**导出格式映射：** txt → `.txt` / md → `.md` / html → `.html` / docx → `.docx`

---

### 3.3 📄 续写小说面板 (idx 2) — `_build_continuation_panel()`

#### 3.3.1 ① 源文档选择（续写独有）

| 控件 | 类型 | 交互 |
|------|------|------|
| 源文档路径 | `QLineEdit` | 只读，Placeholder "未选择文件..." |
| 浏览(文件) | `QPushButton` | "浏览"，最大宽度 60 → `_on_browse_continue_file` |
| 源文件夹路径 | `QLineEdit` | 只读，Placeholder "未选择文件夹..." |
| 浏览(文件夹) | `QPushButton` | "浏览"，最大宽度 60 → `_on_browse_continue_folder` |
| 分析源文档 | `QPushButton` | "🔍 分析源文档并导入设定"，蓝色系 → `_on_analyze_continuation` |
| 直接续写 | `QPushButton` | "⚡ 直接续写"，橙色系 → `_on_start_continuation` |

**_on_browse_continue_file：** `QFileDialog.getOpenFileName()` 过滤 `*.txt *.md *.html *.htm` → 清除文件夹路径

**_on_browse_continue_folder：** `QFileDialog.getExistingDirectory()` → 清除文件路径

#### 3.3.2 ② 书架与章节管理（续写版，结构同小说面板）

| 控件 | ID | 交互 |
|------|----|------|
| 书架下拉框 | `_cont_bookshelf_combo` | `currentTextChanged → _on_cont_book_selected` |
| 新建按钮 | — | `_on_cont_create_book` |
| 删除按钮 | — | `_on_cont_delete_book` |
| 重命名按钮 | — | `_on_cont_rename_book` |
| 章节标题输入 | `_cont_chapter_title_edit` | |
| 章节信息标签 | `_cont_chapter_info_label` | |
| 章节模式 | `_cont_chapter_mode_check` | `toggled → _on_cont_chapter_mode_toggled` |
| 题材下拉框 | `_cont_genre_combo` | `_on_cont_genre_changed` |
| 风格下拉框 | `_cont_tone_combo` | `_on_cont_tone_changed` |

> 续写面板的书架与小说面板共享 `_novel_manager`，数据完全互通。题材/风格变更时会互相同步。

#### 3.3.3 ③ 小说设定（续写版）

| 控件 | ID | 对应小说面板 |
|------|----|-------------|
| 主角设定 | `_cont_protagonist_edit` | `_protagonist_edit` |
| 世界观/背景 | `_cont_background_edit` | `_background_edit` |
| 写作要求 | `_cont_demand_edit` | `_demand_edit` |
| 全局提示词按钮 | — | 同一个 handler `_on_edit_global_prompt` |
| 保存小说设定 | — | `_on_cont_save_settings` |
| 加载小说设定 | — | `_on_cont_load_settings` |
| 章节管理 | — | 同一个 handler `_on_manage_chapters` |
| 世界书 | — | 同一个 handler `_on_world_bible` |

> 注意：续写面板的设定编辑框与小说面板是**独立控件**（不同 ID），数据通过 `_novel_manager.load_meta/save_meta` 同步。

#### 3.3.4 ④ 续写操作

| 控件 | 类型 | 范围/默认 | 说明 |
|------|------|-----------|------|
| 续写要求 | `QTextEdit` | 最大 80/最小 60 | 风格、视角、节奏、必须包含的元素 |
| 字数 | `QSpinBox` | 100-100000, 默认 10000, 步进 500 | |
| 自动扩写 | `QCheckBox` | 默认选中 | |
| 续写剧情 | `QTextEdit` | 最大 80/最小 60 | 可选，剧情走向、关键事件 |
| AI 建议按钮 | `QPushButton` | "🎲 AI 建议发展方向"，绿色 | `_on_cont_panel_suggest` |
| 我指定剧情 | `QPushButton` | "📝 我指定剧情"，棕色 | `_on_cont_panel_specify` |
| 生成下一章 | `QPushButton` | 最小高度 40，紫色渐变 | `_on_cont_generate_chapter` |

**_on_cont_panel_suggest：**
1. 检查 `_cont_analysis_settings` 是否存在
2. 从 settings 提取 background_story
3. 调用 `_build_cont_plot_context()` 构建剧情上下文
4. 启动后台线程 → 调用 `suggest_directions()`（LLM 生成 3-5 个方向）
5. 完成时通过信号打开 `DirectionSelectionDialog`
6. 用户选择后 → `_do_continuation_with_context()`

**_on_cont_panel_specify：** 类似，但跳过 AI 建议，直接进入续写

**_on_cont_generate_chapter：** 续写版生成入口，逻辑同 `_on_generate_chapter` 但使用 `_run_continuation`

**续写后台流程 (`_run_continuation`)：**
1. 构建 User Prompt：原文内容 + 前情提要 + 世界书 + 设定 + 续写要求 + 剧情走向 + 用户偏好
2. 构建 Messages：NOVEL_CHAPTER_WRITING system prompt + 核心设定/人物背景/风格设定 system messages + user prompt
3. 调用 API（非流式）
4. 保存章节版本
5. 保存生成历史
6. 提炼摘要
7. 字数补充检查 → 扩写
8. 更新世界书
9. 刷新章节信息

#### 3.3.5 导出（续写版）

| 控件 | ID |
|------|----|
| 导出格式下拉框 | `_cont_export_format_combo` |
| 导出当前章节 | `_on_export_cont_chapter` |
| 导出全书 | `_on_export_cont_book` |

> 逻辑与小说面板完全相同，仅使用续写面板的格式下拉框和书架选中项。

---

## 4. 右侧面板

### 4.1 Markdown 渲染区

| 控件 | 类型 | 说明 |
|------|------|------|
| QWebEngineView | `_display` | 启用 JavaScript，背景色 #1e1e1e，最小高度 300 |

**渲染函数链：**

| 函数 | 触发时机 | 行为 |
|------|---------|------|
| `_append_user_message(text)` | 发送/章节生成/续写开始 | 转义 HTML → JS 在页面 body 追加 `.user-msg` div |
| `_render_assistant_stream(text)` | 流式 token | Markdown → HTML → 更新/创建 `#stream-container` |
| `_render_assistant_message(text, callback)` | 流式完成 | 替换 `#stream-container` 为 `.assistant-msg`，带 JS 回调 |
| `_render_full_conversation(messages)` | 加载对话 | 一次性拼接全部消息的 HTML → `setHtml()` |

### 4.2 底部输入区

| 控件 | 类型 | 说明 |
|------|------|------|
| 输入框 | `InputTextEdit` (自定义) | Placeholder "输入消息，按 Ctrl+Enter 发送..."，最大高度 120/最小 64 |
| 发送按钮 | `QPushButton` | "发 送"，最小高度 64/宽度 80，蓝色渐变 |
| 停止按钮 | `QPushButton` | "⏹"，最小高度 64/宽度 80，红色渐变，默认不可见 |

**_on_send：**
1. 如果 `_streaming` 且超过 180 秒 → 强制复位（允许重新发送）
2. 如果 `_streaming` 且未超时 → 直接返回
3. 如果是小说模式 + 章节模式 → 调用 `_on_generate_chapter()` 并返回
4. 如果是续写模式 + 章节模式 → 调用 `_on_cont_generate_chapter()` 并返回
5. 获取用户输入 → 清空输入框 → 追加用户消息
6. 重置取消状态 → 显示停止按钮 → 禁用模式切换
7. 启动后台线程 `_run_stream`

**_run_stream：** `for token in _client.chat_stream(user_input)` → 通过信号发送每个 token

**_on_stop：** 隐藏停止按钮 → `_client.cancel()`

**流式状态管理：**

| 变量 | 类型 | 说明 |
|------|------|------|
| `_streaming` | bool | 全局流式锁 |
| `_streaming_start_time` | float | 超时检测（180 秒强制复位） |
| `_assistant_text_buffer` | list[str] | 累积 token |
| `_chapter_finalized` | bool | 章节渲染完成锁 |

**_on_stream_finished：** 如果被取消 → 追加"已取消"消息 → `_on_chapter_rendering_done()`；否则 → `_render_assistant_message()` 带回调

**_on_chapter_rendering_done：** 释放 streaming 和 chapter_finalized 锁，启用生成/模式切换按钮

**_on_stream_error：** 释放所有锁 → 隐藏停止按钮 → 如果不是用户取消 → 弹 `QMessageBox.critical`

---

## 5. 内嵌对话框

### 5.1 ChapterManagerDialog（章节管理）

定义在 `_on_manage_chapters()` 方法内（第 4440-4798 行），模态对话框 500×400。

**内部信号：**
- `_regenerate_done_signal(int, int, str)` — chapter_num, version, file_path
- `_regenerate_error_signal(str)` — error message
- `_rebuild_done_signal()` — 摘要重建完成
- `_rebuild_error_signal(str)` — 摘要重建失败

| 控件 | 交互 | 说明 |
|------|------|------|
| 章节列表 | `QListWidget` | 显示 ⭐ 标记活跃版本，活跃版本青色文字，双击预览 |
| 预览 | 👁 预览 | `itemDoubleClicked` 或点击按钮 → 弹出只读 QTextEdit 内容 |
| 设为活跃 | ⭐ 设为活跃（计入剧情） | 切换活跃版本 → 询问是否重建剧情摘要 |
| 重新生成 | 🔁 重新生成 | 基于当前左侧面板设定重新创作 → 保存为新版本 v+1 |
| 删除此版本 | 🗑 删除此版本 | 红色背景，确认 → 删除版本，自动切换到最新版 |
| 关闭 | 关闭 | 默认按钮 |

**重新生成逻辑：**
1. 查找该版本的生成历史记录（加载 requirement 和 plot）
2. 构建 system messages：NOVEL_CHAPTER_WRITING + 核心设定/人物背景/写作要求 + 世界书
3. 加载前情提要
4. 参考旧版本开头（用于保持风格一致性）
5. 调用 API → 保存为新版本 → 更新世界书

**重建摘要逻辑：**
1. 后台线程调用 `_novel_mgr.rebuild_summary_from_active()`
2. 完成时刷新列表和章节信息

---

## 6. 独立对话框

### 6.1 LoginDialog（登录/注册）

`ui/login_dialog.py`，尺寸 380×300，模态。

**双模式切换：**

| 控件 | ID | 登录模式 | 注册模式 |
|------|----|---------|---------|
| 窗口标题 | — | "DeepSeekAss - 登录" | "DeepSeekAss - 注册" |
| 用户名输入 | `_username_input` | ✅ 显示 | ✅ 显示 |
| 密码输入 | `_password_input` | ✅ 显示 | ✅ 显示 |
| 确认密码标签 | `_confirm_label` | ❌ 隐藏 | ✅ 显示 |
| 确认密码输入 | `_confirm_input` | ❌ 隐藏 | ✅ 显示 |
| 登录按钮 | `_login_btn` | ✅ 显示 | ❌ 隐藏 |
| 注册按钮 | `_register_btn` | ❌ 隐藏 | ✅ 显示 |
| 切换按钮 | `_switch_btn` | "没有账号？去注册" | "已有账号？去登录" |

**密码流：**
- 登录：`AuthManager.authenticate(username, password)` → 成功则设置 `self.username`, `self.enc_key`, 调用 `accept()`
- 注册：验证两次密码一致 → `AuthManager.register(username, password)` → 设置字段 → 提示"密码丢失后将无法恢复数据" → `accept()`

### 6.2 WorldBibleDialog（世界书编辑器）

`ui/world_bible_dialog.py`，尺寸 800×600，模态。

**标签页：**

| 标签 | 数据源 | 格式 |
|------|--------|------|
| 角色 | `WorldBible.characters` | `【角色名】\n  别名：\n  重要性：\n  描述：\n  关系：\n  动机：\n  角色弧光：` |
| 地点 | `WorldBible.locations` | `【地点名】\n  描述：\n  关键事件：` |
| 规则 | `WorldBible.rules` | 每行一条规则 |
| 时间线 | `WorldBible.timeline` | `【章节 N】事件 | 重要性 | 涉及角色` |
| 剧情线 | `WorldBible.active_plot_threads` | `【剧情线名】(状态)\n  描述：\n  涉及角色：\n  伏笔：` |
| 设定与伏笔 | `WorldBible.key_worldbuilding_passages` + `global_foreshadowing` + `global_key_dialogues` | 三个分节 |

每个标签页使用 `QTextEdit` 编辑文本。保存时通过行解析和分隔符重新解析为结构化数据。

| 按钮 | 交互 |
|------|------|
| 💾 保存 | `_on_save` → 解析各标签页文本 → 更新 WorldBible 对象 → `self._saved = True` |
| 关闭 | `close()` → 注意：没有未保存提示 |

**解析规则（保存时）：**
- 角色：以 `【】` 分隔，`重要性：`、`描述：`、`关系：`、`动机：`、`弧光：` 行解析
- 地点：以 `【】` 分隔
- 规则：逐行作为独立规则
- 时间线：逐行解析 `【章节 N】` 和 `|` 分隔符
- 剧情线：以 `【】` 分隔，`描述：`、`角色：`、`伏笔：` 行解析
- 设定/伏笔/对话：以 `##` 分隔

### 6.3 SectionPreviewDialog（段落预览）

`ui/continuation_dialogs.py`，用于源文档分析前确认段落分割。

| 控件 | 说明 |
|------|------|
| 段落列表 | `QListWidget` 显示 AI 识别的语义段落标题 |
| 预览区域 | 选中段落后显示完整内容 |
| 模式：文件 | 单文件模式，展示 AI 分段结果 |
| 模式：文件夹 | 文件夹模式，展示文件列表，可多选 |

**两种模式（由 `mode` 参数控制）：**
- `"analyze"` — 仅预览 + 确认段落
- `"continue"` — 预览 + 确认后直接续写

### 6.4 ContinuationAnalysisDialog（分析结果展示）

`ui/continuation_dialogs.py`，分析完成后弹出，展示提取的世界观数据。

| 区域 | 内容 |
|------|------|
| 标签页：角色/地点/规则/剧情 | 展示提取结果 |
| 标签页：设定 | 展示生成的小说设定 |
| AI 建议 | 按钮：进入方向建议流程 |
| 我指定剧情 | 按钮：进入手动指定流程 |

### 6.5 DirectionSelectionDialog（方向选择）

`ui/continuation_dialogs.py`，展示 LLM 生成的 3-5 个发展方向。

| 控件 | 说明 |
|------|------|
| 方向列表 | 单选，每个显示标题 + 看点 + 情节走向 |
| 确认选择 | 选中的方向作为 plot 参数传入续写流程 |

---

## 7. 启动流程

```
_run_gui()
  │
  ├── QApplication(sys.argv)
  ├── QIcon(icon.svg) 设置窗口图标
  │
  └── DeepSeekChatGUI.__init__()
        │
        ├── 初始化 StreamSignals（连接 7 个信号）
        │
        ├── _login_and_init()
        │     │
        │     ├── LoginDialog（登录/注册）
        │     │     ├── 用户取消 → sys.exit(0)
        │     │     └── 用户确认 → 获取 username, enc_key
        │     │
        │     ├── 初始化 AuthManager
        │     ├── 创建用户目录 (conversations/, bookshelf/)
        │     ├── 初始化 NovelManager（bookshelf_root + 加密）
        │     ├── 初始化 ConversationManager（root_dir + 加密）
        │     │
        │     ├── 加载加密配置（API Key + Base URL）
        │     │     ├── 成功 → 使用
        │     │     └── 失败/空 → _get_api_key_with_retry()
        │     │           ├── QInputDialog 输入
        │     │           ├── _verify_api_key() 验证
        │     │           ├── 验证成功 → 加密保存
        │     │           └── 用户取消 → sys.exit(0)
        │     │
        │     ├── _init_client() — 默认 RolePlayStrategy
        │     ├── 加载全局用户提示词（加密存储 → 兜底明文）
        │     │
        │     ├── 旧数据迁移检测
        │     │     ├── 检测旧目录 bookshelf/ 和 conversations/
        │     │     ├── 如果旧数据未全部迁移 → 弹窗询问
        │     │     ├── 用户确认 → 逐本/逐对话加密复制到新用户目录
        │     │     └── 用户拒绝 → 跳过
        │     │
        │     ├── _init_ui()
        │     │     ├── QSplitter(水平) 左:left_panel 右:right_panel
        │     │     ├── left_panel = QScrollArea
        │     │     │     ├── 聊天模式 QGroupBox
        │     │     │     ├── 模型选择 QGroupBox
        │     │     │     ├── 生成参数 QGroupBox（预设+4个滑块/SpinBox）
        │     │     │     ├── 操作 QGroupBox（清除/修改API Key）
        │     │     │     ├── 对话历史 QGroupBox（保存/加载/删除/导出）
        │     │     │     ├── 状态 QGroupBox（状态/流式计数）
        │     │     │     └── QStackedWidget（三个模式面板）
        │     │     └── right_panel = QWidget
        │     │           ├── QWebEngineView（stretch=1）
        │     │           └── 底部输入区（输入框 + 发送 + 停止）
        │     │
        │     └── 初始显示 INITIAL_HTML
        │           _preset_combo 设为"狂野"
        │           应用深色主题
        │           刷新书架列表
        │           刷新对话历史列表
        │
        └── window.showMaximized()
              app.exec()
```

---

## 8. 关键状态变量一览

| 变量 | 类型 | 初始值 | 作用 |
|------|------|--------|------|
| `_client` | `DeepSeekChatClient \| None` | None | 核心聊天客户端 |
| `_auth` | `AuthManager \| None` | None | 认证管理器 |
| `_enc_key` | `bytes \| None` | None | 加密密钥 |
| `_username` | str | "" | 当前用户名 |
| `_novel_manager` | `NovelManager` | — | 小说管理器 |
| `_conversation_manager` | `ConversationManager` | — | 对话管理器 |
| `_current_conversation_id` | str \| None | None | 当前绑定的对话 ID |
| `_current_conversation_title` | str | "" | 当前对话标题 |
| `_streaming` | bool | False | 流式/生成进行中 |
| `_streaming_start_time` | float | 0.0 | 超时检测 |
| `_assistant_text_buffer` | list[str] | [] | 累积 token |
| `_chapter_finalized` | bool | True | 章节渲染完成锁 |
| `_loading_conversation` | bool | False | 加载对话中（阻止覆盖显示） |
| `_preset_applying` | bool | False | 预设应用中（阻止滑块切回"自定义"） |
| `_last_mode` | str | "" | 上次有效模式（用于 streaming 时回退） |
| `_cont_analysis_source` | str | (动态) | 续写分析阶段的源文本缓存 |
| `_cont_analysis_settings` | dict | (动态) | 续写分析的小说设定缓存 |
| `_cont_analysis_world_data` | dict | (动态) | 续写分析的世界书数据缓存 |

---

## 9. 信号/线程模型

```
StreamSignals(QObject)
├── token(str)           → _on_stream_token       → 流式 token 追加到显示
├── finished()           → _on_stream_finished     → 流式完成
├── error(str)           → _on_stream_error        → 流式错误
├── analysis_done(str,str,str)  → _show_analysis_dialog  → 分析完成
├── directions_ready(list,str,str,int) → _show_direction_selector → 方向建议完成
├── novel_imported(str)  → _on_cont_novel_imported → 导入完成刷新书架
└── refresh_chapter_info(str) → _refresh_chapter_info_display → 安全刷新

ChapterManagerDialog（独立信号）
├── _regenerate_done_signal(int,int,str)
├── _regenerate_error_signal(str)
├── _rebuild_done_signal()
└── _rebuild_error_signal(str)
```

**线程启动点（全部 daemon=True）：**

| 入口函数 | 后台目标 | 用途 |
|---------|---------|------|
| `_on_send` | `_run_stream` | 流式聊天 |
| `_on_generate_chapter` | `_run_chapter_generation` | 小说章节生成 |
| `_on_cont_generate_chapter` | `_run_continuation` | 续写章节生成 |
| `_on_start_continuation` | `_run_continuation` | 直接续写 |
| `_start_analysis_with_sections` | lambda | 世界观提取 + 设定生成 |
| `_run_batch_folder_import` | — | 批量导入章节 |
| `ChapterManagerDialog._on_regenerate` | `_do_regenerate` | 重新生成章节 |
| `ChapterManagerDialog._on_set_active` | `_do_rebuild_summary` | 重建摘要 |
| `_on_cont_suggest` | lambda | AI 方向建议 |

---

## 10. 加密持久化入口

| 文件 | 用途 | 读写方法 |
|------|------|---------|
| `config.enc` | API Key + Base URL | `_load/save_encrypted_config()` |
| `user_prefs.enc` | 全局用户提示词 | `_load/save_global_user_prompt()` |
| `bookshelf/[bookId]/` | 所有小说文件 | `NovelManager._write/read_encrypted_json/text()` |
| `conversations/[uuid].json.enc` | 对话历史 | `ConversationManager.save/load_conversation()` |
| `users/users.json` | 用户注册信息（salt + hash） | `AuthManager._load/save_users()` |

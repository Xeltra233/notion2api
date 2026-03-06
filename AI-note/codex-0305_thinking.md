# codex-0305_thinking 变更记录

## 目标
针对支持思考功能的模型（`gemini-pro` => `galette-medium-thinking`），将 Notion "思考过程"文本与最终回复文本分离，并端到端更新模型显示名称与图标。

## 一、NDJSON 结构诊断结果（采集于 2026-03-05）

### 思考阶段 patch 结构
来自一次完整输出的上游成功运行：
- `p: "/s/-"`
- `o: "a"`
- `v.type: "agent-inference"`
- 文本内容位于 `v.value[].content` 内

观测到的思考文本示例（保留结构，已缩略）：
- `{"default":{"questions":[...]}}`
- `**Re-Evaluating Search Strategies** ...`

### 最终回复 patch 结构
- `p: "/s/5/value/1/content"`（以及 `/s/5/value/3/content`）
- `o: "x"`
- `v: "...最终回复分片..."`

### 同一流中观测到的其他 v.type 值
- `agent-tool-result`
- `text`
- `title`
- `thinking`

### 上游稳定性说明
部分运行仅返回工具/错误类 patch（如 `v.type=error`），没有文本 patch，该现象属于上游间歇性行为。

## 二、后端改动

### `app/stream_parser.py`
- 新增第三种事件输出类型：
  - `{"type": "content", "text": "..."}` → 最终回复
  - `{"type": "search", "data": {...}}` → 联网搜索（已有）
  - `{"type": "thinking", "text": "..."}` → 思考过程
- 新增 `_looks_like_thinking_patch(patch)`，识别规则基于实测结构：
  - type/v.type 关键词命中：`agent-inference|thinking|reasoning|inference`
  - path 关键词命中：`thinking|reasoning|inference|internal`
  - 支持已观测的 `/s/-` + 思考类型组合
  - 保留 `/s/2/...` 路径作为兼容性兜底
- 新增每条有内容 patch 的调试日志（`event=notion_content_patch_debug`），记录完整 patch 元信息（path/type/o/v/patch）
- 修复事件输出顺序：若一个 patch 同时命中搜索和思考特征，优先 emit `thinking`，避免被搜索过滤逻辑吞掉

### `app/api/chat.py`
- 新增 `_build_thinking_chunk(text: str)`：
  - 输出格式：`data: {"type": "thinking_chunk", "text": "..."}`
- 流式路径：
  - 识别 `item_type == "thinking"` 并调用 `_build_thinking_chunk` 发送
  - 思考文本**不计入** `full_text_accumulator`
  - 思考文本**不写入**对话历史
- 非流式路径保持仅拼接 content 事件，thinking/search 均忽略

## 三、模型显示名称与图标更新

### `app/model_registry.py`
新增以下内容：
- `DISPLAY_NAMES`（显示名称映射）
  - `claude-opus → Opus 4.6`
  - `claude-sonnet → Sonnet 4.6`
  - `gemini-pro → Gemini 3.1 Pro`
  - `gpt-5 → GPT-5.2`
- `MODEL_ICONS`（模型图标映射）
  - `claude-opus → U+2733`（✳ 星形）
  - `claude-sonnet → U+2733`（✳ 星形）
  - `gemini-pro → U+2726`（✦ 菱形星）
  - `gpt-5 → U+2699`（⚙ 齿轮）
- 新增辅助函数：
  - `get_display_name(model_name)`
  - `get_model_icon(model_name)`

### `app/api/models.py`
每个模型对象新增字段：
- `display_name`
- `icon`

所有原有字段保持不变，向后兼容。

## 四、前端改动（`frontend/index.html`）

### 模型下拉框
- `loadModels()` 现在将 option 渲染为 `"{icon} {display_name}"` 格式
- option 的 `value` 仍为模型 id，API 请求不受影响
- 降级处理：若后端未返回 `display_name`/`icon` 字段，回退显示原始 id
- 新增 `STATE.modelDisplayNames` 映射表

### 思考面板 UI
在 AI 回复气泡内，插入于搜索来源区块与正文 markdown 区块之间：
- 独立可折叠的思考过程区块
- 默认折叠
- 标题格式：`"{model_display_name}'s thinking: ▸"`
- 展开后内容通过 `marked.parse()` 渲染
- 无思考内容时不显示（与无搜索时行为一致）
- 样式与现有 `.search-sources` 保持一致

### SSE 处理
在 `handleSend()` 中：
- 新增 `type === "thinking_chunk"` 事件识别
- 增量累积 `thinkingText`
- 流式过程中实时更新思考面板

## 五、验证记录

1. Python 语法检查通过：
   - `python -m py_compile app/stream_parser.py app/api/chat.py app/api/models.py app/model_registry.py ...`

2. 解析器行为最小自测通过（本地 FakeResponse）：
   - 合成 `agent-inference` patch + 最终文本 patch，可正确产出独立的 `thinking` 和 `content` 事件

3. 原始 NDJSON 结构采集完成：
   - 上方已记录思考阶段与最终回复阶段 patch 的具体结构特征

# codex-0305_search 记录

## 目标
修复 Notion NDJSON 流中“搜索元数据与正式回复混流”的问题：
- 搜索查询词/来源等信息不再作为 `delta.content` 输出
- 正式回答文本仍按 OpenAI SSE 流式输出
- 前端在 AI 气泡内渲染可折叠的搜索信息区域

## 一、实时抓流（Step 1）与结构结论

### 1. 采集方式
- 在 `app/stream_parser.py` 中添加 `logger.debug` 级别调试输出（不改 logger 默认级别）
- 通过真实 Notion 上游请求抓取 NDJSON（2026-03-05，本地时区 Asia/Shanghai）
- 重点匹配：`queries/category/sources/citations/questions` 与非文本 patch

### 2. 实际观测到的关键结构

#### A. 搜索意图/查询词（分片）
- patch 行示例特征：
  - `{"o":"x","p":"/s/3/value/0/content","v":"{\"web\": {\"queries"}`
  - `{"o":"p","p":"/s/3/value/0/content","v":"{\"web\":{\"queries\":[\"AI news headlines today March 5 2026\"]}}"}`
- 结论：查询词常出现在 `p` 路径下的 `.../value/.../content` 文本增量中，内容是 JSON 片段（可能跨多 chunk）

#### B. 搜索工具结果（完整结构）
- patch 行示例特征：
  - `o: "a", p: "/s/-"`
  - `v.type: "agent-tool-result"`
  - `v.toolName: "search"` / `v.toolType: "search"`
  - `v.input.web.queries: [...]`
  - `v.result.structuredContent.results: [{ title, url, ... }]`
- 结论：这是最稳定、信息最全的搜索元数据来源

#### C. 工具调用阶段
- patch 行示例特征：
  - `v.type: "agent-inference"`
  - `v.value` 中出现 `{"type":"tool_use","name":"search"}`
- 结论：用于识别当前步骤属于搜索相关流程

#### D. 其它工具（如 view）
- patch 行示例特征：
  - `v.type: "agent-tool-result"`
  - `v.toolName: "view"`
  - `v.input.urls: [...]`
- 结论：也会产生可展示来源 URL，可归入搜索元数据区域（来源列表）

## 二、后端改动（Step 2 + Step 3）

### 1) `app/stream_parser.py`

#### 输出格式改造
`parse_stream()` 从 `Generator[str]` 改为：
- `{"type": "content", "text": "..."}`
- `{"type": "search", "data": {...}}`

#### 搜索识别规则（基于实时抓流）
- patch 路径关键词：`search/web/query/source/citation/tool`
- patch 类型关键词：`agent-tool-result`（toolName=search/view）、`tool_use` 等非纯文本步骤
- 值对象关键词：`queries/questions/sources/citations/results/url/urls/toolName/toolType/internal`
- 额外处理：对 `.../content` 中的 JSON 分片（如 `{"web":{"queries":...}}`）进行缓冲拼接并解析，产出 `search` 事件，避免落入 `content`

#### 调试日志
新增 `logger.debug`：
- 命中关键字段的原始 NDJSON 行（截断）
- 非文本/疑似搜索 patch 的结构化 debug（含 `patch_type/path/search_*_hit`）

### 2) `app/notion_client.py`
- `stream_response()` 返回类型改为结构化事件流：`Generator[dict[str, Any], None, None]`
- 逻辑保持不变：继续调用 `parse_stream()` 并透传

### 3) `app/api/chat.py`

#### SSE 输出适配
- `content` 事件：保持 OpenAI chunk 结构，通过 `delta.content` 输出
- `search` 事件：输出自定义 SSE data：
  - `{"type":"search_metadata","searches": {...}}`

#### 对话持久化
- `full_text_accumulator` 只累计 `content` 文本
- `search` 元数据不写入对话历史

#### 非流式路径
- 仅拼接 `content` 事件；忽略 `search` 事件

### 4) `main.py`（兼容修复）
- 兼容 `stream_response()` 新事件格式，避免 CLI 路径报错
- `get_transcript()` 调用同步传入 model 参数（当前默认 `claude-opus`）

## 三、前端改动（Step 4）

文件：`frontend/index.html`

### 1) SSE 解析
- 新增对 `{"type":"search_metadata"}` 的识别
- 搜索数据进入 `searchState`（去重合并 queries/sources）
- 普通 OpenAI chunk 仍按 `choices[0].delta.content` 渲染正文

### 2) AI 气泡内新增折叠搜索区域
- 样式：`search-sources`（灰色小字，暗色主题兼容）
- 结构：
  - 折叠按钮（默认折叠）
  - 查询词展示
  - 来源链接列表（新窗口打开）
- 若无搜索数据，不显示该区域（与旧行为一致）

### 3) 不变项
- 未改动整体页面布局、主题系统、主色和字体体系

## 四、验证结果

### 1) 语法验证
- `python -m py_compile app/stream_parser.py app/notion_client.py app/api/chat.py main.py app/conversation.py` 通过

### 2) 实时流验证（真实上游）
- 观测到 `search` 事件可稳定提取：
  - 查询词：如 `AI news headlines today March 5 2026`
  - 来源：`result.structuredContent.results` 中 `title + url`
- 原先混入正文的 JSON 片段（如 `{"web":{"queries":...}}`）已改为进入 `search` 事件
- `chat.py` 中 `full_text_accumulator` 仅累积 `content`

## 五、已知说明
- 实时请求中，模型有时会输出搜索过程相关自然语言（例如 “let me get more specific results...”）；这属于上游模型正常文本输出，不是工具元数据。当前策略仅过滤结构化搜索元数据与 JSON 查询片段。

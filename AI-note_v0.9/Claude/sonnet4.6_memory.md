# Notion AI Wrapper 项目代码阅读笔记

> 由 Claude Sonnet 4.6 于 2026-03-06 整理

---

## 一、项目总览

这个项目是一个 **Notion AI 的 API 反向代理**，核心目标是：
把 Notion 内部使用的 Claude / Gemini / GPT 模型，包装成标准的 **OpenAI Chat Completions API** 格式，对外提供服务。

### 两种运行模式

| 模式 | 入口 | 说明 |
|------|------|------|
| 终端模式 | `main.py` | 交互式 CLI，直接在命令行聊天 |
| HTTP Server 模式 | `app/server.py` + `uvicorn` | FastAPI 服务，兼容 OpenAI API |

---

## 二、整体架构与文件职责

```
notion-ai/
├── main.py              # CLI 入口
├── app/
│   ├── server.py        # FastAPI 应用，中间件，路由挂载
│   ├── config.py        # 从 .env 读取 NOTION_ACCOUNTS、API_KEY 等配置
│   ├── schemas.py       # Pydantic 请求/响应 Schema（OpenAI 格式）
│   ├── account_pool.py  # 多账号轮询池，含失败冷却机制
│   ├── notion_client.py # 向 Notion 内部 API 发请求的 HTTP 客户端
│   ├── stream_parser.py # 解析 Notion 返回的 NDJSON 流
│   ├── conversation.py  # 【核心】基于 SQLite 的上下文记忆管理器
│   ├── model_registry.py# 模型名映射（claude-opus → avocado-froyo-medium）
│   ├── limiter.py       # IP 频率限制（slowapi，默认 20次/分钟）
│   ├── logger.py        # JSON 结构化日志
│   └── api/
│       ├── chat.py      # POST /v1/chat/completions 端点
│       └── models.py    # GET /v1/models 端点
└── frontend/
    └── index.html       # 前端单页面（Tailwind + Marked.js + Highlight.js）
```

---

## 三、一次请求的完整生命周期

```
前端 / 客户端
    │
    │  POST /v1/chat/completions  (OpenAI 格式)
    ▼
app/api/chat.py  create_chat_completion()
    │
    ├─ 1. _prepare_messages()      拆分 system/user/assistant 消息
    │                              system 消息合并注入到 user prompt 前
    │
    ├─ 2. conversation_id 处理
    │      - 无 id → 新建会话，把请求中的历史消息存入 DB
    │      - 有 id 但不存在 → 新建会话
    │      - 有 id 且存在 → 直接使用
    │
    ├─ 3. account_pool.get_client()   轮询取一个可用的 NotionOpusAPI 客户端
    │
    ├─ 4. conversation_manager.get_transcript()
    │      构建发给 Notion 的 transcript 列表（见下文"上下文记忆"）
    │
    ├─ 5. notion_client.stream_response(transcript)
    │      用 cloudscraper 发 POST 到 notion.so/api/v3/runInferenceTranscript
    │      返回流式 NDJSON 响应
    │
    ├─ 6. stream_parser.parse_stream()
    │      解析 NDJSON，输出三种结构化事件：
    │        {"type": "content",  "text": "..."}   正文
    │        {"type": "thinking", "text": "..."}   思考过程（agent-inference 类型段落）
    │        {"type": "search",   "data": {...}}   搜索元数据
    │
    ├─ 7. 流式模式：封装成 SSE (text/event-stream) 返回
    │     非流式模式：收集所有 content，组装 ChatCompletionResponse 返回
    │
    └─ 8. _persist_round()
           流结束后，把 user_prompt + assistant_reply 存入 SQLite
           add_message() 内部自动触发压缩逻辑（见"上下文记忆"）
```

---

## 四、上下文记忆机制（重点）

上下文记忆由 `app/conversation.py` 中的 `ConversationManager` 类实现，使用 **SQLite** 持久化存储，核心思路是 **滑动窗口 + 滚动摘要压缩**。

### 4.1 数据库结构

```sql
-- 会话表
CREATE TABLE conversations (
    id         TEXT PRIMARY KEY,   -- UUID
    title      TEXT,
    created_at INTEGER,
    summary    TEXT                -- 压缩后的历史摘要
);

-- 消息表
CREATE TABLE messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT,
    role            TEXT,      -- 'user' | 'assistant' | 'system'
    content         TEXT,
    created_at      INTEGER,
    FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
);
```

> DB 文件路径由环境变量 `DB_PATH` 控制，默认 `./data/conversations.db`。

---

### 4.2 核心参数：窗口大小

```python
class ConversationManager:
    WINDOW_SIZE = 6   # 数据库中最多保留 6 条消息（即 3 轮对话）
```

---

### 4.3 消息写入与自动压缩：`add_message()`

每次收到 AI 回复后，`_persist_round()` 调用两次 `add_message()`（一次写 user，一次写 assistant）。

`add_message()` 内部逻辑：

```
写入新消息
    ↓
检查消息总数是否 > WINDOW_SIZE (6)
    ↓ 是
while 消息数 > 6:
    _compress_oldest_turn()   // 压缩最旧的一轮
        ├─ 取出最旧的 user + assistant 两条消息
        ├─ summarize_turn(old_summary, user_msg, assistant_msg)
        │     把新内容追加进 summary 字符串
        │     ⚠ 当前是 TODO stub，只是简单文本拼接，未使用 LLM 真正总结
        ├─ UPDATE conversations SET summary = new_summary
        └─ DELETE 这两条消息
```

**关键细节**：压缩函数 `_compress_oldest_turn()` 只处理最旧的 user+assistant 配对。如果最旧两条不是严格的 user→assistant 顺序，则跳过压缩（避免出错）。

---

### 4.4 构建发给 Notion 的 Transcript：`get_transcript()`

这是把内存中的历史组装成 Notion API 所需格式的关键函数，构建顺序严格如下：

```
transcript 列表
  [0] config block      ← 模型选择、各种 feature flag（useWebSearch 等）
  [1] context block     ← 时区、用户名、userId、当前时间等
  [2] (可选) 摘要 user block   ← "Previous conversation summary:\n{summary}"
  [3] (可选) 摘要 assistant block ← "Understood, I have the context..."
  [4..n-1] 滑动窗口消息  ← 数据库中最新的 6 条（已做 user/assistant 交替校验）
  [n]  本轮新 user prompt
```

**摘要注入方式**：用一个伪造的 user/assistant 对话轮次把压缩历史"喂"给模型，这样模型可以感知到早期上下文，而无需把完整历史都放进 prompt。

**消息规范化 `_normalize_window_messages()`**：
- 强制 user/assistant 严格交替，从 user 开始
- 末尾必须以 assistant 结束（为新 user prompt 留位）
- 不符合规则的消息直接丢弃，保证 transcript 合法

---

### 4.5 整体上下文记忆流程图

```
对话轮次       数据库 messages         summary 字段
  ─────────────────────────────────────────────────
  第1轮   →  [u1, a1]                 (空)
  第2轮   →  [u1, a1, u2, a2]         (空)
  第3轮   →  [u1, a1, u2, a2, u3, a3] (空)
                                        ↑
  第4轮写入时消息数=7 > 6，触发压缩：
            [u2, a2, u3, a3, u4, a4]  "User asked: u1\nAssistant replied: a1"
                                        ↑
  第5轮写入时消息数=7 > 6，再次压缩：
            [u3, a3, u4, a4, u5, a5]  "...u1a1...\n\nUser asked: u2\nAssistant replied: a2"
  ─────────────────────────────────────────────────

  每次 get_transcript() 时，summary + 最近6条消息 一起发给 Notion
```

---

## 五、多账号池机制

`app/account_pool.py` 中的 `AccountPool`：

- 从 `NOTION_ACCOUNTS`（JSON 数组）初始化多个 `NotionOpusAPI` 实例
- **轮询（Round-Robin）**：`get_client()` 每次返回下一个可用账号
- **失败冷却**：`mark_failed(client, cooldown_seconds=60)` 把账号标记为 60 秒内不可用
- 如果所有账号都在冷却期，抛出 RuntimeError（HTTP 503）
- 重试次数 = `min(3, len(pool.clients))`

---

## 六、Notion 流解析（stream_parser.py）

Notion 返回的是 NDJSON 格式的 patch 流，每行是一个 patch 操作。解析器使用 **段落注册表（Segment Registry）** 机制：

- `o:"a" + path="/s/-"` → 创建新的顶层段落，根据 `v.type` 分类：
  - `agent-inference / thinking / reasoning` → `SEG_THINKING`（思考过程）
  - `agent-tool-result / tool / search` → `SEG_TOOL`（工具调用，也归到 thinking 输出）
  - `text / title` → `SEG_CONTENT`（正文）
- `o:"x" + path="/s/N/..."` → 追加文本到已知段落，查表获取类型
- 搜索元数据通过 `_looks_like_search_patch()` 单独提取，输出 `{"type": "search", ...}`
- 清理 Notion 内部 `<lang primary="zh-CN">...</lang>` 标签

---

## 七、模型映射

```python
# model_registry.py
"claude-opus"   → "avocado-froyo-medium"   # Claude Opus 4.6
"claude-sonnet" → "almond-croissant-low"   # Claude Sonnet 4.6
"gemini-pro"    → "galette-medium-thinking" # Gemini 3.1 Pro
"gpt-5"         → "oatmeal-cookie"          # GPT-5.2
```

Notion 内部用食物名称作为模型 ID，这里做了友好名称到内部 ID 的双向映射。

---

## 八、API 端点一览

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/v1/chat/completions` | 对话，支持 stream 和非 stream，兼容 OpenAI |
| GET  | `/v1/models` | 列出可用模型 |
| GET  | `/health` | 健康检查，返回账号池状态和 uptime |
| GET  | `/` | 前端静态页面 |

**鉴权**：若设置了 `API_KEY` 环境变量，所有 `/v1/*` 路径需要 `Authorization: Bearer <key>` 头。

**限速**：`/v1/chat/completions` 路由额外限制 **10次/分钟/IP**（全局默认 20次/分钟）。

---

## 九、前端

`frontend/index.html` 是一个纯静态单页面应用：
- **Tailwind CSS**：样式（仿 Claude 官网的奶油/肉桂配色）
- **Marked.js**：渲染 Markdown 响应
- **Highlight.js**：代码块语法高亮
- 通过 `fetch` 调用本地的 `/v1/chat/completions` 接口
- 支持流式输出（SSE）
- 支持浅色/深色模式切换

---

## 十、已知 TODO / 潜在问题

1. **`summarize_turn()` 只是 stub**：当前压缩历史只是简单文字拼接，随着对话增长，summary 会越来越长，没有真正的 LLM 摘要能力。理想做法是调一个轻量模型把历史真正总结成几句话。

2. **`summarize_turn()` 是同步函数**，但 `add_message()` 在 FastAPI 异步上下文中调用。如果将来引入真正的 LLM 摘要，需要改为 async。

3. **`conversation_id` 由客户端维护**：每次请求都需要客户端把上次返回的 `conversation_id` 带上，否则会开新会话。这要求客户端支持扩展字段。

4. **SQLite 并发**：目前用 WAL 模式 + busy_timeout=5s，单机使用没问题，高并发下可能成为瓶颈。

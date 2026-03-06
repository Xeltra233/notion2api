# codex-0304 变更与分析记录

## 1. 项目结构与内部关联（已完整梳理）

本次已梳理项目内源码与配置文件（`app/`、`frontend/`、根目录运行/部署文件、隐藏配置文件）。  
`.venv/` 属第三方依赖安装目录，文件数量极大且不属于项目业务源码，已做清单级确认但未逐文件审阅。

关键调用链如下：

1. 前端页面：`frontend/index.html`
2. 前端调用：`POST /v1/chat/completions`（SSE）
3. 路由入口：`app/api/chat.py`
4. 对话持久化：`app/conversation.py`（SQLite）
5. 账号分配：`app/account_pool.py`
6. 上游请求：`app/notion_client.py` -> Notion `runInferenceTranscript`
7. 流解析：`app/stream_parser.py`
8. 服务装配：`app/server.py`（中间件、路由、健康检查、静态挂载）

---

## 2. 已知 Bug 根因定位（重点问题）

### 根因 A（直接导致 500）
`app/api/chat.py` 中使用了不存在的 `manager.conversations`：

- 代码原逻辑：`if not conversation_id or conversation_id not in manager.conversations:`
- `ConversationManager` 并没有 `conversations` 属性
- 请求会触发 `AttributeError`，直接变成 500

### 根因 B（Notion 有响应但内容丢失/空白）
前端 SSE 解析没有跨分片缓冲：

- 原逻辑按单次 `reader.read()` 的 chunk 直接 `split('\n')` + `JSON.parse`
- 当一条 `data: {...}` 被网络切成两段时，解析失败并被忽略
- 最终表现为前端空白、内容断裂或几乎无输出

### 根因 C（后端流内容丢失）
`app/stream_parser.py` 解析 `patch.v.value` 列表时覆盖 `content`，只保留最后一个文本片段，导致实际上游文本可能部分丢失。

### 根因 D（错误传播不清晰）
`notion_client.py` 过去把上游错误当普通文本 `yield`，`chat.py` 再用字符串包含判断，既脆弱又容易误判（且存在优先级/空值风险）。

### 根因 E（健康检查潜在 500）
`app/server.py` 里 `/health` 访问了不存在的 `client.status` 字段，同样有潜在 500。

---

## 3. 实际改动清单

## 3.1 `app/api/chat.py`
- 重构消息预处理：
  - 要求最后一条消息必须是 `user`，否则返回 `400`
  - 空 user prompt 返回 `400`
- 修复 conversation_id 逻辑：
  - 使用 `ConversationManager.conversation_exists()` 检查
  - 不再访问不存在属性
- 重构上游重试与错误处理：
  - 识别 `NotionUpstreamError`
  - 仅在可重试场景标记账号冷却并重试
- 增强流式输出：
  - 统一 SSE chunk 构造
  - 增加 `Cache-Control / Connection / X-Accel-Buffering` 头，减少代理缓冲导致的流式延迟
  - 流中断时记录日志并返回提示文本 chunk，避免前端静默空白
- 非流式路径加入空内容保护与异常兜底

## 3.2 `app/notion_client.py`
- 新增 `NotionUpstreamError`（带 `status_code/retriable/response_excerpt`）
- 不再把错误当文本 `yield`，改为抛出结构化异常
- 增加 transcript 入参校验（非空 list）
- 请求超时改为连接/读取分离：`timeout=(15, 120)`
- 非 200 返回时保留摘要并区分是否可重试
- 如果上游流结束但没有任何可用文本，抛出空响应异常
- 增加结构化日志（trace_id/thread_id/account）

## 3.3 `app/stream_parser.py`
- 修复文本片段覆盖问题（改为拼接多个 text 片段）
- 改进类型判断与空值保护
- 使用 `iter_lines(decode_unicode=True)` 简化解析路径

## 3.4 `app/conversation.py`
- 增强 SQLite 连接健壮性：
  - `timeout=10`
  - `PRAGMA busy_timeout = 5000`
  - `PRAGMA journal_mode = WAL`
  - `PRAGMA foreign_keys = ON`
- 新增 `conversation_exists(conversation_id)` 接口（供 API 正确校验）
- `add_message()` 增加 role 合法性校验
- 创建会话时增加结构化日志

## 3.5 `app/account_pool.py`
- 移除 `print`，改为结构化日志
- 新增 `get_status_summary()`（总数/可用/冷却中）
- 所有账号不可用时，错误信息包含预计重试秒数

## 3.6 `app/server.py`
- `/health` 改为读取 `pool.get_status_summary()`，不再访问不存在字段
- 新增通用异常处理器，统一返回 JSON 错误并落日志

## 3.7 `app/schemas.py`
- `ChatMessage.role` 改为 `Literal["user","assistant","system"]`
- 修复可变默认值：`usage` 改为 `default_factory`

## 3.8 `app/config.py`
- 加强 `NOTION_ACCOUNTS` 校验：
  - 数组项必须为对象
  - 必填字段 `token_v2/space_id/user_id` 不能为空

## 3.9 `frontend/index.html`（仅 JS 行为，不改 UI 样式）
- 修复 SSE 解析：
  - 增加 `sseBuffer` 跨 chunk 缓冲
  - 按 `\n\n` 事件边界解析
  - 保留末尾残余数据并做最终解析
- 未改动任何视觉样式、布局、配色、组件结构

---

## 4. 验证记录

已执行：

1. 语法编译检查  
   `.\.venv\Scripts\python.exe -m compileall app main.py`  
   结果：通过

2. 流解析最小自测（本地 FakeResponse）  
   验证 `stream_parser` 对多文本片段拼接结果正确  
   结果：通过（输出 `Hello world`）

3. 对话管理最小自测  
   验证 `new_conversation / conversation_exists / add_message`  
   结果：通过

受限项：

- 当前虚拟环境缺少 `fastapi`（运行集成测试脚本时报 `ModuleNotFoundError: fastapi`），因此未完成端到端 HTTP 回归。

---

## 5. 结论

- “前端空白或 500”核心问题已修复，并补上多处同类故障点。
- 后端健壮性已显著提升：错误语义、日志可观测性、边界校验、连接稳定性、健康检查可靠性均已增强。
- 前端 UI 设计与样式未改动，仅修复流式解析逻辑。

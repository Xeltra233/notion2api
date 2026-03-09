# codex-0306 memory 重构记录

## 目标
- 将旧的 `summary` 拼接逻辑重构为四层记忆架构：
  - 滑动窗口（最近 5 轮 / 10 条）
  - LLM 压缩池（`compressed_summaries`）
  - 完整原文归档（`full_archive`）
  - 主动召回（召回意图检测 + 召回注入）

## 修改文件
- `app/conversation.py`
- `app/summarizer.py`（新增）
- `app/api/chat.py`
- `frontend/index.html`
- `app/config.py`
- `requirements.txt`

## 具体改动

### 1) `app/conversation.py`
- 数据库迁移与新表：
  - 新增 `compressed_summaries` 表（含 `compress_status`）。
  - 新增 `full_archive` 表。
  - 为 `conversations` 增加：
    - `next_round_index INTEGER DEFAULT 0`
    - `compress_failed_at INTEGER`
  - 使用 `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`（并带兼容回退）。
  - 保留 `conversations.summary` 兼容字段，但不再写入。
- 参数调整：
  - `WINDOW_SIZE = 10`
  - `SUMMARY_INJECT_LIMIT = 15`
- 持久化重构：
  - 新增 `persist_round()`，一次性写入 user+assistant 并推进 `next_round_index`。
  - `add_message()` 保持兼容（CLI 仍可用），但不再触发压缩。
  - 所有消息都会写入 `full_archive`（`INSERT OR IGNORE`，避免重复）。
- Transcript 组装重构：
  - 顺序：`config -> context -> failed marker(可选) -> 历史摘要注入(可选) -> 最近10条消息 -> 召回注入(可选) -> 本轮user`。
  - 压缩摘要注入格式：
    - `以下是本次对话的历史摘要（从早到晚）：\n1. ...`
    - 并注入固定 assistant：`我已了解之前的对话背景。`
  - 若存在 `compress_status='failed'`，插入标记消息 `【系统状态标记】MEMORY_STATUS=degraded`。
- 主动召回：
  - 在 `compressed_summaries` 上做 LIKE 检索（summary/user/assistant）。
  - 取最多 5 条对应 `round_index`，从 `full_archive` 取完整原文并格式化注入。
  - 注入固定 assistant：`我已查阅相关历史记录，将综合作答。`
- 新增后台压缩函数：
  - `async compress_round_if_needed(manager, conversation_id)`
  - 当 `messages > WINDOW_SIZE` 时循环压缩最老轮次：
    - 删除最老 user+assistant
    - 写入 `compressed_summaries` pending
    - 归档到 `full_archive`
    - 调用 `summarizer.summarize_turn()`
    - 成功置 `done`，失败置 `failed` 并更新 `conversations.compress_failed_at`
  - 全流程 `try/except`，只打日志不向上抛异常。

### 2) `app/summarizer.py`（新增）
- 新增 `SummarizerUnavailableError`。
- 新增 `summarize_turn(old_summaries, user_msg, assistant_msg)`：
  - 接口：`https://api.siliconflow.cn/v1/chat/completions`
  - 模型降级链：
    - `Qwen/Qwen3-8B`
    - `THUDM/glm-4-9b-chat`
  - `httpx.AsyncClient`，超时：
    - connect 5s
    - read 20s
  - Prompt 按需求实现（system + user）。
  - `SILICONFLOW_API_KEY` 为空时，直接抛 `SummarizerUnavailableError`。

### 3) `app/api/chat.py`
- `create_chat_completion` 新增参数：
  - `background_tasks: BackgroundTasks`
  - `response: Response`
- `_persist_round()` 改为：
  - `manager.persist_round(...)`
  - `background_tasks.add_task(compress_round_if_needed, manager=..., conversation_id=...)`
- 召回意图检测：
  - 新增关键词列表（中英文）。
  - 对最后一条用户消息检测并提取召回查询词。
  - 调用 `manager.get_transcript_payload(..., recall_query=...)` 构建 transcript。
- 响应头透传：
  - 若 `memory_degraded`，在流式和非流式响应都设置：
    - `X-Memory-Status: degraded`

### 4) `frontend/index.html`
- 新增顶部非阻塞 banner（可手动关闭）：
  - 文案：`⚠️ 当前上下文记忆功能受限，部分历史可能无法被感知，如问题持续请联系管理员。`
  - 颜色风格：
    - Light：`#f2f0eb`
    - Dark：`#383838`
    - Accent：`#da7756`
- 新增会话内一次性提示控制：
  - `memoryDegradedNotified`
  - `notifyMemoryDegradedOnce()`
- 在每次 `fetch` 返回后读取响应头：
  - `X-Memory-Status === degraded` 时触发 banner（只显示一次）。

### 5) 配置与依赖
- `app/config.py` 新增：
  - `SILICONFLOW_API_KEY = os.getenv("SILICONFLOW_API_KEY", "")`
- `requirements.txt` 新增：
  - `httpx`

## 执行过的操作
- 读取并分析：
  - `app/conversation.py`
  - `app/api/chat.py`
  - `app/config.py`
  - `frontend/index.html`
  - `app/schemas.py`
  - `requirements.txt`
- 语法检查：
  - `python -m compileall app`
  - `python -m py_compile app\\conversation.py app\\summarizer.py app\\api\\chat.py app\\config.py`
- 变更核对：
  - `git status --short`
  - `git diff -- app\\conversation.py app\\summarizer.py app\\api\\chat.py app\\config.py frontend\\index.html requirements.txt`

## API Key 填写位置
- 在项目环境变量中新增：
  - `SILICONFLOW_API_KEY=你的硅基流动Key`
- 代码读取位置：
  - `app/config.py` 的 `SILICONFLOW_API_KEY`

---

## 续写（Bug Fix）- 2026-03-06

### 修复目标
- Issue 1：`get_transcript()` / `get_transcript_payload()` 未正确感知摘要注入状态。
- Issue 2：压缩时需确保传入“当前轮之前的全部 done 摘要”（累计上下文）。

### 本次仅修改文件
- `app/conversation.py`

### 具体修复点
1. 摘要注入链路可观测性增强（Issue 1）
- 在 `_fetch_recent_done_summaries()` 增加日志：
  - `memory_summary_query_done`：记录 `conversation_id`、`compress_status=done`、`row_count`。
  - `memory_summary_payload_ready`：记录最终可注入 `summary_count`。
- 在 `get_transcript_payload()` 的摘要注入段增加注释与日志：
  - 明确“摘要注入位置必须在 context 之后、recent messages 之前”。
  - `memory_summary_injected`：记录实际注入次数。
- 注入格式保持为：
  - user：`以下是本次对话的历史摘要（从早到晚）：\n1. ...\n2. ...`
  - assistant：`我已了解之前的对话背景。`

2. 累计摘要传参修正（Issue 2）
- 在 `compress_round_if_needed()` 查询 `old_summaries` 时新增约束：
  - `AND round_index < ?`（当前被压缩轮次之前）。
- 继续按 `ORDER BY round_index ASC` 提供给 `summarize_turn()`。
- 增加日志：
  - `memory_cumulative_summaries_ready`：记录 `current_round_index` 与 `old_summary_count`。

### 本次操作记录
- 定位代码片段：`Select-String -Path app\\conversation.py ...`
- 局部补丁修改：`apply_patch`（仅改相关函数段）
- 语法校验：`python -m py_compile app\\conversation.py`

---

## 续写（会话ID链路修复）- 2026-03-06

### 现象复盘
- 日志持续出现：`row_count=0, summary_count=0`。
- 数据库实际检查发现 `compressed_summaries` 存在大量 `compress_status='done'` 记录。

### 根因
- 前端请求未携带 `conversation_id`。
- 后端每次都走 `manager.new_conversation()`，导致每次请求对应新的 `conversation_id`。
- 因此 transcript 构建时按“当前新会话ID”查询压缩池，自然查不到历史 `done` 摘要。

### 修复
1. 后端返回会话ID
- 文件：`app/api/chat.py`
- 在流式响应头新增：`X-Conversation-Id: {conversation_id}`。
- 在非流式响应头新增：`X-Conversation-Id: {conversation_id}`。

2. 前端持久化会话ID并回传
- 文件：`frontend/index.html`
- chat 对象新增字段：`conversationId`。
- 请求体新增：`conversation_id: chat.conversationId || null`。
- 每次响应读取 `X-Conversation-Id`，写回 `chat.conversationId` 并 `saveChats()`。

### 本地检测
- 数据库探测：`compressed_summaries` 已有 `done` 数据，说明压缩逻辑在运行。
- 固定同一 `conversation_id` 的脚本复现实验中，日志出现：
  - `memory_summary_query_done`（`row_count > 0`）
  - `memory_summary_injected`（`summary_count > 0`）
- 结论：摘要注入逻辑本身可用，关键是会话ID必须连续。

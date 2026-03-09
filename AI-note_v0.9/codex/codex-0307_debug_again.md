# codex-0307_debug_again

## 任务范围与约束

本次只做分析和计划，不修改项目业务代码。

已按要求阅读：

1. `CODEX.md`
2. `AI-note/codex/codex-0307_opusdebug.md`
3. 项目主链路（`app/`、`frontend/`、数据库现状、测试脚本）
4. 参考项目 `ref/notion-2api`（只参考，不修改）

---

## 结论先行（针对你给出的现象）

你的两个核心现象都能在当前代码中被解释，而且已经用真实上游请求复测确认：

1. **“正文跑到 thinking 区块，外部只剩超短压缩语句”**  
   不是单点问题，而是两个问题叠加：
   - patch 分段分类把大量文本判成了 `thinking`
   - `record-map` 最终正文提取逻辑拿到了 `title`（例如“打招呼”“Meaning of AIMC”）而不是完整回答

2. **“离开再回来，thinking/联网区块消失，只剩正文（常是短句）”**  
   这是当前前端存储模型导致的确定性结果：
   - 本地仅持久化 `messages[{role, content}]`
   - `thinking` 和 `search` 只在流式渲染期间存在，不会写入本地聊天记录
   - 回放时只按 `role/content` 重画，thinking/search 天然丢失

---

## 本地真实复测（2026-03-07）

我直接用当前仓库代码跑了真实 Notion 上游流（同一账号池），统计 `parse_stream()` 事件：

### 事件计数（prompt: “介绍一下AIMC”）

- `claude-sonnet4.6`：`search=1, thinking=24, content=31, final_content=1`
- `claude-opus4.6`：`search=3, thinking=102, content=0, final_content=1`
- `gpt-5.2`：`search=2, thinking=230, content=0, final_content=1`
- `gemini-3.1pro`：`search=2, thinking=25, content=0, final_content=1`

关键点：

- Opus/GPT/Gemini 在这次复测里 **正文 content 全为 0**，只剩 `thinking + final_content(短句)`，和你的截图表现一致。
- Sonnet 还能出 `content`，所以体感更稳定。

### `record-map` 原始结构抓样（Opus）

同一次请求中，`record-map.thread_message` 同时包含：

- `step_type=title`，文本：`Meaning of AIMC`（短句）
- `step_type=agent-inference`，文本长度 572（完整中文回答）

而当前 `app/stream_parser.py` 的 `_extract_final_content_from_record_map()` 是“遍历到第一个可用文本就 return”，并且把 `title` 也纳入候选。  
因此很容易先返回 `title`，这就是你说的“压缩文本”。

---

## 代码级根因拆解

## A. “压缩短句被当正文”是如何发生的

位置：`app/stream_parser.py`

- `_extract_final_content_from_record_map()` 遍历 `thread_message.values()`，命中第一条非空就 `return`。
- 当前候选类型包含 `title`。
- 当 `title` 排在完整 `agent-inference` 前面时，`final_content` 就变成了短标题。

直接后果：

- `app/api/chat.py` 在流末优先用 `authoritative_final_content` 做持久化。
- 所以数据库会写入“打招呼 / Greeting / Meaning of AIMC”这类短标题。

我已在现库验证到大量此类记录（`messages.role='assistant'`）：

- 例如：`打招呼`、`Greeting`、`介绍Claude`、`买耳机计划`、`等角五边形边长平方和`。

---

## B. “正文在 thinking 区块内”是如何发生的

位置：`app/stream_parser.py`

- 段落分类是按 patch 的 segment/value type：
  - `agent-inference` -> `thinking`
  - `text/title` -> `content`
- 当模型（尤其 Opus/GPT）把可见正文仍放在 `agent-inference` 流中，且没有独立 `text` 子块时，这些文本会全部走 `thinking`。

位置：`app/api/chat.py`

- 流式输出只把 `content` 进主正文；`thinking` 进 `reasoning_content`。
- 如果 `content` 没有（或只有很短），前端主正文就会很短，而大段文字留在 thinking 面板。

---

## C. 为什么“离开再回来 thinking/联网块消失”

位置：`frontend/index.html`

- `chat.messages` 只存 `{ role, content }`。
- thinking/search 是运行态变量（`thinkingText`、`searchState`），没有持久化入 `chat.messages`。
- `selectChat()` 只调用 `appendMessageToDOM(msg.role, msg.content, true)` 回放正文，不恢复 thinking/search。

所以这是当前前端数据模型的“设计性缺失”，不是偶发现象。

---

## D. “记忆和压缩被污染”的链路

位置：`app/api/chat.py` + `app/conversation.py` + SQLite

- 持久化优先 `final_content`（现在经常是 title 短句）
- 下游记忆窗口和压缩摘要基于这些已污染轮次
- 导致摘要出现“本轮无具体内容”等低价值结果（库中已存在）

---

## 与 `ref/notion-2api` 的关键对照（只参考结论）

`ref` 的做法里有三点对当前问题最关键：

1. 三通道并行（`patch + record-map + markdown-chat`）的思想是对的
2. 最终正文与增量正文分离，最终态优先
3. `record-map` 提取里重点看 `markdown-chat / agent-inference`，而不是把 `title` 当最终正文主来源

另外，`ref` 的 payload builder 更集中，便于模型协议分流；当前项目逻辑分散在 `conversation.py + notion_client.py`，后续排障成本更高。

---

## 针对你当前情况的修复计划（仅计划，不改代码）

## 阶段 1（P0）：修正 `record-map` 最终正文选择策略

目标：彻底消除“压缩短句当正文”。

计划：

1. `_extract_final_content_from_record_map()` 改为“收集候选后排序”，不是命中即返回
2. 候选优先级建议：
   - `markdown-chat`（最高）
   - `agent-inference`（次高）
   - `text`
   - `title`（最低，仅兜底）
3. 同级候选按“更晚 + 更长 + 可读性”打分，优先完整回答
4. 给 `final_content` 事件附带元信息（来源 type、长度），便于日志验证

预期：`final_content` 不再是“打招呼/Meaning of ...”。

---

## 阶段 2（P0）：修正流末“最终正文与已流正文不一致”处理

目标：避免前端停留在短正文。

问题点：

- 当前 `_compute_missing_suffix()` 仅处理“final 以 streamed 为前缀”的情况。
- 若两者不满足前缀关系（常见于“short title vs full answer”），不会补齐。

计划：

1. 新增“非前缀不一致”的显式覆盖分支（至少对 `X-Client-Type: Web` 生效）
2. Web 客户端收到覆盖事件后，用最终正文替换 `fullAiReply`（不是追加）
3. 持久化前再次做质量门禁：若 final 明显劣于 streamed（过短/疑似 title），回退到更合理版本

---

## 阶段 3（P1）：优化 patch 级 thinking/content 分流

目标：减少“正文进 thinking”的视觉问题。

计划：

1. 对 `agent-inference` 增加短缓冲，不立刻落 thinking
2. 若后续出现明确正文块，则缓冲内容维持 thinking
3. 若最终只有 `agent-inference` 且 `record-map` 给出完整正文，则以最终正文为主，不让短正文/空正文占主区
4. `title` patch 不再作为主正文流输出来源（避免 UI 短句抖动）

---

## 阶段 4（P1）：修复“离开回来丢 thinking/search”

目标：消除你定义的“通病”。

计划：

1. 扩展前端本地消息结构：
   - `assistant` 消息保存 `content + thinking + search + modelDisplayName`
2. 流结束后将 thinking/search 快照写入 `chat.messages`
3. `selectChat()` 回放时恢复 search panel 与 thinking panel
4. 保留 `sanitizeChats()`，但不要清掉有 thinking/search 的有效 assistant 记录

---

## 阶段 5（P2）：历史脏数据治理

目标：让旧会话不再持续污染记忆。

计划：

1. SQL 脚本识别并标注疑似 title 污染 assistant（极短且命中标题模式）
2. 对受污染对话给出“重建建议”或自动回填策略
3. 摘要表按需重算（至少跳过“用户和AI均未提供具体内容”的机械摘要）

---

## 风险与原则

1. 不做模型名硬编码特判（避免误伤 Sonnet）
2. 不做“thinking 全量兜底正文”的粗暴回退
3. 优先结构证据（record-map/markdown-chat）而非关键词猜测
4. 保持 `ref/` 只参考，不改动

---

## 本次分析后的最终判断

你现在遇到的症状不是随机 bug，而是当前分流链路中的可复现组合问题：

- **最终正文选择错误（title 抢占）**
- **patch 分类下正文落 thinking**
- **前端未持久化 thinking/search**

所以修复顺序必须是：

1. 先修 `final_content` 选择与流末覆盖（P0）
2. 再做 patch 观感优化（P1）
3. 最后做前端回放与历史清理（P1/P2）

这条路线和你给出的现象、以及当前代码与实测证据是完全一致的。


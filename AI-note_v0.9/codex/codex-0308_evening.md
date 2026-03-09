# codex-0308_evening

## 时间

- 更新日期：2026-03-08
- 任务类型：修复方案设计（仅计划，不改代码）
- 目标问题：滑动窗口记忆坍塌（窗口里常见仅 user、缺 assistant）

---

## 一、结论（先说可执行方案）

基于当前代码与实库数据，建议采用 **两阶段修复**：

1. **Phase 1（P0 止血，最小改动）**
   - 先解决“assistant 在历史重建链路被静默丢弃”的问题，确保滑动窗口里稳定恢复 `user+assistant` 成对历史。
   - 优先改动前端过滤与后端归一化，不先做复杂迁移。

2. **Phase 2（P1 结构化增强）**
   - 引入 `thinking` 的持久化与透传，减少未来模型（GPT-5.4/Opus）在“正文弱、thinking 强”场景下的再次退化风险。
   - 补齐迁移、兼容和回归测试。

理由：当前最致命故障是“窗口缺 assistant 导致失忆”，应先保配对完整；`thinking` 入库是增强项，但不应阻塞 P0 修复上线。

---

## 二、问题链路复盘（结合 Gemini 审计 + 本地代码现状）

## 1) 前端过滤是首个触发点（高优先级）

- 位置：`frontend/index.html`（`handleSend` 中 requestMessages filter）
- 现状：assistant 必须 `content.trim()` 非空才会被发给后端。
- 后果：一旦某轮 assistant 只有 thinking/search、正文空，历史回传时该 assistant 被前端永久剔除。

## 2) 后端“历史重建路径”会放大前端丢失（高优先级）

- 位置：`app/api/chat.py`
- 现状：当 `conversation_id` 缺失/失效，会新建会话并按请求 `messages` 批量重建历史。
- 后果：如果请求侧已经缺 assistant，重建后的 DB 天然只剩 user，滑窗必然失真。

## 3) 归一化策略会进一步清空不完整轮次（高优先级）

- 位置：`app/conversation.py::_normalize_window_messages`
- 现状：空 content 消息会被过滤，再做相邻 `user->assistant` 配对。
- 后果：assistant 被过滤后，整对轮次失效，窗口里 user 比例持续升高。

## 4) `thinking` 未持久化是长期风险点（中优先级）

- 位置：`messages` 表当前仅 `role/content/created_at`
- 风险：在“正文很少、thinking 承载主体信息”的模型输出形态下，系统缺乏可恢复信号。
- 判断：这不是本次坍塌的唯一根因，但会显著提高再次复发概率。

## 5) assistant transcript 表示兼容性需纳入验证（中优先级）

- 现状：非 Gemini 历史 assistant 用 `type='assistant'`；参考实现常用 `agent-inference`。
- 风险：若上游对 `assistant` 历史兼容弱，模型可能“看见 user、弱感知 assistant”。
- 处理策略：先在 P0 验证，不先盲改协议形态。

---

## 三、最终计划（按优先级执行）

## Phase 1：P0 止血（建议先做）

### 1. 前端历史过滤改为“保角色，不保空正文”

- 改动目标：
  - 请求构造时，assistant 不再以 `content.trim()` 作为硬过滤条件。
  - 只要 `msg.role==='assistant'` 即保留进入请求历史。
- 说明：
  - 若担心纯空噪声，可在后端做二次治理，不在前端直接丢角色。

### 2. 后端归一化支持“assistant 无正文但可占位”

- 改动目标：
  - `_normalize_window_messages` 不因 assistant `content=''` 直接丢弃。
  - 维持 `user->assistant` 配对完整性。
- 可选策略（二选一）：
  - A：允许空 assistant 通过配对（最小入侵）。
  - B：空 assistant 自动填占位符（如 `[assistant_no_visible_content]`）以保证上游 transcript 稳定。
- 推荐：先 A，必要时再 B（避免占位符污染模型语义）。

### 3. 历史重建链路加保护

- 改动目标：
  - 在 `restore_history` 路径加日志与计数：`user_count/assistant_count`。
  - 当出现严重失衡时打 warning，便于快速定位是否是前端回传污染。

### 4. P0 验收标准

- 同一会话连续 8+ 轮后：
  - `messages` 中 recent window 应保持近似 1:1 的 user/assistant。
  - `get_transcript_payload` 产物中历史 assistant 条数不为 0。
  - GPT-5.4 / Opus 复测不再出现“仅看见提问、不看见回答”的失忆表现。

---

## Phase 2：P1 结构化增强（P0 稳定后执行）

### 1. 为 `messages` 增加 `thinking` 字段（向后兼容迁移）

- 迁移目标：
  - `ALTER TABLE messages ADD COLUMN thinking TEXT`（兼容旧库）
  - 读路径默认 `thinking=''`，不破坏历史数据。

### 2. 持久化接口扩展

- 改动目标：
  - `persist_round` / `add_message` 支持传入 `thinking`。
  - `chat.py` 在流结束持久化时，把最终 thinking 一并写入。

### 3. 窗口归一化升级为“content/thinking 双通道判定”

- 改动目标：
  - assistant 有 `content` 或有 `thinking` 均视为有效回复。
  - transcript 层保守策略：
    - 默认仍以 `content` 为主；
    - 如 `content` 为空且 `thinking` 非空，可用摘要占位（非全量思维链），避免上下文断链。

### 4. P1 验收标准

- 仅 thinking 场景也能在窗口内保留 assistant 轮次；
- 历史重建后不再出现成片 user-only；
- 现有 Sonnet/Gemini 行为无回归。

---

## 四、测试与回归计划

## 1) 单元测试新增/更新

- `test_window_normalize.py`
  - 新增：assistant `content=''` 但有 thinking 时，配对不丢。
  - 新增：连续 user + 空 assistant 混合场景的稳定性。

- `chat` 路由测试
  - 模拟 `conversation_id` 丢失触发 restore_history；
  - 校验 restore 后 `messages` 角色分布与 transcript 输出。

## 2) 集成回归

- 模型维度：`gpt-5.4`、`gpt-5.2`、`claude-opus4.6`、`claude-sonnet4.6`、`gemini-3.1pro`
- 场景维度：
  - 正常正文输出
  - thinking 主导、正文稀薄
  - 断线重发/刷新后继续聊
  - 超窗压缩后继续多轮

---

## 五、发布与回滚策略

## 发布顺序

1. 先发 Phase 1（前端过滤 + 归一化 + 监控日志）
2. 观察 24h：重点看 `user_count - assistant_count` 分布是否收敛
3. 再发 Phase 2（thinking 持久化与迁移）

## 回滚原则

- 若 Phase 1 引发兼容异常，优先回滚归一化策略，保留日志增强。
- 若 Phase 2 迁移异常，保留新列但回退写入逻辑，不做 destructive rollback。

---

## 六、最终建议（执行决策）

建议按以下顺序实施：

1. **立即执行 Phase 1（P0）**：这是修复“滑窗坍塌”的最短路径。
2. **Phase 1 稳定后再做 Phase 2（P1）**：把 `thinking` 入库做成长期稳态能力，而不是和 P0 强绑定。
3. **在 P0 期间同步验证 assistant transcript 结构兼容性**（`assistant` vs `agent-inference`），用数据决定是否进入下一轮协议层调整。

本文件仅为修复方案设计，未包含代码变更。


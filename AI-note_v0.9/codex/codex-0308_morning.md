# codex-0308_morning

## 时间

- 更新日期：2026-03-08
- 更新目标：在不破坏 OpenAI 通用协议兼容性的前提下，修复 Opus/GPT 流式分流问题，保证 thinking 与正文都可用且不缺失。

---

## 本次结论（先说结果）

本轮已完成两类修复并做了回归：

1. **协议兼容优先（P0）**
   - 非 Web 客户端不再接收本地 UI 自定义 SSE 事件。
   - Web 客户端继续保留 `search_metadata` 增强事件。

2. **分流稳定性增强（P0）**
   - `stream_parser` 增强 value 子块识别：支持 `/s/N/value/<index>` 显式索引路径（不仅是 `/value/-`）。
   - 解决一类“正文子块未注册，回退继承 thinking 段类型”的误判风险。

3. **回归守护（新增）**
   - 新增 `test_stream_regression.py`，可自动校验：
     - non-web：无自定义 `type` 事件，仍有 reasoning + content chunk
     - web：有 `search_metadata` 事件，仍有 reasoning + content chunk

---

## 实际代码改动

## 1) `app/api/chat.py`

### 改动点

- 在 `item_type == "search"` 分支中，`search_metadata` 事件改为仅 `client_type == "web"` 时发送。
- non-web 下继续通过正文 markdown 注入搜索信息，不发送自定义事件。

### 目的

- 避免非 Web 客户端收到额外自定义事件而出现协议解析风险。
- 保持 OpenAI 标准 chunk 输出路径稳定。

---

## 2) `app/stream_parser.py`

### 改动点

- 新增 `_extract_value_add_index(path)`：识别 `o:"a"` 且路径为 `/s/N/value/<idx|->` 的 value block 注册行为。
- 在段落子块注册逻辑中，兼容：
  - `/value/-`（自动追加）
  - `/value/1`（显式索引）
- `next_val_id` 更新改为 `max(current, vid + 1)`，防止索引回退覆盖。

### 目的

- 覆盖 Notion 上游“显式 value 索引追加”形态。
- 降低正文误入 thinking 的概率，尤其是 Opus/GPT 某些 patch 结构下。

---

## 3) `test_stream_regression.py`（新增）

### 内容

- 使用 `FastAPI TestClient` + fake upstream pool/client，避免依赖真实上游波动。
- 验证两个场景：
  1. non-web 请求：
     - `custom_types == []`
     - `content_chunks > 0`
     - `reasoning_chunks > 0`
  2. web 请求：
     - `custom_types` 包含 `search_metadata`
     - `content_chunks > 0`
     - `reasoning_chunks > 0`

### 目的

- 将本轮“协议兼容 + thinking/content 共存”要求固化为可重复执行的回归检查。

---

## 验证记录

## 编译检查

- `python -m compileall app test_stream_regression.py`：通过。

## 回归脚本

- `python test_stream_regression.py`：通过。
- 输出结果（摘要）：
  - `PASS non-web StreamStats(status_code=200, custom_types=[], content_chunks=1, reasoning_chunks=1)`
  - `PASS web StreamStats(status_code=200, custom_types=['search_metadata'], content_chunks=1, reasoning_chunks=1)`

## 实流抽样（本地）

- 对 `gpt-5.2` / `claude-opus4.6` 的简单问答流抽样中，API 层可保证有 `reasoning_content` 且有至少一个 `content` chunk（由流末最终正文兜底/补齐）。

---

## 风险与边界

1. 本轮修复解决了“显式 value 索引导致的分流误判”这一已知结构性问题，但并不意味着所有模型场景都会稳定地产生大量正文增量。
2. 某些模型在简单问题上仍可能主要通过 `agent-inference` 提供文本，正文可能更多依赖流末 `final_content` 对齐。
3. 当前策略优先保证协议兼容与结果可用，不做模型名硬编码特判。

---

## 当前状态总结

- **已满足优先级 1**：non-web OpenAI 通用协议路径更干净，不再混入 Web 专用自定义事件。
- **已满足优先级 2**：thinking 与正文共存链路有回归保障，且正文不会因该类分流错误而缺失。
- **已新增守护**：`test_stream_regression.py` 可持续防回归。

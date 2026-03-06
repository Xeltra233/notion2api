# Gemini3.1-0305_night 修改记录

## 背景
用户反馈前端的部分"思考过程（Thinking）"回复会溢出，跑到正文的部分。

## 复现结论
在 Notion 流数据中，单个 `agent-inference` 数据段（segment）内部的 `value` 数组同时包含多个子值（sub-value）：
- `value[0]`：类型为 `thinking`（思考过程）
- `value[1]`：类型为 `text`（正式回复）

旧解析器无法区分同一段内部的不同子值类型，导致思考文本泄漏。

## 根因（三重缺陷）

### 1) `segment_roles` 未被正确初始化
- 当 `agent-inference` 通过 `/s/-` 路径追加时，`patch_seg = None`，导致 `segment_roles` 从未被设置。
- 后续思考文本在 `/s/2/value/0/content` 上流式传输时，`_record_thinking_segment` 仅更新 `max_thinking_segment` 而**未**设置 `segment_roles[2] = THINKING`。

### 2) `text` 子值覆盖了段位角色
- 当 `value[1]`（类型 `text`）被追加到 `/s/2/value/-` 时，段位角色分配代码将 `segment_roles[2]` 设为 `ANSWER`。
- 由于段位 2 之前未被标记为 `THINKING`，覆盖成功，导致后续所有 seg=2 的 `o:"x"` patch 被误判为正文。

### 3) 等段切换规则误判
- `should_start_answer` 中的等段规则：`patch_seg == max_thinking_segment && seg_role not in {THINKING, TOOL}` → 由于 `seg_role` 未被正确设为 THINKING，条件为 `True`，导致思考文本提前输出为正文。

## 修改内容

### stream_parser 核心修复
文件：`app/stream_parser.py`

#### 修复 1：`_record_thinking_segment` 补充段位角色标记
- 当思考文本流式到达某个段位时，除了更新 `max_thinking_segment`，还会将 `segment_roles[seg]` 设为 `PHASE_THINKING`（仅在尚未设置时）。
- 确保后续的 `text` 子值追加不会覆盖该段的角色。

#### 修复 2：区分段内子值追加与新顶层段追加
- 原代码对所有 `text` 类型的追加统一执行 `THINKING → INIT` 过渡。
- 新逻辑通过检测路径中是否包含 `/value/` 来区分：
  - **段内子值追加**（如 `/s/2/value/-`）：直接过渡到 `PHASE_ANSWER`，并设置 `answer_segment_start` 和 `segment_roles[seg] = ANSWER`。
  - **新顶层段**（如 `/s/-`）：过渡到 `PHASE_INIT`（保持原行为）。

#### 修复 3：`segment_roles` 防覆盖保护
- `text` 类型追加时，仅在段位尚未被标记为 `THINKING` 或 `TOOL` 时才设为 `ANSWER`。

## 验证

### 定向测试（模拟截图中的 RoboMaster 场景）
- 构造了与截图完全一致的 NDJSON 序列：`agent-inference` 段包含 `thinking` 和 `text` 两个子值
- 修复前：`{'thinking': 2, 'content': 4}` — "This is general knowledge" 泄漏到 content
- 修复后：`{'thinking': 3, 'content': 3}` — 所有思考文本保留在 thinking，正式回复在 content

### 回归测试（已有 NDJSON 样本）
- 样本：`AI-note/tmp_fail_rich_2.ndjson`
- 修复后：`{'thinking': 1, 'content': 88, 'search': 2}` — 正文正常输出

## 结论
本次修复从根本上解决了因 Notion 在单个 `agent-inference` 段内混合 `thinking` 和 `text` 子值而导致的思考文本溢出问题。通过三层防护（段位角色初始化、子值级过渡判断、角色覆盖保护），确保各子值类型被正确分流。

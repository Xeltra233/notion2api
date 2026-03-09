# codex-0305_thinking_debug 变更记录

## 目标
修复思考内容泄漏到正文、最终回复丢失的 bug，核心手段是在 `parse_stream()` 中引入阶段状态机，按流的实际时序动态区分思考、工具调用和最终回复三个阶段。

## 一、根本原因（已定位）

1. **`_looks_like_thinking_patch` 逻辑过于保守**：大量以 `o:"x"` 增量追加的思考文本 patch 没有 type 字段、path 也是普通路径，无法被识别为思考内容，导致泄漏到正文。

2. **状态机缺失**：`parse_stream()` 是无状态逐 patch 分类，无法依据"当前处于流的哪个阶段"来决定文本归属。

3. **`_contains_thinking_markers` 副作用**：递归检查文本字符串内容，导致正文中含有 "reasoning"、"inference" 等词汇时被误判为思考内容吞掉。

4. **JSON 片段过滤不完整**：`{"default":{"questions":[...]}}` 未被 `_looks_like_search_json_fragment` 命中，导致搜索查询 JSON 泄漏到正文。

## 二、改动清单

### `app/stream_parser.py`

#### 新增阶段状态机常量（line 33）
```python
PHASE_INIT     = "init"
PHASE_THINKING = "thinking"
PHASE_TOOL     = "tool"
PHASE_ANSWER   = "answer"
```

#### 在 `parse_stream()` 中引入状态跟踪（line 384）
状态转换规则：
- `o:"a"` + `v.type` 含 `agent-inference/thinking/reasoning` → 切换到 `THINKING`
- `o:"a"` + `v.type` 含 `agent-tool-result/tool/search/web` → 切换到 `TOOL`
- agent 阶段后的容器 patch（`text/title`）→ 回到 `INIT` 等待答案段
- 经历过 THINKING 或 TOOL 之后出现的纯文本 → 切换到 `ANSWER`

emit 规则：
- `THINKING`/`TOOL` 阶段的非搜索文本 → `{"type": "thinking", "text": ...}`
- `ANSWER` 阶段的文本 → `{"type": "content", "text": ...}`
- 搜索元数据始终 → `{"type": "search", "data": ...}`（与阶段无关）

#### 新增状态切换调试日志 `_phase_transition()`（line 403）
- `event = notion_phase_transition`
- 记录 old_phase / new_phase / 触发 patch 摘要（path/op/type）

#### 修复 `_contains_thinking_markers`（line 122）
- 删除 `isinstance(value, str)` 字符串递归分支
- 仅递归 dict/list 结构的键名，避免正文词汇误判

#### 补强 JSON 片段过滤（line 354）
在 `_looks_like_search_json_fragment()` 中新增：
- 识别 `{"default":{"questions"/"queries"...}}` 外层包装结构
- 新增 `'"default"'` 命中条件

## 三、验证记录

1. Python 语法检查通过：
   - `python -m py_compile app/stream_parser.py`

2. 本地构造流回归测试通过：
   - `thinking → tool → answer` 三阶段正确分流为 `thinking / search / content`
   - `{"default":{"questions":[...]}}` 不再泄漏到正文
   - 普通模型（无思考阶段）直出文本仍正确归为 `content`
## 四、2026-03-05 晚间补充修复（仅 thinking/search、无最终正文）

### 复现结论
- 使用同一提示词（“少废话，你GPA多少”相关）多次真实上游请求，修复前稳定复现：`content=0`，仅有 `search + thinking`。
- 典型原始 NDJSON 结构特征：
  - 思考/工具前序段：`/s/3`、`/s/5`、`/s/7`（含 `tool_use`、`agent-inference`）
  - 最终正式回复文本：`o:"x"` + `path=/s/9/value/0/content` 连续增量
  - 同段还会出现元字段（如 `finishedAt/model/tokens`）

### 根因补充
- `max_thinking_segment` 之前会被“同段元字段文本 patch”抬高（例如 `/s/9/finishedAt` 这类），导致后续 `/s/9/value/0/content` 命中“等于而非大于”场景。
- 旧逻辑在 `phase == THINKING` 时只允许 `patch_seg > max_thinking_segment` 才切到 `ANSWER`，因此最终正文被持续归类为 `thinking`。

### 本次代码修复（app/stream_parser.py）
1. 新增 `_is_value_content_path(path)`：仅将 `/value/.../content` 视作真实流式文本路径。
2. 调整 `_record_thinking_segment(seg, path)`：
   - 只在真实文本路径上更新 `max_thinking_segment`
   - 避免 `finishedAt/model/tokens` 这类元路径污染 thinking 边界
3. 补充等段切换规则：
   - 在 `phase == THINKING` 且 `patch_op == "x"` 时，若
     - `patch_seg == max_thinking_segment`
     - 且路径是 `/value/.../content`
     - 且该段未被显式标记为 `THINKING/TOOL`
   - 允许切换到 `ANSWER`，输出 `content`

### 验证结果
- 回放失败样本 `codex-note/tmp_fail_rich_2.ndjson`：
  - 修复前：`{'search': 2, 'thinking': 89, 'content': 0}`
  - 修复后：`{'search': 2, 'thinking': 2, 'content': 87}`
- 真实上游 3 次回归：均已出现 `content`（不再是 only thinking/search）
  - 示例：`{'search': 1, 'thinking': 1, 'content': 74}`、`{'search': 1, 'thinking': 1, 'content': 52}`、`{'search': 1, 'thinking': 2, 'content': 62}`

# ClaudeCode-0308_noon：Opus/GPT 思考内容分流问题深度分析

## 时间

- 分析日期：2026-03-08 中午
- 问题描述：Opus/GPT 模型下，思考区块显示正文内容，正文部分也显示正文内容
- 期望行为：思考部分展示思考内容，正文流式输出正文

---

## 一、问题现象

### 用户报告

**Opus/GPT 模型（有bug）：**
- 思考区块内部是正文内容
- 正文部分也是正文内容
- 更准确地说：思考区块在流式输出正文，输出完毕后突然复制了一份到正文区块

**Sonnet 模型（完美）：**
- 思考区块展示思考内容
- 正文区块流式输出正文

### 用户推测

怀疑是不同模型输出逻辑不同导致的，需要找到不"误伤"Sonnet的解决方案。

---

## 二、代码架构分析

### 2.1 整体流程

```
Notion AI 上游
    ↓ (NDJSON patch流)
stream_parser.py (解析)
    ↓ (结构化事件：content/thinking/search)
chat.py (SSE封装)
    ↓ (OpenAI兼容格式)
客户端 (展示)
```

### 2.2 核心分类逻辑：段落注册表

**位置：** `app/stream_parser.py` 的 `parse_stream()` 函数

**核心数据结构：**
```python
segment_types: dict[int, str] = {}          # seg_index → SEG_THINKING / SEG_TOOL / SEG_CONTENT
value_types: dict[tuple[int, int], str] = {}  # (seg_index, val_index) → 类型
next_seg_id = 0                             # /s/- 追加时分配的递增序号
next_val_id: dict[int, int] = {}            # seg_index → 下一个 value block 序号
```

**分类函数（唯一入口）：**
```python
def _classify_segment_type(effective_type: str) -> str:
    if not effective_type:
        return SEG_CONTENT
    if effective_type == "text":
        return SEG_CONTENT
    if effective_type == "title":
        return SEG_META
    if any(kw in effective_type for kw in _THINKING_TYPES):  # "agent-inference", "thinking", "reasoning", "inference"
        return SEG_THINKING
    if any(kw in effective_type for kw in _TOOL_TYPES):
        return SEG_TOOL
    return SEG_CONTENT
```

**输出规则：**
```python
if seg_owner in (SEG_THINKING, SEG_TOOL):
    yield {"type": "thinking", "text": cleaned}
else:
    yield {"type": "content", "text": cleaned}
```

### 2.3 Final Content 提取逻辑

**位置：** `app/stream_parser.py` 的 `_extract_final_content_from_record_map()` 函数

**优先级规则：**
```python
FINAL_STEP_PRIORITIES: dict[str, int] = {
    "markdown-chat": 400,   # Gemini模型
    "agent-inference": 300, # Claude模型
    "text": 200,            # 纯文本
    "title": 50,
}
```

**提取逻辑：**
```python
if step_type == "markdown-chat":
    content = _extract_markdown_chat_text(step.get("value"))
elif step_type == "agent-inference":
    content = _extract_text_from_value_items(step.get("value"))  # 提取所有 type="text" 的内容
elif step_type in {"text", "title"}:
    raw_value = step.get("value")
    if isinstance(raw_value, str):
        content = raw_value
```

---

## 三、根本原因分析

### 3.1 问题根源

**核心矛盾：**
1. `agent-inference` 段落被归类为 `SEG_THINKING`
2. Opus/GPT 模型可能在 `agent-inference` 段落中混合了思考内容和正文内容
3. Notion 的 patch 结构没有明确区分这两者

**关键发现：**
根据 `opus4.6_night.md` 的记录，之前的"段落注册表"方案已经解决了大部分问题，但对于某些模型依然存在 edge case。

### 3.2 不同模型的 Patch 结构推测

**Sonnet 模型（完美）：**
```
segment 0: type="agent-inference" → 归类为 SEG_THINKING
  ├── value[0]: 思考内容
segment 1: type="text" → 归类为 SEG_CONTENT
  ├── value[0]: 正文内容

recordMap:
  - step_type="agent-inference" (思考内容，优先级300)
  - step_type="text" (正文内容，优先级200) ← 被选中
```

**Opus/GPT 模型（有bug）：**
```
segment 0: type="agent-inference" → 归类为 SEG_THINKING
  ├── value[0]: 思考内容 + 正文内容（混合在一起）

recordMap:
  - step_type="agent-inference" (所有内容，优先级300) ← 被选中
```

### 3.3 问题链路

**流式阶段：**
1. `agent-inference` 段落被创建 → 注册为 `SEG_THINKING`
2. 所有内容（思考+正文）追加到该段落
3. 所有内容被输出为 `{"type": "thinking", "text": ...}`
4. 客户端显示在思考区块

**Final Content 阶段：**
1. `record-map` 事件到达
2. `_extract_final_content_from_record_map` 提取 `agent-inference` 的内容（包含所有内容）
3. 输出为 `{"type": "final_content", "text": ...}`
4. 客户端显示在正文区块

**结果：**
- 思考区块：流式显示所有内容（包括正文）
- 正文区块：突然显示所有内容（复制一份）

---

## 四、解决方案建议

### 方案对比

| 方案 | 描述 | 优点 | 缺点 | 推荐度 |
|------|------|------|------|--------|
| 方案1 | 内容启发式分割 | 不依赖模型名 | 不可靠，可能误判 | ⭐⭐ |
| 方案2 | 模型名称特判 | 精准针对问题模型 | 硬编码，需要维护 | ⭐⭐⭐⭐ |
| 方案3 | 调整优先级 | 简单，不针对模型 | 可能丢失内容 | ⭐⭐⭐ |
| 方案4 | 修改final_content逻辑 | 精准修复final_content | 需要理解recordMap结构 | ⭐⭐⭐⭐⭐ |

### 方案1：内容启发式分割（不推荐）

**思路：**
在 `agent-inference` 段落中，尝试检测思考内容和正文的分界线。

**实现：**
```python
def _split_thinking_and_content(text: str) -> tuple[str, str]:
    # 尝试检测分界线，如 "【回答】"、"Answer:" 等
    # 不可靠，容易误判
    pass
```

**缺点：**
- Notion AI 的输出没有固定格式的分界线
- 可能误判思考内容中的类似标记
- 维护成本高

### 方案2：模型名称特判（推荐）

**思路：**
针对 Opus/GPT 模型，降低 `agent-inference` 的优先级，或者跳过 `agent-inference` 的 `final_content`。

**实现位置：** `app/stream_parser.py`

**修改点1：调整优先级**
```python
# 需要传入模型名称，这需要修改API接口
FINAL_STEP_PRIORITIES: dict[str, int] = {
    "markdown-chat": 400,
    "agent-inference": 300,  # Opus/GPT: 200
    "text": 200,
    "title": 50,
}
```

**修改点2：模型特定逻辑**
```python
# 在 _extract_final_content_from_record_map 中
if step_type == "agent-inference" and is_opus_or_gpt_model:
    # 跳过 agent-inference，等待 text 类型
    continue
```

**缺点：**
- 需要在 `stream_parser.py` 中访问模型名称
- 需要修改 `parse_stream()` 函数签名
- 硬编码模型名称

### 方案3：调整优先级（部分推荐）

**思路：**
将 `text` 类型的优先级提高到 `agent-inference` 之上。

**实现：**
```python
FINAL_STEP_PRIORITIES: dict[str, int] = {
    "markdown-chat": 400,
    "text": 350,           # 提高
    "agent-inference": 300,
    "title": 50,
}
```

**优点：**
- 简单，不需要模型名称
- 优先选择明确的正文内容

**缺点：**
- 如果 Opus/GPT 没有输出 `text` 类型，会回退到 `agent-inference`
- 可能影响其他模型的行为

### 方案4：修改 Final Content 逻辑（最推荐）

**思路：**
在 `_extract_final_content_from_record_map` 中，同时存在 `agent-inference` 和 `text` 时，优先选择 `text`。

**实现：**
```python
def _extract_final_content_from_record_map(data: dict[str, Any]) -> dict[str, Any] | None:
    # ... 现有逻辑 ...

    # 新增：过滤策略
    # 如果同时存在 agent-inference 和 text，只保留 text
    has_text = any(c["step_type"] == "text" for c in candidates)
    if has_text:
        candidates = [c for c in candidates if c["step_type"] in ("text", "markdown-chat")]

    # ... 后续选择最佳候选的逻辑 ...
```

**优点：**
- 精准修复 `final_content` 的重复问题
- 不影响流式阶段的分类
- 不需要模型名称
- 对 Sonnet 模型无影响（它已经有 `text` 类型）

**缺点：**
- 如果 Opus/GPT 没有输出 `text` 类型，问题依然存在

### 方案5：混合方案（终极推荐）

**思路：**
结合方案3和方案4，并进行更深层的分析。

**核心洞察：**
根据 `opus4.6_night.md` 的记录，"段落注册表"方案已经解决了大部分流式分类问题。当前的问题主要在于 `final_content` 阶段。

**解决方案：**

1. **调整优先级**：将 `text` 提高到 `agent-inference` 之上
2. **优化 final_content 提取**：如果同时存在 `agent-inference` 和 `text`，只选择 `text`
3. **增加调试日志**：记录 `recordMap` 中的所有候选，便于后续分析

**实现：**

```python
# 修改1：调整优先级
FINAL_STEP_PRIORITIES: dict[str, int] = {
    "markdown-chat": 400,
    "text": 350,           # 从200提高到350
    "agent-inference": 300, # 保持不变
    "title": 50,
}

# 修改2：优化 final_content 提取
def _extract_final_content_from_record_map(data: dict[str, Any]) -> dict[str, Any] | None:
    # ... 现有的候选收集逻辑 ...

    # 新增：智能过滤
    # 如果同时存在高优先级的 text/markdown-chat，则忽略 agent-inference
    high_priority_types = {"text", "markdown-chat"}
    has_high_priority = any(c["step_type"] in high_priority_types for c in candidates)

    if has_high_priority:
        # 过滤掉 agent-inference，保留明确的正文内容
        candidates = [c for c in candidates if c["step_type"] in high_priority_types]

    # ... 后续选择最佳候选的逻辑 ...

    # 新增：调试日志
    logger.debug(
        "Final content candidates",
        extra={
            "request_info": {
                "event": "final_content_candidates",
                "total": len(candidates),
                "filtered": len([c for c in candidates if c["step_type"] in high_priority_types]),
                "best_type": best.get("step_type", "unknown"),
                "best_length": len(best.get("text", "")),
            }
        },
    )
```

**优点：**
- 精准修复 `final_content` 的重复问题
- 不影响流式阶段的分类
- 不需要模型名称
- 对 Sonnet 模型无影响（它已经有 `text` 类型）
- 对 Opus/GPT 模型有效（如果有 `text` 类型）
- 增加调试日志，便于后续分析

**缺点：**
- 如果 Opus/GPT 没有输出 `text` 类型，问题依然存在
- 但这个问题可以通过后续观察和调整解决

---

## 五、风险评估

### 5.1 对 Sonnet 模型的影响

**无影响（✅）：**
- Sonnet 模型已经明确分离 `agent-inference` 和 `text`
- 调整优先级后，依然会选择 `text` 类型
- 流式阶段的行为不变

### 5.2 对 Opus/GPT 模型的影响

**正面影响（✅）：**
- 如果有 `text` 类型，会被优先选择，避免重复
- 流式阶段的行为不变

**潜在问题（⚠️）：**
- 如果没有 `text` 类型，问题依然存在
- 但这可以通过后续观察和调整解决

### 5.3 对 Gemini 模型的影响

**无影响（✅）：**
- Gemini 使用 `markdown-chat` 类型，优先级最高
- 不受调整影响

---

## 六、实施建议

### 6.1 立即实施（推荐）

**方案5：混合方案**

1. 修改 `FINAL_STEP_PRIORITIES`
2. 优化 `_extract_final_content_from_record_map` 的过滤逻辑
3. 增加调试日志

### 6.2 观察和调整

**实施后需要观察：**
1. Opus/GPT 的 `recordMap` 中是否有 `text` 类型
2. 如果没有，需要进一步分析 Opus/GPT 的输出结构
3. 可能需要针对 Opus/GPT 做特殊处理

### 6.3 长期方案

**如果方案5不完全有效，考虑：**
1. 在 `stream_parser.py` 中增加模型名称参数
2. 针对 Opus/GPT 模型，特殊处理 `agent-inference` 的内容
3. 可能需要在 `agent-inference` 段落中启发式分割思考内容和正文

---

## 七、代码修改清单

### 文件：`app/stream_parser.py`

**修改点1：调整优先级**
```python
# 位置：第57-62行
FINAL_STEP_PRIORITIES: dict[str, int] = {
    "markdown-chat": 400,
    "text": 350,           # 从200提高到350
    "agent-inference": 300,
    "title": 50,
}
```

**修改点2：优化 final_content 提取**
```python
# 位置：第605行之后（candidates 收集完成后）
# 新增智能过滤逻辑
high_priority_types = {"text", "markdown-chat"}
has_high_priority = any(c["step_type"] in high_priority_types for c in candidates)

if has_high_priority:
    candidates = [c for c in candidates if c["step_type"] in high_priority_types]
```

**修改点3：增加调试日志**
```python
# 位置：第617行之后（best 选择完成后）
logger.debug(
    "Final content selected",
    extra={
        "request_info": {
            "event": "final_content_selected",
            "step_type": best.get("step_type", "unknown"),
            "length": len(best.get("text", "")),
            "candidates_before": len(candidates) + len([c for c in candidates if c.get("step_type") == "agent-inference"]),
        }
    },
)
```

---

## 八、验证计划

### 8.1 测试用例

**Sonnet 模型：**
- 输入：简单问题
- 期望：思考区块显示思考内容，正文区块显示正文内容
- 验证：无变化

**Opus 模型：**
- 输入：简单问题
- 期望：思考区块显示思考内容，正文区块显示正文内容（不重复）
- 验证：问题修复

**GPT 模型：**
- 输入：简单问题
- 期望：思考区块显示思考内容，正文区块显示正文内容（不重复）
- 验证：问题修复

### 8.2 回归测试

运行 `test_stream_regression.py`，确保：
- non-web 模式：无自定义事件
- web 模式：有 search_metadata 事件
- 所有模式：reasoning 和 content 都存在

---

## 九、总结

### 9.1 问题本质

Opus/GPT 模型的 `agent-inference` 段落混合了思考内容和正文内容，导致：
1. 流式阶段：所有内容被归类为 `thinking`
2. final_content 阶段：所有内容被复制到正文区块

### 9.2 解决方案

**推荐：方案5（混合方案）**
- 调整优先级，将 `text` 提高到 `agent-inference` 之上
- 优化 final_content 提取，优先选择明确的正文内容
- 增加调试日志，便于后续分析

**优点：**
- 精准修复问题
- 不影响 Sonnet 模型
- 不需要硬编码模型名称
- 便于后续观察和调整

### 9.3 后续工作

如果方案5不完全有效，需要：
1. 观察 Opus/GPT 的 `recordMap` 结构
2. 可能需要针对 Opus/GPT 做特殊处理
3. 可能需要在 `agent-inference` 段落中启发式分割思考内容和正文

---

## 十、参考文档

- `AI-note/Claude/opus4.6_thinking.md` - 段落注册表方案的设计
- `AI-note/Claude/opus4.6_night.md` - value block 级别追踪的补充修复
- `AI-note/codex/codex-0308_morning.md` - 最近的流式解析增强
- `ref/notion-2api/app/providers/notion_provider.py` - 参考项目的实现

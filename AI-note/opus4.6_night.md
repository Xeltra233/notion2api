# opus4.6_night 变更记录

## 目标

彻底解决两个持续一整天的遗留 bug：
1. **正文丢失**：有时完全没有正文输出，但 Notion 官网已输出完毕
2. **思考泄漏**：thinking 内容溢出到正文（如搜索 JSON、思考文字混入正文）

## 一、根因分析

### 旧方案的本质缺陷

`parse_stream()` 已经过 5 轮补丁（codex-0304 → codex-0305_search → codex-0305_thinking → codex-0305_thinking_debug → opus4.6_thinking），代码膨胀到 653 行，核心分类逻辑包含：

- **阶段状态机**：`PHASE_INIT → THINKING → TOOL → ANSWER`，4 个状态 + 十几个转换条件
- **关键词猜测**：`_looks_like_thinking_patch()`、`_contains_thinking_markers()`、`_looks_like_tool_patch()` 通过关键词猜测每个 patch 的归属
- **序号比较**：`max_thinking_segment`、`answer_segment_start`、`segment_roles` 多重序号跟踪
- **`should_start_answer` 决策树**：12 个分支条件判断是否进入"回答阶段"

**根本问题**：`o:"x"` patch（往已有段落追加文本）**不携带 type 字段**，只有 path 如 `/s/2/value/0/content`。旧代码对这类 patch 做关键词猜测——这不可靠，导致：
- 思考文本被误判为正文（泄漏）
- 正文被误判为思考/搜索（丢失）
- 状态机在错误时刻转换导致后续所有输出分类错误

### Notion NDJSON 流的真实结构

| patch 操作 | path 示例 | 含义 | 是否有 type |
|-----------|-----------|------|------------|
| `o:"a"` | `/s/-` | **创建新顶层段落** | ✅ `v.type` 明确标注 |
| `o:"a"` | `/s/2/value/-` | 段落内追加子块 | ✅ 有 type |
| `o:"x"` | `/s/2/value/0/content` | 往已有段落追加文本 | ❌ **无 type** |

段落 type 值示例：
- `agent-inference` → 思考过程
- `agent-tool-result` → 工具调用结果（搜索等）
- `text` / `title` → 正文回复

**关键洞察**：type 信息只在 `o:"a"` 创建新段落时出现一次。只要在那一刻记录下来，后续所有 `o:"x"` 通过段落序号查表即可，完全不需要猜测。

## 二、新方案：段落注册表（Segment Registry）

### 核心数据结构

```python
segment_types: dict[int, str] = {}  # 段落序号 → "thinking" / "tool" / "content"
next_seg_id = 0                     # 递增计数器
```

### 分类函数（唯一入口）

```python
def _classify_segment_type(effective_type: str) -> str:
    if not effective_type or effective_type in ("text", "title"):
        return SEG_CONTENT
    if any(kw in effective_type for kw in _THINKING_TYPES):
        return SEG_THINKING
    if any(kw in effective_type for kw in _TOOL_TYPES):
        return SEG_TOOL
    return SEG_CONTENT  # 未知类型默认归正文，保证不丢内容
```

### 工作流程

1. **注册**：遇到 `o:"a" + path="/s/-"` → `segment_types[next_seg_id++] = _classify_segment_type(v.type)`
2. **查表**：遇到 `o:"x" + path="/s/N/..."` → `seg_owner = segment_types[N]`
3. **输出**：
   - `seg_owner == "thinking"/"tool"` → `yield {"type": "thinking", "text": ...}`
   - `seg_owner == "content"` → `yield {"type": "content", "text": ...}`
   - 搜索元数据始终独立提取 → `yield {"type": "search", "data": ...}`

## 三、删除清单

| 删除项 | 行数 | 原因 |
|-------|------|------|
| `THINKING_PATH_KEYWORDS` | 常量 | 不再通过 path 关键词猜测 |
| `THINKING_TYPE_KEYWORDS` | 常量 | 替换为 `_THINKING_TYPES` |
| `TOOL_TYPE_KEYWORDS` | 常量 | 替换为 `_TOOL_TYPES` |
| `PHASE_INIT/THINKING/TOOL/ANSWER` | 常量 | 状态机已废弃 |
| `_is_value_content_path()` | 函数 | 不再需要判断 path 是否为内容路径 |
| `_contains_thinking_markers()` | 函数 | 递归检查字符串导致误判 |
| `_looks_like_thinking_patch()` | 函数 | 关键词猜测，完全废弃 |
| `_looks_like_tool_patch()` | 函数 | 同上 |
| `_phase_transition()` | 内部函数 | 状态机已废弃 |
| `_record_thinking_segment()` | 内部函数 | 序号跟踪已废弃 |
| `should_start_answer` 决策树 | ~40行 | 12 个分支全部删除 |

## 四、Bug 1 修复：正文丢失

**原因**：`should_start_answer` 决策树中，当 `phase == PHASE_THINKING` 且 `patch_seg == max_thinking_segment` 时，多个条件互相矛盾：
- `_is_value_content_path()` 检查过于严格
- `seg_role` 可能被 sub-value append 覆盖为 THINKING
- 结果正文被归为 thinking，前端只在折叠面板里显示

**新方案如何解决**：`o:"a" + path="/s/-"` 创建的正文段落 type 为 `"text"`，注册时就标记为 `SEG_CONTENT`。后续所有 `o:"x"` 在该段落上追加的文本都查表归为 content，不可能被误判。

## 五、Bug 2 修复：思考泄漏

**原因**：`_looks_like_thinking_patch()` 通过 `_contains_thinking_markers()` 递归检查 patch value 的字符串内容，正文中含有 "reasoning"、"inference" 等词汇时被误判为思考。同时 `/s/2` 被硬编码认为是思考段落。

**新方案如何解决**：完全不检查文本内容，只看 `v.type` 结构化字段。正文段落无论内容里有什么词，type 是 `"text"` 就归正文。

## 六、统计

| 指标 | 旧代码 | 新代码 |
|------|--------|--------|
| 文件总行数 | 653 | 490 |
| `parse_stream` 行数 | ~200 | ~100 |
| 分类函数数量 | 5 个 | 1 个 (`_classify_segment_type`) |
| 状态变量 | 6 个 | 2 个 (`segment_types`, `next_seg_id`) |
| 分支条件 | 12+ | 2 (`if seg_owner in (THINKING, TOOL)` / `else`) |

## 七、未修改的文件

- `app/api/chat.py` — 下游管道已正确处理 content/search/thinking 三种类型
- `frontend/index.html` — thinking_chunk 和 search_metadata 的 SSE 处理无需变更
- `app/notion_client.py` — 流传输层无关
- 所有搜索相关工具函数 — 搜索功能一直正确，保持不动

## 八、补充修复：value block 级别追踪

### 问题

段落注册表只追踪顶层 segment 类型。但 Notion 会将思考和正文放在**同一个 segment** 内：

```
segment 2 (type="agent-inference")        ← 注册为 SEG_THINKING
  ├── value[0] (继承 agent-inference)     ← 思考文本 ✓
  └── value[1] (type="text", 通过 /s/2/value/- 追加)  ← 正文！但被归为 thinking ✗
```

所有 `o:"x"` 在 `/s/2/value/1/content` 追加的正文，因为 `segment_types[2] = SEG_THINKING`，全部被输出为 thinking。

### 修复

新增两层追踪：

```python
value_types: dict[tuple[int, int], str] = {}  # (seg_idx, val_idx) → 类型
next_val_id: dict[int, int] = {}              # seg_idx → 下一个 value block 序号
```

注册规则：
1. `o:"a" + /s/-` 创建新 segment → `value_types[(N, 0)]` 继承 segment 类型
2. `o:"a" + /s/N/value/-` 追加子块 → `value_types[(N, vid++)] = classify(type)`

分类规则：
1. `o:"x" + /s/N/value/M/content` → 查 `value_types[(N, M)]`
2. 查不到 → 回退到 `segment_types[N]`
3. 都查不到 → 默认 `SEG_CONTENT`

新增辅助函数 `_extract_value_index(path)` 从 path 中提取 value block 序号。

### 效果

```
segment 2 (type="agent-inference")
  ├── value[0]: value_types[(2,0)] = SEG_THINKING → thinking ✓
  └── value[1]: value_types[(2,1)] = SEG_CONTENT  → content  ✓
```

## 九、验证

- Pylance 语法检查通过 ✅
- uvicorn --reload 启动成功 ✅
- 等待实际对话测试


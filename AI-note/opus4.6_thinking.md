# opus4.6_thinking 变更记录

## 目标

修复"思考过程泄漏到正文"的问题。彻底替换之前 Codex 实现的基于关键词猜测的阶段状态机，改用**段落注册表**（Segment Registry）方案——完全依赖 Notion 后端自己标注的 `type` 字段来分类。

## 一、问题根因

### Notion NDJSON 流的结构

Notion 的 `runInferenceTranscript` 返回 NDJSON 流，每行是一个 `{"type":"patch", "v":[...]}` 对象。其中的 patch 有两类关键操作：

| 操作 | 含义 | path 示例 | 是否携带 type |
|------|------|-----------|--------------|
| `o:"a"` | 创建新段落（append） | `/s/-` | ✅ `v.type` 明确标注 |
| `o:"x"` | 往已有段落追加文本 | `/s/2/value/0/content` | ❌ 无 type 字段 |

段落类型示例：
- `agent-inference` → 思考过程
- `agent-tool-result` → 工具调用（搜索等）
- `text` / `title` → 正文回复

### 旧方案的缺陷（阶段状态机）

1. **对 `o:"x"` patch 运行 `_looks_like_thinking_patch()`** 做关键词猜测——但 `o:"x"` 根本没有 type 字段，猜测不靠谱。
2. **`_contains_thinking_markers()` 递归检查字符串内容**——正文中含 "reasoning"、"inference" 等词时误判为思考内容。
3. **段落序号跟踪失败**——`/s/-` path 无数字序号，`_extract_segment_index("/s/-")` 返回 `None`，导致 `max_non_answer_segment` 永远未被设置。
4. **`should_start_answer` 逻辑过早触发**——phase 从 THINKING 切到 INIT 后，下一个 `o:"x"` 就被判定为 ANSWER，实际上那还是思考文本的延续。

## 二、改动清单

### 文件：`app/stream_parser.py`

#### 删除的代码

| 项目 | 说明 |
|------|------|
| `THINKING_PATH_KEYWORDS` | 不再通过 path 关键词猜测思考段落 |
| `PHASE_INIT / THINKING / TOOL / ANSWER` | 不再使用阶段状态机 |
| `_contains_thinking_markers()` | 递归检查导致误判，移除 |
| `_looks_like_thinking_patch()` | 基于关键词猜测，移除 |
| `_looks_like_tool_patch()` | 同上，移除 |
| `_phase_transition()` 内部函数 | 状态机已废弃 |
| `_record_non_answer_segment()` 内部函数 | 序号跟踪已废弃 |

#### 新增的代码

**段落分类常量：**
```python
SEG_THINKING = "thinking"
SEG_TOOL     = "tool"
SEG_CONTENT  = "content"
```

**`_classify_segment(effective_type)` 函数：**
根据 `o:"a"` patch 的 `type` 字段判断段落归属。仅检查 type，不做任何内容猜测：
- type 含 `THINKING_TYPE_KEYWORDS` → `SEG_THINKING`
- type 含 `TOOL_TYPE_KEYWORDS` → `SEG_TOOL`
- type 为 `text`/`title` 或空 → `SEG_CONTENT`

**`parse_stream()` 核心重写——段落注册表：**

```
segment_types = {}       # segment_index → "thinking" | "tool" | "content"
next_segment_index = 0   # /s/- 追加时分配的递增序号
```

逻辑流程：
1. 遇到 `o:"a"` + `path="/s/-"`（新建顶层段落）：
   - 分配序号 `seg_idx = next_segment_index++`
   - 从 `v.type` 读取类型，调用 `_classify_segment()` 归类
   - 存入 `segment_types[seg_idx]`
2. 遇到 `o:"a"` + `path="/s/N/..."` （段落内追加）：
   - 取已有段落序号 N，确保 `next_segment_index` 不低于 N+1
3. 遇到 `o:"x"` + `path="/s/N/..."`（文本追加）：
   - 查表 `segment_types[N]` 获得归属
4. 输出：
   - `seg_owner == "thinking" | "tool"` → `yield {"type": "thinking", "text": ...}`
   - `seg_owner == "content"` → `yield {"type": "content", "text": ...}`
   - 搜索元数据始终独立提取 → `yield {"type": "search", "data": ...}`

#### 保留的代码（未修改）

- `_looks_like_search_patch()` — 搜索检测仍有效
- `_extract_search_data_from_patch()` — 搜索元数据提取
- `_looks_like_search_json_fragment()` — JSON 文本检测
- `_strip_lang_tags()` — lang 标签清理
- `_extract_text_from_patch()` — 文本提取
- 所有搜索收集相关函数

### 文件：`app/api/chat.py`

无修改。`chat.py` 的 `_normalize_stream_item()` 已正确处理 `content`/`search`/`thinking` 三种类型，下游管道无需调整。

### 文件：`frontend/index.html`

无修改。前端已有 `thinking_chunk` 和 `search_metadata` 两种 SSE 事件处理，UI 面板结构完整。

## 三、对比总结

| | 旧方案（状态机） | 新方案（段落注册表） |
|---|---|---|
| `o:"x"` 分类 | 关键词猜测 + 序号比较 | 查表 `segment_types[N]` |
| 思考识别 | `_looks_like_thinking_patch()` | Notion `v.type` 字段 |
| 误判风险 | 高（正文含关键词即误判） | 无（仅依赖结构化 type） |
| 代码复杂度 | 590 行，4 个阶段常量，3 个分类函数 | ~460 行，无状态机 |
| `/s/-` 处理 | 序号提取返回 None，跟踪失败 | 递增计数器分配序号 |

## 四、验证

- Pylance 语法检查：`stream_parser.py` 通过 ✅
- uvicorn `--reload` 自动重载并启动成功 ✅
- 等待实际对话测试确认思考/正文分离效果

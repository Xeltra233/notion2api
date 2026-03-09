# Gemini-CLI-0308_advice：基于原始数据 real.md 的 Opus/GPT 修复战术指南

## 1. 核心发现 (Key Discovery)

通过对 `real.md` 原始数据的解剖，我们确认了 Opus 4.6 模型的分流机制并非基于 XML 标签，而是基于 **Segment 内部的索引级定义 (Item-level Type Definition)**。

### 关键证据 (Evidence from real.md)：
- **段落初始化 (L338)**：`agent-inference` 段落创建时，其 `value` 数组的第一个元素 (`value[0]`) 显式定义为 `type: "thinking"`。
- **增量更新 (L358)**：路径明确指向 `/s/5/value/0/content`。这代表更新的是第 0 个项（思考项）。
- **内容混合 (L1050)**：在 `thinking` 项的 `content` 中，**同时包含了思考过程和最终答案**。
- **正文追加 (L1060)**：在同一个段落中，Notion 随后追加了第二个元素 (`value[1]`)，其 `type: "text"`，仅包含纯净的答案。

---

## 2. 失败原因审计 (Why Previous Fixes Failed)

我们之前的 `stream_parser.py` 逻辑存在两个致命盲区：
1. **粒度过粗**：将 `agent-inference` 段落整体标记为 `SEG_THINKING`。这导致即便路径指向了 `value[1]` (Text)，解析器依然认为它是思考内容。
2. **忽略索引**：解析器只识别路径中的 `/value/` 关键字，而没有解析其后的数字索引（`0`, `1`, `...`）。对于 Opus 这种“先思考后正文都在一段内”的模型，不解析索引就无法分流。

---

## 3. 给 ClaudeCode 的修复建议 (Implementation Advice)

### A. 升级“段落注册表”为“值项注册表 (Value Item Registry)”
不要只存储 `segment_id -> type`，要存储 `(segment_id, item_index) -> type`。
- 当收到 `agent-inference` 段落的初始化 patch 时，遍历其 `value` 数组，记录每个索引对应的 `type`。

### B. 精细化路径解析 (Path-Aware Parsing)
修改 `parse_stream` 中的路径提取逻辑：
- 识别 `.../value/(\d+)/content`。
- 提取其中的数字索引 `idx`。
- 根据 `(seg_id, idx)` 查找注册表中的真实类型。
- 如果类型是 `thinking` -> 输出到 `reasoning_content`。
- 如果类型是 `text` -> 输出到 `content`。

### C. 解决 Opus 的“内容溢出” (Handling Overflow)
由于 Opus 的 `thinking` 项内容可能包含正文：
1. **流式策略**：一旦检测到该段落开始输出 `type: "text"` 的增量（即 `idx` 增加），立刻终止对之前 `thinking` 项的展示（或将其标记为完成）。
2. **去重策略**：在 `final_content` 提取阶段，如果同时存在 `thinking` 和 `text` 项，且 `thinking` 项的末尾包含 `text` 项的内容，应进行字符串截断，确保思考区块不显示正文。

---

## 4. 结论

**不要再试图猜测模型。** 请直接信任 NDJSON 路径中的索引和对应的 `type` 字段。只要实现了“路径索引 -> 类型映射”的精准追踪，Opus/GPT 的分流问题将从根本上得到解决。

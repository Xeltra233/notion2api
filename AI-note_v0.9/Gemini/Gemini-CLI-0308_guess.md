# Gemini-CLI-0308_guess：Opus/GPT 思考内容分流失败的底层协议猜想

## 1. 核心矛盾点 (Core Conflict)

经过多次 Debug 和对 `ref/notion-2api` 项目的审计，我们发现目前项目在处理 Opus (4.6) 和 GPT (5.2) 模型时的“思考/正文混淆”问题，本质上是由于对 Notion AI **非对等协议实现**的误解。

- **现状**：Sonnet 模型运行完美，因为其数据流是**结构化分流**。
- **痛点**：Opus/GPT 模型运行异常，因为其数据流是**内容级分流**。

---

## 2. 深度协议猜想 (Protocol Hypotheses)

### 猜想一：双重分流策略 (Dual Dispatch Strategy)

Notion 对不同模型的 SSE (NDJSON) 输出采用了完全不同的封装方式：

| 模型类型 | 分流方式 | 数据结构示例 | 识别机制 |
| :--- | :--- | :--- | :--- |
| **Sonnet** | **结构化 (Structural)** | `p: [".../s/0/value"]` (Thinking)<br>`p: [".../s/1/value"]` (Content) | 基于 `segment_id` 和路径切换 |
| **Opus/GPT** | **内容级 (In-band)** | `p: [".../s/0/value"], v: "<thinking>思考中...</thinking>这是正文"` | 基于字符串内部 XML 标签 |

### 猜想二：Information Gap 的本质

我们之前的 `stream_parser.py` 逻辑建立在 **“Notion 一定会通过路径 (Path) 区分段落”** 的假设上。
- 当 Opus/GPT 输出时，它们全程只占用一个 `segment` (通常是 `agent-inference`)。
- 我们的解析器看到 `agent-inference` 就无脑归类为 `SEG_THINKING`。
- **结果**：即便 `v` 字段里出现了正文，只要它还属于那个 `segment`，就会被全部塞进思考区块。

---

## 3. 参考项目 (notion-2api) 的佐证

在 `ref/notion-2api/app/providers/notion_provider.py` 中发现：
- 该项目并没有真正的流式分流逻辑。
- 它使用了极其激进的**正则表达式过滤**：
  ```python
  content = re.sub(r'<thinking>[\s\S]*?</thinking>\s*', '', content, flags=re.IGNORECASE)
  ```
- 这证实了：在某些模型（Opus/GPT）的流中，**思考内容确实是被包裹在标签内的纯文本字符串**，而不是独立的 JSON 对象。

---

## 4. 破局方案建议 (Technical Roadmap)

### A. 启发式内容解析 (Heuristic Content Parsing)
不再仅仅依赖 `segment_type`。在 `_extract_text_from_v` (或者流式输出的入口) 增加对标签的实时检测：
- **STATE_THINKING**: 捕获到 `<thinking>` 或 `<thought>` 标签后开启。
- **STATE_CONTENT**: 捕获到 `</thinking>` 或 `</thought>` 标签后切换，或者在标签不存在时根据模型名降级。

### B. 路径深度追踪 (Path Depth Tracking)
如果标签不是唯一的信号，需要检查 Opus/GPT 在输出正文时，`p` 数组的索引是否发生了变化（例如从 `["value", 0]` 变为了 `["value", 1]`），即便它们同属于一个 `segment`。

### C. 最终阶段处理 (Final Stage)
目前的 `final_content` 逻辑已经通过优先级解决了重复显示的问题，但**流式阶段的体验**必须通过上述“内容解析”来修复。

---

## 5. 结论

**不要再试图寻找完美的 `segment_type` 映射表。** 对于 Opus/GPT，分流的逻辑不在 JSON 的 Key 里，而在 `v` 对应的 Value 字符串里。我们需要一个能够“看穿”字符串内部标签的智能解析器。

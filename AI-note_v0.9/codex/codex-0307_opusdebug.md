# codex-0307_opusdebug

## 目标

分析并记录 `Claude Opus 4.6` 在当前项目中的 P0 问题：

1. 没有上下文记忆
2. 连滑动窗口记忆都失效
3. 离开聊天页再返回时，AI 回复变成三个点的加载态

本文件只记录分析、猜测、证据和预期改动范围，不直接修复代码。

---

## 一、结论

当前判断：**根因不在 memory 模块本身，而在更前面的流解析阶段，而且当前解析器只盯着 `patch`，忽略了 `record-map` 这个最终态通道。**

`Opus` 的最终可见正文，被当前解析器错误地全部归类成了 `thinking`，没有进入 `content` 通道。随后：

- 后端不会把这部分文本写入 `assistant_reply`
- 这一轮对话不会被正常持久化进 SQLite
- 后续轮次构造 transcript 时，自然拿不到上一轮 assistant 回复
- 滑动窗口、压缩摘要、主动召回全部失效

因此，“Opus 没记忆”其实是“Opus 的上一轮回复在持久化前就丢了”。

`Sonnet` 正常的原因，不是记忆模块对 `Sonnet` 特判，而是 `Sonnet` 的上游 patch 结构刚好符合当前解析器假设。

---

## 二、实测证据

### 1. 真实上游请求复现

我本地直接使用现有账号配置分别对 `claude-sonnet4.6` 和 `claude-opus4.6` 发起真实请求，统计 `parse_stream()` 产出的事件类型。

结果：

- `Sonnet`：同时产出 `search` / `thinking` / `content`
- `Opus`：只产出 `search` / `thinking`，**`content = 0`**

一次实际观测到的计数：

```text
MODEL claude-sonnet4.6
COUNTS {'search': 1, 'thinking': 12, 'content': 19}

MODEL claude-opus4.6
COUNTS {'search': 1, 'thinking': 48}
CONTENT_LEN 0
```

这说明：

- `Sonnet` 的正文确实走到了 `content`
- `Opus` 的正文在当前代码下没有进入 `content`

---

### 2. 原始 NDJSON patch 差异

进一步抓取原始 Notion patch 后，发现 `Sonnet` 和 `Opus` 的结构不同。

#### Sonnet

会先创建一个 `agent-inference` 段，作为思考过程：

```text
o='a', path='/s/-', vtype='agent-inference'
```

随后在同一个 segment 内，又追加一个新的 value block：

```text
o='a', path='/s/3/value/-', vtype='text'
```

后续正文都写在：

```text
/s/3/value/1/content
```

这正好符合当前 `value_types[(seg, value_idx)]` 的分类逻辑，所以 `Sonnet` 正常。

#### Opus

`Opus` 这次抓到的流中：

```text
o='a', path='/s/-', vtype='agent-inference'
```

之后所有用户可见回复，都直接追加在：

```text
/s/3/value/0/content
```

**没有出现后续 `vtype='text'` 的子块。**

也就是说，在当前这类流形态里：

- 从结构上看，它一直是 `agent-inference`
- 但从用户视角看，这其实已经是最终回复正文

当前解析器按 type 直接判成 `thinking`，于是整段正文被错分。

---

### 3. 后端持久化链路为何断掉

`app/api/chat.py` 当前逻辑是：

- `thinking` 只进入 `reasoning_content`
- 只有 `content` 才会追加到 `full_text_accumulator`
- 最终只有 `full_text_accumulator.strip()` 非空才调用 `_persist_round()`

这意味着：

1. `Opus` 回复如果全被判成 `thinking`
2. 那么 `full_text_accumulator` 一直为空
3. 本轮 assistant 回复不会正常写库

于是下一轮虽然前端还保留了本地消息列表，但后端真正用于记忆的 SQLite 会话里，没有上一轮 assistant。

---

### 4. 为什么会出现“返回页面后三个点”

前端当前有两个叠加问题：

1. assistant 消息内容为空时，`appendMessageToDOM()` 会把它渲染为 typing indicator
2. 请求结束后，前端仍然会把空字符串 assistant 写入 `chat.messages`

所以：

- 当某次 `Opus` 回复没有进入 `content`
- 前端本地就保存了一条 `assistant: ""`
- 重新进入这个聊天页时，这条空 assistant 会再次被渲染成“三个点”

这不是“真正还在加载”，而是空字符串消息被当成加载态复用了。

---

### 5. 数据库已有污染

检查 `data/conversations.db` 时，已发现多条空 assistant 记录。

一次统计结果：

```text
total messages: 789
empty assistant messages: 29
```

并且有些 `compressed_summaries` 已经压到了“本轮对话中，用户和AI均未提供具体内容”这类摘要。

这说明问题已经不只是 UI，而是：

- 历史会话数据已被空 assistant 污染
- 部分压缩摘要也已经被坏数据影响

---

## 三、参考项目 `ref/notion-2api` 的关键启发

阅读 `ref/notion-2api/app/providers/notion_provider.py` 后，确认这个项目有两个非常值得借鉴的点。

### 1. 它不是只看 patch，而是三通道一起看

参考实现的 `_parse_ndjson_line_to_texts()` 会同时消费：

1. `markdown-chat`
   - 直接整包正文
   - 主要用于 Gemini 风格线程
2. `patch`
   - 增量碎片
   - 用来做实时流式拼接
3. `record-map`
   - 最终数据库状态
   - 从 `thread_message -> value -> value -> step` 里提取完整正文

它最后的决策逻辑也很明确：

- 如果拿到了 `record-map` 或 `markdown-chat` 的最终正文，就优先使用它
- 否则才退回到纯 patch 增量拼接

这比我们现在“只看 patch”稳健很多。

---

### 2. 它的 payload 是按模型协议分流的

参考实现的 `_prepare_payload()` 会根据 `mapped_model` 动态决定：

- `threadType`
- `config`
- `context`
- 是否加 `debugOverrides`

其中最明显的是：

- `vertex-*` -> `threadType = markdown-chat`
- 其他模型 -> `threadType = workflow`

而我们当前项目里：

- `config.type` 固定写死为 `workflow`
- 请求 payload 的 `threadType` 也固定为 `workflow`
- `createThread / isPartialTranscript / transcript` 结构没有按模型分流

这虽然不一定是本次 `Opus` 失忆的唯一根因，但确实说明我们当前协议层更“单一假设”，容错性更差。

---

### 3. 它把 `record-map` 当成权威最终态

这点对本次 P0 特别重要。

我已经在**当前项目、当前账号、当前 Opus/Sonnet 实测**中确认：

- `Sonnet` 请求返回了 `record-map`
- `Opus` 请求也返回了 `record-map`

一次实际统计：

```text
MODEL claude-sonnet4.6 STATUS 200
TYPE_COUNTS {'patch-start': 1, 'patch': 35, 'record-map': 2}

MODEL claude-opus4.6 STATUS 200
TYPE_COUNTS {'patch-start': 1, 'patch': 44, 'record-map': 2}
```

并且 `record-map` 中确实能取到完整正文候选：

```text
RECORD_MAP_CANDIDATES [('agent-inference', '...完整中文回复...')]
```

这说明：

> 当前项目不是拿不到 `Opus` 的最终正文，而是拿到了却完全没解析它。

---

## 四、当前项目与参考项目的关键差异

### 差异 1：当前 `stream_parser.py` 只解析 `patch`

当前实现中：

- 只处理 `data.get("type") == "patch"`
- 非 patch 行全部直接跳过

这意味着当前解析器会忽略：

- `patch-start`
- `record-map`
- 未来可能出现的 `markdown-chat`

所以它天然缺少最终态补位能力。

---

### 差异 2：当前 parser 只按 patch 内 `type` 分类

当前 parser 的核心假设是：

- `agent-inference` -> `thinking`
- `text` -> `content`

这个假设对 `Sonnet` 通常成立，但对 `Opus` 不稳定，因为 `Opus` 的最终回复可能根本不再单独发 `text` 子块。

因此，**仅靠 patch 的 type 分类，无法覆盖 `Opus` 的全部流形态。**

---

### 差异 3：当前 payload 构造分散且固定

现在项目里 payload 的构造逻辑是拆开的：

- `app/conversation.py` 负责生成 `config/context/dialog blocks`
- `app/notion_client.py` 负责补充请求级字段

问题在于：

- `config.type` 固定 `workflow`
- `threadType` 固定 `workflow`
- `createThread = True`
- `isPartialTranscript = False`

缺少一个统一的、按模型分流的 payload builder。

相比之下，参考项目的 `_prepare_payload()` 虽然简单，但结构更清晰，后续扩展成本更低。

---

### 差异 4：当前 `chat.py` 没有“最终正文覆盖”机制

当前流式输出逻辑默认：

- patch 里的 `content` 就是最终正文
- 只要流里没收到 `content`，最后就没有 assistant reply

它没有“权威最终正文”这个概念。

而参考项目明确区分：

- `incremental_fragments`
- `final_message`

最后优先使用 `final_message`。

这正是当前项目在 `Opus` 上缺的兜底。

---

## 五、修复策略重排

在加入参考项目思路后，我对修复计划做出调整：

### 第一优先级：三通道解析，而不是只修 patch 分类

原先我的主要思路是：

- 在 patch 层做“缓冲后判定”
- 尽量从 `agent-inference` 和 `text` 的结构差异中恢复正文

这个思路仍然有价值，但现在已经不是唯一主线。

新的主线应当是：

> **把 `record-map` 接回来，作为权威最终正文；patch 负责实时增量；markdown-chat 作为 Gemini 风格兼容通道。**

也就是：

- 频道 A：`markdown-chat`
- 频道 B：`patch`
- 频道 C：`record-map`

其中：

- `patch` 负责实时性
- `record-map` 负责最终正确性
- `markdown-chat` 负责特定模型协议兼容

---

### 第二优先级：`chat.py` 必须认识“最终权威正文”

加入 `record-map` 之后，`chat.py` 不能再只靠 `full_text_accumulator`。

需要维护至少两套状态：

1. `streamed_content_accumulator`
   - patch 增量累积出来的正文
2. `authoritative_final_content`
   - 从 `record-map` / `markdown-chat` 提取的最终正文

最终规则应当是：

- 若 `authoritative_final_content` 存在，则它优先于增量拼接结果
- 若它比已流出的正文更完整，应在流末尾做一次补位
- 持久化时，必须优先写入它

否则 parser 就算解析出 `record-map`，P0 也不会真正修复。

---

### 第三优先级：payload builder 做模型协议分流

这一步不是 P0 唯一 blocker，但建议在修复过程中一起梳理，否则 parser 修好后，后面还会继续踩不同模型的协议坑。

建议新增统一的 payload builder，职责类似参考项目 `_prepare_payload()`：

1. 根据模型 ID 决定 `threadType`
2. 根据模型协议决定 `config`
3. 根据模型协议决定 `context.surface`
4. 决定 `createThread / isPartialTranscript / debugOverrides`

当前判断：

- `Opus / Sonnet / GPT` 仍大概率属于 `workflow`
- `Gemini` 未来若切到不同上游协议，应能切 `markdown-chat`

这一步更偏“架构矫正”，优先级略低于 `record-map` 接入，但应该纳入本次计划书。

---

### 第四优先级：patch 侧保留结构缓冲，作为 UX 优化

即便引入 `record-map`，也仍建议保留对 patch 的“缓冲后判定”增强。

原因：

- 如果完全不处理，`Opus` 在流式过程中仍可能把最终正文先展示到 thinking 区
- 然后流末尾又通过 `record-map` 在正文区补一次，造成视觉重复

因此 patch 侧的增强可以作为“去重和提纯”：

1. 对 `agent-inference` 先做短暂缓冲
2. 若同 segment 后续出现 `text` 子块
   - 缓冲部分判为 thinking
3. 若直到 segment 结束都没有 `text` 子块
   - 优先等待 `record-map`
   - 若 `record-map` 也没有，则回退为 content

换言之：

- `record-map` 解决正确性
- patch buffer 解决流式观感

---

## 六、最终计划书

### 阶段 1：增强 `app/stream_parser.py`

目标：从“只看 patch”升级为“三通道解析器”。

计划：

1. 保留现有 patch 解析能力
   - 继续输出 `content/search/thinking`
2. 新增 `record-map` 解析
   - 从 `recordMap.thread_message.*.value.value.step` 提取最终正文
   - 识别 `step.type == 'agent-inference'` 和 `step.type == 'markdown-chat'`
3. 预留 `markdown-chat` 直接事件解析
4. 为下游输出新的事件类型
   - 例如 `final_content`
   - 或 `final` / `authoritative_content`

注意：

- 这一步不应该破坏现有 Sonnet 搜索与 thinking 流
- `record-map` 只做新增通道，不替换现有 patch 搜索逻辑

---

### 阶段 2：改造 `app/api/chat.py`

目标：让流式响应和持久化都能使用“权威最终正文”。

计划：

1. 新增对 `final_content` 事件的处理
2. 分离：
   - patch 流中的临时正文
   - 最终权威正文
3. 在流结束前决定最终 assistant reply
   - 优先 `final_content`
   - 其次 `streamed_content_accumulator`
4. 如果此前没有流出正文，而 `final_content` 存在
   - 在 `[DONE]` 前补一个 content chunk 给前端
5. 持久化时绝不允许再次把空 assistant 当成成功轮次

这是修复 P0 的必要步骤，不再只是“可选补监控”。

---

### 阶段 3：重构 payload 生成逻辑

目标：建立统一的模型协议分流层。

建议落点：

- 优先考虑放在 `app/notion_client.py`
- 或抽成新的 payload helper

计划：

1. 把目前分散在 `conversation.py` 和 `notion_client.py` 的 payload 逻辑收口
2. 参考 `ref/notion-2api` 的 `_prepare_payload()`
3. 根据模型映射输出：
   - `threadType`
   - `config`
   - `context`
   - `debugOverrides`
4. 评估 assistant 历史在 transcript 中的表示方式
   - 当前：`type='assistant'`
   - 参考：`type='agent-inference' + text value`

这里要谨慎：

- 这一步可能影响 Sonnet
- 因此应放在 parser/chat 主修之后

---

### 阶段 4：前端空消息防御

目标：消除“三个点假加载”并减少脏数据继续扩散。

计划：

1. 结束请求时，如果 assistant 为空且没有权威正文
   - 不落本地历史
2. 历史渲染时，空 assistant 不再显示 typing
3. 可选清洗 `localStorage` 中已有坏消息

这一步主要是症状治理，不是 P0 主因修复，但应一起做。

---

### 阶段 5：历史脏数据清理

目标：修复已被污染的旧会话。

计划：

1. `conversation.py` 过滤 recent window 中的空 assistant
2. 评估是否需要脚本清理：
   - `messages` 空 assistant
   - `compressed_summaries` 的空摘要轮次
3. 必要时提示用户旧会话需重建

---

## 七、风险控制

### 对 Sonnet 的保护原则

1. 不按模型名硬编码“Opus 特判正文”
2. 不在 `chat.py` 里做“没 content 就把 thinking 全当正文”的粗暴 fallback
3. 优先采用：
   - `record-map` 最终态兜底
   - patch 结构缓冲
   - 最小化协议分流

这样才能最大限度避免误伤当前正常的 `Sonnet`。

---

### 对 Opus 的修复优先顺序

不是：

1. 先大改 payload
2. 再看 parser

而应该是：

1. 先接 `record-map`
2. 再让 `chat.py` 用它持久化
3. 再优化 patch 流观感
4. 最后收口 payload builder

因为当前实测已经证明：

> 在现有 payload 下，`record-map` 已经真实返回了完整正文。

所以 parser / chat 接线优先级最高。

---

## 八、能否修复

结论：**能修，而且现在比先前判断更有把握。**

原因不是“猜测更充分”，而是：

1. 已实测确认 `Opus` 和 `Sonnet` 都返回 `record-map`
2. `record-map` 中已有完整最终正文
3. 当前代码只是没解析、没使用它

预期结果：

1. `Opus` 正文恢复
2. assistant 回复重新写库
3. 滑动窗口恢复
4. 压缩摘要恢复
5. 页面返回时不再只剩三个点

但要注意：

- 新会话最先受益
- 旧会话可能仍需清理坏数据

---

## 九、本次分析涉及的核心文件

当前项目：

- `app/stream_parser.py`
- `app/api/chat.py`
- `app/notion_client.py`
- `app/conversation.py`
- `frontend/index.html`
- `data/conversations.db`

参考项目：

- `ref/notion-2api/app/providers/notion_provider.py`
- `ref/notion-2api/app/core/config.py`

---

## 十、最终判断

综合当前项目实测与参考项目逻辑，P0 的本质现在可以更准确地表述为：

> `Opus` 的最终正文并不是不存在，而是当前项目只消费了 patch 碎片，既没有正确识别其中的正文，也没有接入 `record-map` 这个最终态通道，导致正文未进入持久化链路，最终表现为“完全失忆”。

所以最终修复路线应当是：

1. 三通道 parser
2. `chat.py` 使用权威最终正文
3. payload 按模型协议分流
4. 前端与历史数据做空消息治理

这个版本比上一版计划更稳，也更贴近 Notion 实际协议。

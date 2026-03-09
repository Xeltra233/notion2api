# ClaudeCode-0308_afternoon：两个关键问题分析

## 时间

- 分析日期：2026-03-08 下午
- 问题描述：
  1. Opus/GPT模型正文依然在thinking区块流式输出
  2. 滑动窗口只记录用户问题，没有AI回复

---

## 问题1：Opus/GPT流式阶段问题（未解决）

### ���户反馈

> Opus和GPT模型大概率依旧会把正文放在thinking区块里面流式输出，最后在复制一份到正文区块。与之前不同的是，现在thinking区块会先展示思考部分内容，再开始流式输出正文。

**说明：** 我之前的修复方案（方案5）只解决了 `final_content` 阶段的问题，但没有解决**流式阶段**的问题。

### 根本原因

**核心矛盾：**
- Opus/GPT 模型在**同一个 `agent-inference` 段落**中混合了思考内容和正文内容
- 当前的"段落注册表"方案**无法在段落内区分**思考和正文
- 所有内容都被归类为 `SEG_THINKING`，在流式阶段输出为 `{"type": "thinking", "text": ...}`

**对比 Sonnet 模型：**
- Sonnet 明确分离 `agent-inference` 和 `text` 两个段落
- 流式阶段：thinking → content（清晰分离）
- Final content 阶段：选择 `text` 类型（我的修复已生效）

**Opus/GPT 的流结构：**
```
segment 0: type="agent-inference"  ← 唯一的段落
  ├── value[0]: 思考内容...       ← 流式输出为 thinking
  ├── value[0]: 正文内容...       ← 依然流式输出为 thinking（问题！）
  └── value[1]: 可能还有更多正文  ← 依然流式输出为 thinking（问题！）

recordMap:
  - step_type="agent-inference"   ← 之前的修复已过滤（如果有text类型）
  - step_type="text" (如果有)     ← 优先选择
```

### 问题链路

**流式阶段（问题所在）：**
1. `agent-inference` 段落被创建 → 注册为 `SEG_THINKING`
2. 所有内容（思考+正文）追加到该段落
3. **所有内容被输出为 `{"type": "thinking", "text": ...}`** ← 问题！
4. 客户端在思考区块显示所有内容（包括正文）

**Final Content 阶段（已修复）：**
1. 我的修复优先选择 `text` 类型，过滤 `agent-inference`
2. 但如果 Opus/GPT 没有生成 `text` 类型，问题依然存在

### 解决方案探索

#### 方案A：模型特定处理（推荐）

**思路：** 针对 Opus/GPT 模型，在流式阶段特殊处理 `agent-inference` 段落

**实现：**
```python
# 在 parse_stream() 中，增加模型名称参数
def parse_stream(response: requests.Response, model_name: str = "") -> Generator[dict[str, Any], None, None]:
    # ...
    is_opus_or_gpt = model_name in ["claude-opus4.6", "gpt-5.2"]

    if is_opus_or_gpt and seg_class == SEG_THINKING:
        # 启用内容分析模式
        # 尝试在段落内区分思考和正文
        pass
```

**挑战：**
- 如何在段落内区分思考和正文？
- 需要启发式规则或内容分析

#### 方案B：内容启发式分割（不推荐）

**思路：** 尝试检测 `agent-inference` 段落中思考内容和正文的分界线

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

#### 方案C：接受现状（临时方案）

**思路：** 承认 Opus/GPT 的限制，在客户端层面处理

**实现：**
- 客户端检测 thinking 区块的内容
- 如果包含大量正文内容，在 UI 上做特殊处理
- 例如：在 thinking 区块显示"查看思考过程"，在正文区块显示完整内容

### 结论

**Opus/GPT 的思考/正文分流问题是一个深层次的结构问题，目前的"段落注册表"方案无法在流式阶段解决。**

**建议：**
1. **短期：** 在客户端层面处理，提供更好的UI体验
2. **中期：** 研究如何在 `agent-inference` 段落内区分思考和正文
3. **长期：** 向 Notion 反馈，要求他们在 API 中明确区分思考和正文

---

## 问题2：滑动窗口AI回复丢失bug（已定位）

### 用户反馈

> 我采用的上下文架构是"滑动窗口"+ "摘要压缩"。原先设计是滑动窗口储存近五轮的问答+调用一个轻量级模型生成滑动窗口之外的摘要，最后把摘要和滑动窗口拼接，发送给AI。但是测试发现，AI没有滑动窗口区域内部的AI回复，滑动窗口只记录了我最近的5次问题，没有AI回复。

### 根本原因

**问题定位：** `app/conversation.py` 的 `_normalize_window_messages` 方法（第152-170行）

**问题代码：**
```python
def _normalize_window_messages(self, messages: List[Dict[str, str]]) -> List[Dict[str, str]]:
    normalized: List[Dict[str, str]] = []
    expected_role = "user"
    for msg in messages:
        role = msg.get("role", "")
        content = str(msg.get("content", "") or "")
        if role not in {"user", "assistant"}:
            continue
        if not content.strip():
            continue
        if role != expected_role:  # <-- 问题在这里！
            continue  # 跳过不符合期望顺序的消息
        normalized.append({"role": role, "content": content})
        expected_role = "assistant" if expected_role == "user" else "user"

    # Keep transcript append-safe for a new user prompt.
    while normalized and normalized[-1]["role"] != "assistant":
        normalized.pop()
    return normalized
```

**问题分析：**

这个函数假设消息是严格的交替顺序：`user → assistant → user → assistant → ...`

第162-163行：`if role != expected_role: continue`

**如果消息顺序不匹配期望，就会跳过！**

**举例说明问题：**

假设 `messages` 是：
```
[
  {"role": "user", "content": "问题1"},
  {"role": "assistant", "content": "回答1"},
  {"role": "user", "content": "问题2"},
  {"role": "assistant", "content": "回答2"},
  {"role": "user", "content": "问题3"},
  {"role": "assistant", "content": "回答3"},
  {"role": "user", "content": "问题4"},
  {"role": "assistant", "content": "回答4"},
  {"role": "user", "content": "问题5"},
  {"role": "assistant", "content": "回答5"}
]
```

**正常流程（期望）：**
1. expected_role = "user"，遇到 "user" 问题1 → 加入normalized，切换为 "assistant"
2. expected_role = "assistant"，遇到 "assistant" 回答1 → 加入normalized，切换为 "user"
3. ... 依此类推，所有消息都被保留

**异常流程（如果有问题）：**
如果 `messages` 中缺少某些消息或顺序错乱，比如：
```
[
  {"role": "user", "content": "问题1"},
  {"role": "user", "content": "问题2"},  // <-- 缺少回答1，期望是assistant但遇到user
  {"role": "assistant", "content": "回答2"},
  ...
]
```

**结果：**
1. expected_role = "user"，遇到 "user" 问题1 → 加入normalized，切换为 "assistant"
2. expected_role = "assistant"，遇到 "user" 问题2 → **不匹配期望，跳过！**
3. expected_role = "assistant"，遇到 "assistant" 回答2 → 加入normalized，切换为 "user"
4. ...

**问题2（最后一个while循环）：**
```python
while normalized and normalized[-1]["role"] != "assistant":
    normalized.pop()
```

这行代码会删除末尾的user消息（如果最后一个不是assistant），这是为了确保transcript以assistant消息结束，但可能会导致问题。

### 数据库验证

**查询结果：**
```
conversation_id: 4b731615-d720-4e50-bfe1-65d9dc5a5b34
total: 10, user_count: 5, assistant_count: 5
```

**消息列表（按时间正序）：**
```
1502|user|问题1
1503|assistant|回答1
1504|user|问题2
1505|assistant|回答2
1506|user|问题3
1507|assistant|回答3
1508|user|问题4
1509|assistant|回答4
1510|user|问题5
1511|assistant|回答5
```

**结论：数据库中的消息是完整的，user和assistant成对出现！**

那么问题一定在于：
1. **`_fetch_recent_messages` 获取的顺序有问题**
2. **或者 `_normalize_window_messages` 的逻辑有问题**

### 可能的原因

**原因1：`_fetch_recent_messages` 的排序问题**

```python
rows = conn.execute(
    """
    SELECT role, content
    FROM messages
    WHERE conversation_id = ?
    ORDER BY created_at DESC, id DESC  # <-- 倒序
    LIMIT ?
    """,
    (conversation_id, limit),
).fetchall()
messages = [{"role": r["role"], "content": r["content"]} for r in rows]
messages.reverse()  # <-- 反转成正序
return messages
```

如果 `ORDER BY created_at DESC, id DESC` 的顺序不稳定（例如，同一时间戳的消息顺序不确定），`reverse()` 后的顺序可能不是 user → assistant 的交替顺序。

**原因2：同一时间戳的消息顺序不确定**

从数据库查询可以看到：
```
1772952438|2|user,assistant  # 同一时间戳，2条消息
1772952477|2|user,assistant  # 同一时间戳，2条消息
```

如果 `ORDER BY created_at DESC, id DESC` 的顺序是 `assistant → user`（而不是 `user → assistant`），那么 `reverse()` 后的顺序就是 `user → assistant`，这是对的。

但如果在某个情况下，顺序变成了 `assistant → user`，那么 `_normalize_window_messages` 就会跳过所有消息！

### 解决方案

#### 修复1：改进 `_normalize_window_messages`（推荐）

**思路：** 不要跳过不符合期望的消息，而是重新构建正确的交替顺序

**实现：**
```python
def _normalize_window_messages(self, messages: List[Dict[str, str]]) -> List[Dict[str, str]]:
    normalized: List[Dict[str, str]] = []
    for msg in messages:
        role = msg.get("role", "")
        content = str(msg.get("content", "") or "")
        if role not in {"user", "assistant"}:
            continue
        if not content.strip():
            continue
        normalized.append({"role": role, "content": content})

    # 确保消息是成对的 user → assistant
    paired: List[Dict[str, str]] = []
    i = 0
    while i < len(normalized):
        if i + 1 < len(normalized):
            if normalized[i]["role"] == "user" and normalized[i + 1]["role"] == "assistant":
                paired.extend([normalized[i], normalized[i + 1]])
                i += 2
                continue
        # 如果不成对，跳过
        i += 1

    # Keep transcript append-safe for a new user prompt.
    while paired and paired[-1]["role"] != "assistant":
        paired.pop()
    return paired
```

#### 修复2：改进 `_fetch_recent_messages` 的排序

**思路：** 确保获取的消息是正确的 user → assistant 交替顺序

**实现：**
```python
rows = conn.execute(
    """
    SELECT role, content
    FROM messages
    WHERE conversation_id = ?
    ORDER BY created_at ASC, id ASC  # <-- 改为正序
    LIMIT ?
    """,
    (conversation_id, limit),
).fetchall()
```

但这需要先获取所有消息，然后取最后 `limit` 条：

```python
all_rows = conn.execute(
    """
    SELECT role, content
    FROM messages
    WHERE conversation_id = ?
    ORDER BY created_at ASC, id ASC
    """,
    (conversation_id,),
).fetchall()
recent_rows = all_rows[-limit:] if len(all_rows) > limit else all_rows
messages = [{"role": r["role"], "content": r["content"]} for r in recent_rows]
return messages
```

### 下一步行动

**立即执行：**
1. 修复 `_normalize_window_messages` 的逻辑
2. 测试修复后的效果
3. 观察 AI 是否能看到完整的对话历史

**验证方法：**
1. 查看数据库中的消息记录
2. 查看 AI 收到的 transcript 内容
3. 确认 AI 能看到 user 和 assistant 消息

---

## 总结

### 问题1：Opus/GPT流式阶段问题
- **状态：** 未解决
- **原因：** 深层次的结构问题，`agent-inference` 段落混合了思考和正文
- **建议：** 短期在客户端处理，长期向 Notion 反馈

### 问题2：滑动窗口AI回复丢失
- **状态：** 已定位
- **原因：** `_normalize_window_messages` 的严格交替顺序检查
- **解决方案：** 改进 `_normalize_window_messages` 的逻辑
- **下一步：** 立即修复并测试

---

**相关文档：**
- `AI-note/Claude/ClaudeCode-0308_noon.md` - 之前的分析（final_content修复）
- `AI-note/Claude/opus4.6_night.md` - 段落注册表方案
- `app/conversation.py` - 对话管理实现

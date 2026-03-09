# Thread 自动清理功能整合报告

**日期**: 2026-03-06  
**修改文件**: `app/notion_client.py`  
**参考脚本**: `delete_by_threadID.py`（已可删除）

---

## 背景

`delete_by_threadID.py` 实现了通过 `saveTransactions` API 将 Notion thread 的 `alive` 字段设为 `False`，从而清除主页面对话记录的功能。目标是将此逻辑整合进主代码体系，做到**每次新 thread 生成后自动触发删除**。

---

## 修改内容

### `app/notion_client.py`

**1. 新增 import**
```python
import threading
```

**2. `NotionOpusAPI.__init__` 中新增 `delete_url`**
```python
self.delete_url = "https://www.notion.so/api/v3/saveTransactions"
```

**3. 新增 `delete_thread(thread_id)` 方法**

将 `delete_by_threadID.py` 的核心逻辑内化为类方法，直接复用实例的 `token_v2 / user_id / space_id`，无需额外配置。删除成功记录 `info`，失败记录 `warning`，不抛异常。

**4. `stream_response()` 流结束后自动触发**

```python
# 流结束后，在后台线程中异步删除本次生成的 thread，保持 Notion 主页面干净
threading.Thread(
    target=self.delete_thread,
    args=(thread_id,),
    daemon=True,
    name=f"notion-thread-gc-{thread_id[:8]}",
).start()
```

---

## 设计决策

| 决策 | 原因 |
|------|------|
| 使用 `daemon=True` 后台线程 | 避免在 generator 内部引入 asyncio 复杂度，进程退出时自动回收 |
| 失败只 warning，不抛异常 | 删除失败不应影响用户的正常对话响应 |
| 逻辑内化到类方法 | 复用已有认证信息，保持代码内聚性 |

---

## 触发时序

```
stream_response() 被调用
  ├─ 生成 thread_id = uuid4()
  ├─ POST /api/v3/runInferenceTranscript  (流式输出)
  ├─ yield chunks ... (实时返回给调用方)
  └─ 流读取完毕
       └─► Thread(delete_thread(thread_id)).start()
              └─► POST /api/v3/saveTransactions { alive: False }
```

# Notion AI API Wrapper - 项目架构与关键信息

**最后更新**: 2026-03-09 08:50
**面向**: Claude Code (AI Assistant)

## 项目概述

FastAPI 反向工程 Notion Web API，提供 OpenAI 兼容接口。核心特性：流式响应、三层记忆系统、多账号池、Thread ID 持久化。

## 核心架构

```
app/
├── server.py              # FastAPI 入口
├── api/chat.py            # Chat Completions API（核心）
├── conversation.py        # 记忆管理：滑动窗口+压缩池+归档
├── notion_client.py       # Notion API 客户端（逆向工程）
├── account_pool.py        # 多账号负载均衡
├── model_registry.py      # 模型名称映射
└── schemas.py             # Pydantic 数据模型
```

## 三层记忆系统

1. **sliding_window 表** (8轮对话，核心记忆)
   - 每轮存储：user_content + assistant_content + assistant_thinking
   - UPSERT 逻辑确保数据完整性
   - 作为 transcript 历史消息的唯一来源

2. **compressed_summaries 表** (中期记忆)
   - 超过8轮的旧对话自动压缩为摘要

3. **full_archive 表** (永久存储)
   - 所有消息的完整归档

## v0.9 关键修复（必须保持）

### 1. Thread ID 持久化 (conversation.py)
```python
# conversations 表新增 thread_id 列
def get_conversation_thread_id(conversation_id: str) -> Optional[str]
def set_conversation_thread_id(conversation_id: str, thread_id: str) -> None
```
**作用**: 整个对话复用同一个 thread_id

### 2. is_partial_transcript=True (notion_client.py)
```python
# 重用 thread 时必须设置
request_profile["is_partial_transcript"] = True
```
**关键**: 告诉 Notion 接受客户端 transcript 中的历史消息

### 3. 移除 Thread 自动删除
```python
# 不再调用 delete_thread
# 副作用：Notion 主页会累积对话（可接受）
```

### 4. 移除 Legacy 回退逻辑
```python
# get_transcript_payload() 强制使用滑动窗口
# 不再回退到 messages 表
```

## 核心流程

```python
# 1. API 层接收请求 (chat.py)
conversation_id = request.headers.get("X-Conversation-Id") or new_conversation()

# 2. 构建记忆 (conversation.py)
thread_id = get_conversation_thread_id(conversation_id)
recent_messages = get_sliding_window(conn, conversation_id)  # 从数据库读取
transcript = build_transcript(recent_messages, new_prompt)

# 3. 发送到 Notion (notion_client.py)
client.stream_response(transcript, thread_id=thread_id)

# 4. 保存回复 (conversation.py)
persist_round(conversation_id, user_prompt, assistant_reply, thinking)
set_conversation_thread_id(conversation_id, thread_id)  # 首次保存
```

## 支持的模型

- claude-opus4.6 (avocado-froyo-medium)
- claude-sonnet4.6 (almond-croissant-low)
- gemini-3.1pro (galette-medium-thinking)
- gpt-5.2 (oatmeal-cookie)
- gpt-5.4 (oval-kumquat-medium)

## 关键 Bug 修复记录

### ✅ 已修复：上下文记忆缺失 (v0.9)
**问题**: AI 无法回忆之前的对话
**原因**: 每次创建新 thread + 删除旧 thread + is_partial_transcript=false
**修复**: Thread ID 持久化 + is_partial_transcript=True

### ✅ 已修复：滑动窗口边界条件 (v0.9)
**问题**: persist_round() 和 update_sliding_window() 冲突
**修复**: 统一使用 UPSERT (INSERT ... ON CONFLICT DO UPDATE)

## 需要小心的核心问题

### ⚠️ Thread 不再自动删除
- **影响**: Notion 主页会累积大量对话记录
- **当前方案**: 可接受，用户手动清理
- **未来**: v1.0 需要实现定期清理（24小时后）

### ⚠️ 滑动窗口依赖 is_partial_transcript=True
- **关键**: 重用 thread 时必须设置此参数
- **验证**: 检查日志中 "is_partial_transcript": true
- **失败症状**: AI 又"失忆"了

### ⚠️ 数据库完整性
- **风险**: INSERT OR IGNORE 会导致静默失败
- **修复**: 使用 UPSERT 确保幂等性
- **验证**: 检查 sliding_window 表是否有完整数据

## 调试技巧

```bash
# 检查滑动窗口数据
sqlite3 data/conversations.db "SELECT conversation_id, round_number, substr(user_content,1,50) FROM sliding_window ORDER BY round_number;"

# 检查 thread_id
sqlite3 data/conversations.db "SELECT id, thread_id FROM conversations;"

# 查看关键日志
grep "is_partial_transcript" debug.log
grep "thread_id" debug.log
grep "sliding_window_query" debug.log
```

## 常见任务

### 添加新的 API 路由
在 `app/api/` 下创建新文件，在 `server.py` 中注册

### 修改记忆逻辑
主要修改 `conversation.py` 的三个函数：
- `get_sliding_window()` - 读取历史
- `persist_round()` - 保存新轮次
- `get_transcript_payload()` - 构建 transcript

### 调试 Notion API
查看 `notion_client.py` 的日志，关注：
- thread_id 是否复用
- is_partial_transcript 值
- transcript 内容

## 环境变量

```env
NOTION_ACCOUNTS=[{"token_v2": "...", "space_id": "...", ...}]
API_KEY=optional
DB_PATH=./data/conversations.db
```

## Git 提交规范

```
feat: 新功能
fix: 修复 bug
docs: 文档更新
refactor: 重构
perf: 性能优化
test: 测试相关
```

## 当前状态

- **版本**: v0.9
- **核心功能**: ✅ 完整可用
- **已知限制**: Notion 主页累积对话
- **下一步**: v1.0 Thread 自动清理

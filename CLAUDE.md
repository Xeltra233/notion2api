# Notion2API

## 项目概述
FastAPI 反向工程 Notion Web API，提供 OpenAI 兼容接口。
核心特性：流式响应、三层记忆系统、多账号池、Thread ID 持久化。

## 架构
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
1. **sliding_window 表**（8轮，核心）— user/assistant/thinking，UPSERT 写入
2. **compressed_summaries 表**（中期）— 超出8轮自动压缩
3. **full_archive 表**（永久）— 完整归档

记忆读写入口：`conversation.py` 的 `get_sliding_window()` / `persist_round()` / `get_transcript_payload()`

## 关键行为约束（不得修改）
- **Thread ID 持久化**：整个对话复用同一个 thread_id，存储在 conversations 表
- **is_partial_transcript=True**：重用 thread 时必须设置，否则 AI 失忆
- **不删除 Thread**：已移除自动删除逻辑，Notion 主页会累积对话（可接受）
- **强制滑动窗口**：`get_transcript_payload()` 不再回退到 messages 表

## 支持的模型
| 对外名称 | Notion 内部代号 |
|---|---|
| claude-opus4.6 | avocado-froyo-medium |
| claude-sonnet4.6 | almond-croissant-low |
| gemini-3.1pro | galette-medium-thinking |
| gpt-5.2 | oatmeal-cookie |
| gpt-5.4 | oval-kumquat-medium |

## 环境变量
```env
NOTION_ACCOUNTS=[{"token_v2": "...", "space_id": "...", ...}]
API_KEY=optional
DB_PATH=./data/conversations.db
```

## Git 提交规范
`feat` / `fix` / `docs` / `refactor` / `perf` / `test`

## 当前状态
- 版本：v0.9，核心功能完整可用
- 待办：v1.0 实现 Thread 定期清理（24小时后）
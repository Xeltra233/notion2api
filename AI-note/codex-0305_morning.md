# codex-0305 morning 变更记录

## 目标
在不改动前端 UI 与既有协议关键字段的前提下，为项目增加多模型支持，并将前端模型名映射到 Notion 内部模型标识符。

## 本次改动

### 1) 新增模型注册模块
新增文件：`app/model_registry.py`

- `MODEL_MAP`：
  - `claude-opus` -> `avocado-froyo-medium`
  - `claude-sonnet` -> `almond-croissant-low`
  - `gemini-pro` -> `galette-medium-thinking`
  - `gpt-5` -> `oatmeal-cookie`
- `get_notion_model(model_name: str) -> str`：未知模型回退到默认值 `avocado-froyo-medium`
- `list_available_models() -> list[str]`：返回可选模型名列表

### 2) 动态模型列表接口
修改文件：`app/api/models.py`

- 移除硬编码单模型返回
- 改为从 `list_available_models()` 动态生成 `/v1/models` 响应数据

### 3) 对话 transcript 使用模型映射
修改文件：`app/conversation.py`

- `get_transcript()` 签名调整为：
  - `get_transcript(self, notion_client, conversation_id: str, new_prompt: str, model_name: str) -> list`
- `config_block["value"]["model"]` 改为 `get_notion_model(model_name)` 动态映射
- `searchScopes: [{"type": "everything"}]`、UUID 生成逻辑、其余配置字段保持不变

### 4) Chat 接口模型校验与透传
修改文件：`app/api/chat.py`

- 新增模型白名单校验：
  - 若 `req_body.model` 不在 `list_available_models()` 中，返回 `400`
  - 错误信息包含可用模型列表
- 在调用 `manager.get_transcript()` 时传入 `req_body.model`

## 协议约束核对
- `workflow` 类型配置未改动（仍在 config block 中保持）
- `searchScopes: [{"type": "everything"}]` 未改动
- `traceId/threadId/transcript` 块 UUID 随机生成逻辑未改动
- 前端 UI 与样式未改动

## 验证
已执行：
- `rg -n "get_transcript\(" app`（确认仅一个调用点且已更新参数）
- `.\.venv\Scripts\python.exe -m compileall app main.py`（语法编译通过）
- `.\.venv\Scripts\python.exe -c "import py_compile; [py_compile.compile(p, doraise=True) for p in ['app/model_registry.py', 'app/api/models.py', 'app/api/chat.py', 'app/conversation.py']]; print('ok')"`（关键改动文件语法检查通过）

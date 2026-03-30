# Notion2API

> 一个基于 Notion 的 OpenAI 兼容 API 服务，现在已经同时包含后台运维控制台、账号池、运行时配置、usage 查询、OAuth / register 工具链、workspace 运维，以及多模态聊天能力。

🌐 中文 | [English](./README_EN.md)

## 这个项目现在是什么

Notion2API 已经不只是一个简单的 `/v1/chat/completions` 代理壳。

它现在同时包含：

- OpenAI 兼容聊天 API
- 轻量 `/v1/responses` 兼容层
- 带图片输入支持的多模态请求解析
- 多账号 Notion 账号池
- 浏览器后台运维控制台
- 基于 `admin session` 的后台鉴权
- 运行时配置编辑与代理诊断
- usage 汇总与明细查询
- OAuth callback / register 自动化工具链
- workspace 同步 / 探测 / 创建能力
- 一批无人工验证脚本

如果你原本只把它理解成一个上游代理，现在它更接近一个“带控制台和运维面板的产品化系统”。

---

## 功能总览

### 1. OpenAI 兼容 API 面

- `POST /v1/chat/completions`
- `POST /v1/responses`
- `GET /v1/models`
- 流式响应
- 模型注册与兼容归一化
- 对 OpenAI 风格请求体的校验与解析

### 2. 多模态与图片输入支持

API 同时支持纯文本消息，以及 OpenAI 风格的多模态 `content` 数组。

当前支持的用户内容类型：

- `text`
- `image_url`

当前支持的图片引用形式：

- 普通 `http://` / `https://` 图片 URL
- `data:image/...;base64,...` 形式的数据 URI
- 由服务端媒体缓存生成的 `/v1/media/...` 或自定义 public base URL 链接

前端聊天输入区已经支持图片上传 / 拖拽上传。新上传图片会优先走服务端媒体缓存，再把返回的媒体链接写入聊天内容；旧的 `data:` URI 历史消息仍保持兼容。

### 3. 账号池与账号运维

- 多账号负载均衡
- 账号启用 / 停用状态管理
- safe / raw 两种后台账号视图
- 账号导出 / 导入 / 替换
- 单账号 refresh、probe、workspace 同步、workspace 创建
- 后台动作的审计型元数据返回

### 4. 后台运维控制台

自带前端已经不再只是一个简单设置弹窗，而是一个更接近运维面板的后台工作区，包含：

- Overview
- Usage
- Accounts
- Runtime
- Diagnostics

主要后台体验包括：

- 当前浏览器会话内的后台登录恢复
- 后台登录状态恢复
- 默认按 safe / 脱敏方式渲染数据
- 运行状态卡片与账号健康视图
- usage 筛选与事件列表
- OAuth callback 导入解析

### 5. 运行时配置与诊断

后端提供了一批给运维使用的运行时配置与诊断面：

- 运行时配置编辑
- 代理健康检查
- refresh 诊断
- workspace 诊断
- 请求模板查看
- auto-register 状态与队列可见性
- workspace create dry-run 状态暴露

### 6. Usage 查询能力

后台 usage 接口同时支持汇总和事件明细：

- `GET /v1/admin/usage/summary`
- `GET /v1/admin/usage/events`

当前可按以下维度筛选：

- 时间范围
- 模型
- 账号
- 请求类型

这样后台就不只是“看账号状态”，也能回答真实的运营问题。

### 7. OAuth、register 与 callback 工具链

项目现在已经包含更完整的 OAuth 风格账号导入与 register 自动化能力：

- OAuth start payload 生成
- 面向 localhost 的 callback bridge 支持
- 后台里的 callback 解析 / finalize 流程
- refresh-status 与 refresh-diagnostics 视图
- auto-register 状态可视化
- hydration retry 与 register 门禁逻辑

后台的目标不只是“触发操作”，而是帮助你判断账号到底是需要 refresh、重新授权、补全 hydration，还是 workspace 修复。

### 8. Workspace 运维能力

项目把 workspace 相关能力单独做成了可观察、可诊断的运维面：

- workspace 同步
- workspace 诊断
- workspace create 状态
- workspace 创建请求模板
- workspace create dry-run 支持
- 面向 refresh / workspace 的探测脚本

---

## 后台安全模型

后台路由现在不再只是复用一个可重复提交的明文密码头。

当前后台流程：

1. 使用用户名 / 密码调用 `POST /v1/admin/login`
2. 获取一个短期有效的 `admin session`
3. 后续后台请求携带 `X-Admin-Session`
4. 使用当前后台账号继续访问后台控制台

当前关键行为：

- `/v1/admin/accounts/safe` 返回脱敏后的账号数据
- `/v1/admin/accounts` 和 `/v1/admin/accounts/{account_id}` 是显式 raw 视图
- `/v1/admin/accounts/export` 默认脱敏，只有 `?raw=true` 才返回原始导出
- 工具 / 状态类接口会返回 `response_mode`
- config / report / snapshot 一类接口会返回 `redaction_mode`

---

## API 认证模型

普通 API 访问和后台运维认证是分开的。

### 客户端 API 认证

对于普通 `/v1/...` 客户端请求：

- 如果 Runtime 里的 `API_KEY` 为空，则全局 Bearer 校验关闭
- 如果设置了 `API_KEY`，客户端必须发送 `Authorization: Bearer <your-key>`
- 这意味着部署时既可以走本地开放模式，也可以在后台 Runtime 中启用自定义 API key

### 后台认证

对于后台操作：

- 使用 `POST /v1/admin/login`
- 获取 `admin session`
- 后续请求发送 `X-Admin-Session`

### Chat 访问认证

对于聊天模块与聊天 API：

- 可以保持开放模式
- 也可以在 Runtime 中启用单独的 Chat password
- 启用后，前端 Chat 模块需要先完成一次 chat access 登录
- 已登录后台的 admin session 可以直通聊天模块，方便运维排查

这样可以把运维权限和普通聊天 / 模型访问权限分离开，也把后台密码和聊天访问密码拆开。

---

## 快速开始

### 1. 准备最小启动配置

如果你的部署路径是先把后台起起来，再通过 OAuth 或注册机导入账号，那么默认只需要关心后台密码和端口。

最小启动示例：

```bash
cp .env.example .env

ADMIN_PASSWORD=change-me-now
HOST=0.0.0.0
PORT=8000
```

后台登录默认使用：

- 用户名：`admin`
- 密码：你设置的 `ADMIN_PASSWORD`

账号池不再是默认启动前置项。你可以在服务启动后，再通过后台里的 OAuth / register / import 流程补充账号。

### 最小启动配置说明

默认情况下，你真正需要关心的 env 不应该很多：

| 变量 | 用途 |
| --- | --- |
| `ADMIN_PASSWORD` | 后台登录密码 |
| `HOST` / `PORT` / `HOST_PORT` | 只有在你需要自定义监听或 Docker 暴露端口时再改 |

如果你已经准备好了账号池，仍然可以继续使用 `NOTION_ACCOUNTS_FILE` 或 `NOTION_ACCOUNTS` 作为兼容入口；但更推荐先启动后台，再在 **Admin > Runtime / Accounts** 中完成后续操作。

### 高级配置说明

以下配置仍然保留兼容，但不再建议作为默认起步路径：

- `APP_MODE`
- `UPSTREAM_PROXY` / `UPSTREAM_HTTP_PROXY` / `UPSTREAM_HTTPS_PROXY`
- `ALLOWED_ORIGINS`
- `TEMP_MAIL_*`
- `REGISTER_*`
- `SILICONFLOW_API_KEY`
- refresh / workspace 相关高级配置

更完整的模板请直接看 `.env.example`。其中部分配置虽然已经能在后台 Runtime 面板里编辑，但像 `ALLOWED_ORIGINS` 这类启动期配置，修改后通常仍需要重启服务才能完全生效。

### 2. 启动服务

#### Docker

```bash
docker-compose up -d
```

#### 本地运行

```bash
pip install -r requirements.txt
uvicorn app.server:app --host 0.0.0.0 --port 8000
```

### 3. 打开本地入口

- 服务：`http://localhost:8000`
- 模型列表：`GET /v1/models`
- 后台控制台：直接打开根路径 `/`，默认先进入后台入口
- Chat 模块：登录后台后从左侧模块导航进入；如果启用了 Chat password，则还需要额外完成一次聊天访问登录

---

## 主要接口

### 公开 / 客户端接口

- `GET /v1/models`
- `POST /v1/chat/completions`
- `POST /v1/responses`

### 后台鉴权

- `POST /v1/admin/login`
- `POST /v1/admin/change-password`

### 后台账号

- `GET /v1/admin/accounts/safe`
- `GET /v1/admin/accounts`
- `GET /v1/admin/accounts/{account_id}`
- `PATCH /v1/admin/accounts/{account_id}`
- `DELETE /v1/admin/accounts/{account_id}`
- `GET /v1/admin/accounts/export`
- `POST /v1/admin/accounts/import`
- `POST /v1/admin/accounts/replace`

### 后台运行时 / 诊断

- `GET /v1/admin/config`
- `PUT /v1/admin/config/settings`
- `GET /v1/admin/config/proxy-health`
- `GET /v1/admin/oauth/refresh-status`
- `GET /v1/admin/oauth/refresh-diagnostics`
- `GET /v1/admin/workspaces/create-status`
- `GET /v1/admin/workspaces/diagnostics`
- `GET /v1/admin/request-templates`
- `GET /v1/admin/oauth/callback`
- `POST /v1/admin/oauth/callback`

### 后台 Usage

- `GET /v1/admin/usage/summary`
- `GET /v1/admin/usage/events`

---

## 前端后台控制台

前端现在更接近一个 admin-first 的运维后台，而不是单纯的聊天设置页。

根路径 `/` 默认先进入后台入口，当前主要区域包括：

- Overview
- Usage
- Accounts
- Runtime
- Diagnostics
- Chat

前端还支持：

- 当前浏览器会话内恢复后台登录状态
- 后台登录状态恢复与凭证更新入口
- OAuth callback 导入解析
- 默认按 safe / 脱敏方式渲染后台数据
- usage 筛选与事件列表展示
- workspace 与 runtime 相关动作面板
- Chat 模块单独访问控制
- 聊天输入区的图片附件处理与服务端媒体缓存链接

---

## 验证脚本

仓库里现在已经补充了一批无人工验证脚本，用于覆盖后台、运行时、账号、导出、usage、refresh 与 workspace 行为。

按职责大致可以分成下面几组：

### 认证 / Session / 安全边界

- `scripts/verify_admin_session_auth_flow.py`
- `scripts/verify_register_admin_protection.py`
- `scripts/verify_admin_redaction_modes.py`
- `scripts/verify_safe_accounts_view.py`
- `scripts/verify_usage_admin_endpoints.py`

### 后台壳与前后端语义契约

- `scripts/verify_frontend_semantic_fields_backend_contract.py`
- `scripts/verify_admin_first_entry_shell.py`
- `scripts/verify_admin_first_default_module_contract.py`
- `scripts/verify_workspace_footer_actions_contract.py`
- `scripts/verify_direct_mode_ignores_warp_proxy.py`

### Chat / 访问门禁 / 媒体

- `scripts/verify_chat_access_flow.py`
- `scripts/verify_chat_session_module_access_contract.py`
- `scripts/verify_admin_logout_chat_gate_contract.py`
- `scripts/verify_runtime_chat_session_reset_contract.py`
- `scripts/verify_chat_access_refresh_session_cleanup_contract.py`
- `scripts/verify_media_upload_flow.py`

### Refresh / Workspace / 探测流程

- `scripts/verify_refresh_action_success.py`
- `scripts/verify_create_workspace_success.py`
- `scripts/verify_refresh_probe_success.py`
- `scripts/verify_workspace_probe_success.py`

这些脚本的目的，是让关键后端行为不只依赖人工点页面验证。

---

## 说明

- 本地账号 JSON 文件应保持未提交状态
- 后台列表 / 导出默认使用 safe 视图
- raw 视图与 raw 导出必须显式触发，并带审计语义
- `ADMIN_PASSWORD` 用作后台登录密码，也可以在后台中自行修改
- `API_KEY` 可以留空用于本地开放模式，也可以设置为自定义值启用 Bearer 保护
- Chat password 是 Runtime 中的独立配置，不等于后台密码
- `media_public_base_url` 用于生成对外可访问的媒体链接；不填时默认跟随当前服务地址
- `media_storage_path` 建议指向持久化挂载目录，避免服务重启后图片缓存失效
- 如果你部署在 Zeabur 之类的平台，建议把媒体缓存目录和运行时数据一起挂到持久化卷
- workspace create 目前主要暴露 dry-run 与诊断导向的运维能力
- 项目仍支持聊天 API 使用，但后台运维已经成为一等功能

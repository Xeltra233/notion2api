# Notion2API

> 一个基于 Notion 的 OpenAI 兼容 API 服务，同时包含后台运维控制台、账号池、运行时配置、usage 查询，以及 workspace / register 自动化能力。

🌐 [English](./README.md) | 中文

## 这个项目现在是什么

Notion2API 已经不只是一个简单的聊天壳。

它现在同时包含：

- OpenAI 兼容 API
- 多账号 Notion 账号池
- 浏览器后台控制台
- 基于 `admin session` 的后台鉴权
- 运行时配置与代理诊断
- usage 汇总与明细查询
- refresh / probe / workspace 运维动作
- register 自动化与补全诊断

如果你原本只把它当成 `/v1/chat/completions` 代理，现在它的产品形态已经更接近“带后台的运维系统”。

---

## 核心能力

### API 层

- OpenAI 兼容的 `/v1/chat/completions`
- 轻量 `/v1/responses` 兼容接口
- 流式响应
- API 层对多模态请求结构的兼容解析
- 模型注册与兼容处理

### 账号池与运维动作

- 多账号负载均衡
- safe / raw 两种后台账号视图
- 账号导出 / 导入 / 替换
- 单账号 refresh、probe、workspace 同步、workspace 创建
- 带审计语义的动作日志与返回元数据

### 后台控制台

- 浏览器中的后台工作区
- `admin session` 登录流
- 默认后台凭证强制轮换
- overview / usage / accounts / runtime / diagnostics 分区
- 默认脱敏的数据暴露策略

### 运行时与诊断

- 运行时配置编辑
- 代理健康检查
- refresh 诊断
- workspace 诊断
- 请求模板查看
- auto-register 状态与队列可见性

### Usage 查询

- `/v1/admin/usage/summary`
- `/v1/admin/usage/events`
- 支持按时间、模型、账号、请求类型筛选

---

## 后台安全模型

后台路由现在不再只是复用一个可重复提交的明文密码头。

当前后台流程：

1. 使用用户名 / 密码调用 `POST /v1/admin/login`
2. 获取一个短期有效的 `admin session`
3. 后续后台请求携带 `X-Admin-Session`
4. 如果仍在使用默认后台凭证，则敏感后台操作会被阻止，直到完成密码轮换

当前关键行为：

- `/v1/admin/accounts/safe` 返回脱敏后的账号数据
- `/v1/admin/accounts` 和 `/v1/admin/accounts/{account_id}` 是显式 raw 视图
- `/v1/admin/accounts/export` 默认脱敏，只有 `?raw=true` 才返回原始导出
- 工具 / 状态类接口会返回 `response_mode`
- config / report / snapshot 一类接口会返回 `redaction_mode`

---

## 快速开始

### 1. 准备 Notion 凭据

打开 https://www.notion.so/ai 并登录，然后通过 DevTools 获取所需字段。

最少需要：

- `token_v2`
- `space_id`
- `user_id`

账号可以用两种方式提供：

- 直接写入 `.env` 的 `NOTION_ACCOUNTS`
- 放在本地 JSON 文件里，通过 `NOTION_ACCOUNTS_FILE` 加载

示例：

```bash
cp .env.example .env

NOTION_ACCOUNTS='[{"token_v2":"your_token","space_id":"your_space","user_id":"your_uid","space_view_id":"your_view","user_name":"your_name","user_email":"your_email"}]'
APP_MODE=standard
```

或者：

```bash
NOTION_ACCOUNTS_FILE=./accounts.local.json
APP_MODE=standard
```

文件格式见 `accounts.local.json.example`。

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

本地默认入口：

- 服务：`http://localhost:8000`
- 模型列表：`GET /v1/models`
- 后台控制台：打开自带前端，在设置 / 后台区域登录

---

## 主要后台接口

### 鉴权

- `POST /v1/admin/login`
- `POST /v1/admin/change-password`

### 账号

- `GET /v1/admin/accounts/safe`
- `GET /v1/admin/accounts`
- `GET /v1/admin/accounts/{account_id}`
- `PATCH /v1/admin/accounts/{account_id}`
- `DELETE /v1/admin/accounts/{account_id}`
- `GET /v1/admin/accounts/export`
- `POST /v1/admin/accounts/import`
- `POST /v1/admin/accounts/replace`

### 运行时 / 诊断

- `GET /v1/admin/config`
- `PUT /v1/admin/config/settings`
- `GET /v1/admin/config/proxy-health`
- `GET /v1/admin/oauth/refresh-status`
- `GET /v1/admin/oauth/refresh-diagnostics`
- `GET /v1/admin/workspaces/create-status`
- `GET /v1/admin/workspaces/diagnostics`
- `GET /v1/admin/request-templates`

### Usage

- `GET /v1/admin/usage/summary`
- `GET /v1/admin/usage/events`

---

## 前端后台控制台

前端现在已经不是单纯的设置弹窗，而是更接近运维后台工作区。

当前包含的主要区域：

- Overview
- Usage
- Accounts
- Runtime
- Diagnostics

前端还支持：

- 在当前浏览器会话中恢复后台登录状态
- 默认后台密码轮换引导
- OAuth callback 解析与导入
- 默认按 safe 方式渲染后台数据
- usage 筛选与事件列表展示

---

## 验证脚本

仓库里现在已经补充了一批无人工验证脚本，用于覆盖后台、运行时、账号、导出和 usage 行为。

例如：

- `scripts/verify_admin_session_auth_flow.py`
- `scripts/verify_usage_admin_endpoints.py`
- `scripts/verify_register_admin_protection.py`
- `scripts/verify_refresh_action_success.py`
- `scripts/verify_create_workspace_success.py`
- `scripts/verify_refresh_probe_success.py`
- `scripts/verify_workspace_probe_success.py`
- `scripts/verify_safe_accounts_view.py`
- `scripts/verify_admin_redaction_modes.py`

这些脚本的目的，是让关键后端行为不只依赖人工点页面验证。

---

## 说明

- 本地账号 JSON 文件应保持未提交状态
- 后台列表 / 导出默认使用 safe 视图
- raw 视图与 raw 导出必须显式触发，并带审计语义
- 默认后台凭证应在首次登录后立即轮换
- 项目仍支持聊天 API 使用，但后台运维已经成为一等功能
# Notion2API

> A Notion-based OpenAI-compatible API service with an admin operations console, account pool, runtime controls, usage reporting, and workspace/register automation.

🌐 English | [中文](./README_CN.md)

## What this project is now

Notion2API is no longer just a thin chat wrapper.

It now combines:

- an OpenAI-compatible API surface for chat clients
- a multi-account Notion account pool
- a browser-based admin console
- session-based admin authentication
- runtime configuration and proxy diagnostics
- usage summary and usage event queries
- refresh / probe / workspace operations
- register automation and hydration diagnostics

If you only expect a `/v1/chat/completions` proxy, the project has already grown beyond that scope.

---

## Core capabilities

### API layer

- OpenAI-compatible `/v1/chat/completions`
- lightweight `/v1/responses` compatibility endpoint
- streaming responses
- multimodal-compatible request parsing at the API layer
- model registry and compatibility handling

### Account pool and operations

- multi-account load balancing
- safe vs raw admin account views
- account export / import / replace flows
- per-account refresh, probe, workspace sync, workspace creation
- action logs and audit-oriented metadata

### Admin console

- browser-based admin workspace
- `admin session` login flow
- forced password rotation for default admin credentials
- overview / usage / accounts / runtime / diagnostics sections
- masked vs raw data exposure semantics

### Runtime and diagnostics

- runtime config editing
- proxy health inspection
- refresh diagnostics
- workspace diagnostics
- request template inspection
- auto-register status and queue visibility

### Usage reporting

- `/v1/admin/usage/summary`
- `/v1/admin/usage/events`
- filtering by time, model, account, and request type

---

## Admin security model

Admin routes are no longer protected by a reusable plain password header alone.

Current admin flow:

1. `POST /v1/admin/login` with username/password
2. receive a short-lived `admin session`
3. send `X-Admin-Session` on admin requests
4. if default credentials are still in use, sensitive admin actions are blocked until password rotation is completed

Important behavior:

- `/v1/admin/accounts/safe` returns masked account data
- `/v1/admin/accounts` and `/v1/admin/accounts/{account_id}` are explicit raw views
- `/v1/admin/accounts/export` is masked by default; `?raw=true` is explicit
- utility/status endpoints expose `response_mode`
- config/report/snapshot style endpoints expose `redaction_mode`

---

## Quick start

### 1. Prepare credentials

Open https://www.notion.so/ai and log in, then use DevTools to collect the required values.

Minimal account fields:

- `token_v2`
- `space_id`
- `user_id`

You can store accounts either:

- directly in `.env` with `NOTION_ACCOUNTS`
- or in a local JSON file with `NOTION_ACCOUNTS_FILE`

Example:

```bash
cp .env.example .env

NOTION_ACCOUNTS='[{"token_v2":"your_token","space_id":"your_space","user_id":"your_uid","space_view_id":"your_view","user_name":"your_name","user_email":"your_email"}]'
APP_MODE=standard
```

Or:

```bash
NOTION_ACCOUNTS_FILE=./accounts.local.json
APP_MODE=standard
```

See `accounts.local.json.example` for the expected file format.

### 2. Start the service

#### Docker

```bash
docker-compose up -d
```

#### Local

```bash
pip install -r requirements.txt
uvicorn app.server:app --host 0.0.0.0 --port 8000
```

Default local entry:

- app: `http://localhost:8000`
- models: `GET /v1/models`
- admin console: open the bundled frontend and sign in from the settings/admin area

---

## Main admin endpoints

### Auth

- `POST /v1/admin/login`
- `POST /v1/admin/change-password`

### Accounts

- `GET /v1/admin/accounts/safe`
- `GET /v1/admin/accounts`
- `GET /v1/admin/accounts/{account_id}`
- `PATCH /v1/admin/accounts/{account_id}`
- `DELETE /v1/admin/accounts/{account_id}`
- `GET /v1/admin/accounts/export`
- `POST /v1/admin/accounts/import`
- `POST /v1/admin/accounts/replace`

### Runtime / diagnostics

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

## Frontend admin console

The frontend now includes an operations-oriented admin workspace instead of only a basic settings view.

Current sections include:

- Overview
- Usage
- Accounts
- Runtime
- Diagnostics

The frontend also supports:

- admin login state restore within the current browser session
- forced default-password rotation guidance
- callback parsing for OAuth import flows
- masked/safe rendering for admin data by default
- usage filters and event list rendering

---

## Verification scripts

This repository now includes a larger set of non-manual verification scripts for admin, runtime, account, export, and usage behavior.

Examples:

- `scripts/verify_admin_session_auth_flow.py`
- `scripts/verify_usage_admin_endpoints.py`
- `scripts/verify_register_admin_protection.py`
- `scripts/verify_refresh_action_success.py`
- `scripts/verify_create_workspace_success.py`
- `scripts/verify_refresh_probe_success.py`
- `scripts/verify_workspace_probe_success.py`
- `scripts/verify_safe_accounts_view.py`
- `scripts/verify_admin_redaction_modes.py`

These scripts are intended to validate backend behavior without relying only on manual UI checks.

---

## Notes

- local account JSON files are meant to stay uncommitted
- safe views are the default for admin list/export flows
- raw views and raw exports are explicit and auditable
- default admin credentials should be rotated immediately after first login
- the project still supports chat usage, but operational administration is now a first-class part of the product

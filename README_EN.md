# Notion2API

> A Notion-based OpenAI-compatible API service that now includes an admin operations console, account pool, runtime controls, usage reporting, OAuth/register tooling, workspace operations, and multimodal chat support.

🌐 [中文](./README.md) | English

## What this project is now

Notion2API is no longer just a thin `/v1/chat/completions` wrapper.

It now combines:

- an OpenAI-compatible chat API
- a lightweight `/v1/responses` compatibility layer
- multimodal request parsing with image support
- a multi-account Notion account pool
- a browser-based admin operations console
- session-based admin authentication with password rotation
- runtime config editing and proxy diagnostics
- usage summary and event queries
- OAuth callback tooling and register automation
- workspace sync / probe / create operations
- a growing set of non-manual verification scripts

If you only expect a single upstream proxy, the current product shape is much closer to an operations-oriented control plane.

---

## Feature overview

### 1. OpenAI-compatible API surface

- `POST /v1/chat/completions`
- `POST /v1/responses`
- `GET /v1/models`
- streaming responses
- model registry and compatibility normalization
- request validation for OpenAI-style payloads

### 2. Multimodal and image input support

The API accepts both plain text and OpenAI-style multimodal content arrays.

Current supported user content types:

- `text`
- `image_url`

Supported image reference forms:

- normal `http://` or `https://` image URLs
- `data:image/...;base64,...` payloads

The frontend also includes image upload / drag-upload support and stores uploaded image references in the local chat flow.

### 3. Account pool and account operations

- multi-account load balancing
- account enable/disable state management
- safe vs raw account views for admin usage
- account export / import / replace flows
- per-account refresh, probe, workspace sync, and workspace creation actions
- audit-oriented action metadata for admin operations

### 4. Admin operations console

The bundled frontend is no longer only a simple settings popup. It now exposes an operations-oriented admin workspace with sections for:

- Overview
- Usage
- Accounts
- Runtime
- Diagnostics

Key admin UX behavior:

- browser-session admin login restore
- forced rotation for default admin credentials
- safe/masked rendering by default
- operational status cards and account health views
- usage filters and event list rendering
- callback parsing for OAuth import flows

### 5. Runtime controls and diagnostics

The backend exposes runtime settings and diagnostic surfaces for operators, including:

- runtime config editing
- proxy health inspection
- refresh diagnostics
- workspace diagnostics
- request template inspection
- auto-register status and queue visibility
- workspace create dry-run visibility

### 6. Usage reporting

Admin usage endpoints support both summary and event-level inspection:

- `GET /v1/admin/usage/summary`
- `GET /v1/admin/usage/events`

Current filters cover dimensions such as:

- time range
- model
- account
- request type

This allows the admin console to answer operational questions instead of only showing account health.

### 7. OAuth, register, and callback tooling

This project now includes a more complete operational flow around OAuth-style account import and register automation:

- OAuth start payload generation
- localhost-friendly callback bridge support
- callback parsing / finalize flows in the admin panel
- refresh-status and refresh-diagnostics views
- auto-register state visibility
- hydration retry and register protection logic

The admin surface is designed to help operators inspect whether accounts need refresh, reauthorization, hydration retry, or workspace repair before being reused.

### 8. Workspace operations

The project exposes workspace-focused operational flows instead of leaving them hidden inside account state:

- workspace sync
- workspace diagnostics
- workspace create status
- workspace creation request templates
- workspace create dry-run support
- probe-oriented verification scripts for refresh/workspace behavior

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
- config/report/snapshot-style endpoints expose `redaction_mode`

---

## API authentication model

Client-side API authentication and admin authentication are intentionally separate.

### Client API auth

For normal `/v1/...` client requests:

- if `API_KEY` is blank in `.env`, global Bearer validation is disabled
- if `API_KEY` is set, clients must send `Authorization: Bearer <your-key>`
- this allows deployments to run in either open local mode or protected custom-key mode

### Admin auth

For admin operations:

- use `POST /v1/admin/login`
- obtain an `admin session`
- send `X-Admin-Session` on subsequent admin requests

This keeps operator access separate from normal model/chat access.

---

## Quick start

### 1. Prepare Notion credentials

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
API_KEY=
```

Or:

```bash
NOTION_ACCOUNTS_FILE=./accounts.local.json
APP_MODE=standard
API_KEY=your-custom-key
```

See `accounts.local.json.example` for the expected file format.

### Recommended environment variables

The homepage README should call out the main env/config knobs explicitly. The most important ones are:

| Variable | Purpose |
| --- | --- |
| `NOTION_ACCOUNTS` | Inline JSON array for the Notion account pool |
| `NOTION_ACCOUNTS_FILE` | Local JSON file path for the account pool; takes precedence over `NOTION_ACCOUNTS` |
| `API_KEY` | Client Bearer key for `/v1/chat/completions` and related `/v1` access; leave blank to disable client auth |
| `APP_MODE` | App mode such as `standard`, `lite`, or `heavy` |
| `UPSTREAM_PROXY` / `UPSTREAM_HTTP_PROXY` / `UPSTREAM_HTTPS_PROXY` | Optional upstream proxy routing |
| `HOST` / `PORT` / `HOST_PORT` | Local binding and Docker-exposed ports |
| `ALLOWED_ORIGINS` | CORS allowlist |
| `DB_PATH` | SQLite path for persisted conversation data |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | Initial admin bootstrap credentials used for first login / rotation flow |
| `TEMP_MAIL_PROVIDER` / `TEMP_MAIL_BASE_URL` / `TEMP_MAIL_API_KEY` / `TEMP_MAIL_DOMAIN` | Auto-register mail provider settings |
| `REGISTER_HEADLESS` / `REGISTER_USE_API` | Register automation behavior |
| `SILICONFLOW_API_KEY` | Optional dependency for `heavy` mode |
| `LOG_LEVEL` / `TZ` | Logging and timezone settings |

If you need the full template, check `.env.example` directly.

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

### 3. Open the local entry points

- app: `http://localhost:8000`
- models: `GET /v1/models`
- admin console: open the bundled frontend and sign in from the settings/admin area

---

## Main endpoints

### Public / client-facing

- `GET /v1/models`
- `POST /v1/chat/completions`
- `POST /v1/responses`

### Admin auth

- `POST /v1/admin/login`
- `POST /v1/admin/change-password`

### Admin accounts

- `GET /v1/admin/accounts/safe`
- `GET /v1/admin/accounts`
- `GET /v1/admin/accounts/{account_id}`
- `PATCH /v1/admin/accounts/{account_id}`
- `DELETE /v1/admin/accounts/{account_id}`
- `GET /v1/admin/accounts/export`
- `POST /v1/admin/accounts/import`
- `POST /v1/admin/accounts/replace`

### Admin runtime / diagnostics

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

### Admin usage

- `GET /v1/admin/usage/summary`
- `GET /v1/admin/usage/events`

---

## Frontend admin console

The frontend is now closer to an operations dashboard than a plain chat settings sheet.

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
- workspace and runtime action surfaces
- image attachment handling in chat input

---

## Verification scripts

This repository now includes a larger set of non-manual verification scripts for admin, runtime, account, export, usage, refresh, and workspace behavior.

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
- `scripts/verify_direct_mode_ignores_warp_proxy.py`
- `scripts/verify_frontend_semantic_fields_backend_contract.py`

These scripts are intended to validate backend behavior without relying only on manual UI checks.

---

## Notes

- local account JSON files are meant to stay uncommitted
- safe views are the default for admin list/export flows
- raw views and raw exports are explicit and auditable
- default admin credentials should be rotated immediately after first login
- `API_KEY` can be left blank for local/open deployments or set to a custom value for Bearer protection
- workspace create currently exposes dry-run and diagnostic-oriented operator tooling
- the project still supports chat usage, but operational administration is now a first-class part of the product

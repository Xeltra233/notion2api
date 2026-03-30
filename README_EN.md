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
- session-based admin authentication for the admin console
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
- server-cached `/v1/media/...` links or links generated from a custom public media base URL

The frontend also includes image upload / drag-upload support. Newly uploaded images are pushed into the server-side media cache first, then the returned media link is stored in chat content; older `data:` URI history remains compatible.

### 3. Account pool and account operations

- multi-account load balancing
- account enable/disable state management
- safe vs raw account views for admin usage
- account export / import / replace flows
- per-account refresh, probe, workspace sync, and workspace creation actions
- audit-oriented action metadata for admin operations

### 4. Admin operations console

The bundled frontend now shows the admin login screen first; after login, it opens the admin workspace.

Main modules after login:

- Overview
- Accounts
- Runtime
- Diagnostics
- Usage
- Chat

Key admin UX behavior:

- browser-session admin login restore
- admin login state restore and credential update entry points
- safe/masked rendering by default
- operational status cards and account health views
- usage filters and event list rendering
- callback parsing for OAuth import flows
- a dedicated Chat module instead of chat being the default landing page

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
4. continue using the admin console with the current admin account

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

- if `API_KEY` is blank in Runtime, global Bearer validation is disabled
- if `API_KEY` is set, clients must send `Authorization: Bearer <your-key>`
- this allows deployments to run in either open local mode or enabling a custom API key from the Runtime panel

### Admin auth

For admin operations:

- use `POST /v1/admin/login`
- obtain an `admin session`
- send `X-Admin-Session` on subsequent admin requests

### Chat access auth

For the Chat module and chat-facing APIs:

- chat can stay open
- or you can enable a separate Chat password in Runtime
- once enabled, the frontend Chat module requires an extra chat access login
- an already-authenticated admin session can bypass this for operator troubleshooting

This keeps operator access separate from normal model/chat access, and also separates the admin password from the chat access password.

---

## Quick start

### 1. Prepare the minimum startup configuration

If your deployment flow is to bring up the admin console first and import accounts later via OAuth or the register flow, you only need to care about the admin password and the port.

Minimum startup example:

```bash
cp .env.example .env

ADMIN_PASSWORD=change-me-now
HOST=0.0.0.0
PORT=8000
```

The default admin login is:

- username: `admin`
- password: the `ADMIN_PASSWORD` you configured

The account pool is no longer treated as a default startup prerequisite. You can bring the service up first and then add accounts through the admin OAuth / register / import flows.

### Minimum env surface

By default, you should only need a very small set of env values:

| Variable | Purpose |
| --- | --- |
| `ADMIN_PASSWORD` | Password for the admin login |
| `HOST` / `PORT` / `HOST_PORT` | Only change these when you need custom bind or Docker-exposed ports |

If you already have accounts ready, `NOTION_ACCOUNTS_FILE` and `NOTION_ACCOUNTS` are still supported as compatibility paths, but the preferred product flow is to start the admin console first and finish the rest from **Admin > Runtime / Accounts**.

### Advanced configuration

The following settings are still supported for compatibility, but they are no longer the recommended default starting path:

- `APP_MODE`
- `UPSTREAM_PROXY` / `UPSTREAM_HTTP_PROXY` / `UPSTREAM_HTTPS_PROXY`
- `ALLOWED_ORIGINS`
- `TEMP_MAIL_*`
- `REGISTER_*`
- `SILICONFLOW_API_KEY`
- refresh / workspace advanced settings

For the complete template, check `.env.example`. Some settings can already be edited from the Runtime panel, but startup-time settings such as `ALLOWED_ORIGINS` should still be treated as requiring a restart to fully apply.

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
- admin console: open the root path `/` to see the admin login screen first
- Chat module: enter it after admin sign-in; if Chat password is enabled, complete chat access login as well

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

The frontend now shows the admin login screen first instead of opening on chat.

Main modules after login:

- Login
- Overview
- Accounts
- Runtime
- Diagnostics
- Usage
- Chat

The frontend also supports:

- admin login state restore within the current browser session
- admin credential update entry points
- callback parsing for OAuth import flows
- masked/safe rendering for admin data by default
- usage filters and event list rendering
- workspace and runtime action surfaces
- separate access control for the Chat module
- image attachment handling in chat input with server-cached media links

---

## Verification scripts

This repository now includes a larger set of non-manual verification scripts for admin, runtime, account, export, usage, refresh, and workspace behavior.

They can be grouped roughly by responsibility:

### Auth / sessions / security boundaries

- `scripts/verify_admin_session_auth_flow.py`
- `scripts/verify_register_admin_protection.py`
- `scripts/verify_admin_redaction_modes.py`
- `scripts/verify_safe_accounts_view.py`
- `scripts/verify_usage_admin_endpoints.py`

### Admin shell and frontend/backend contracts

- `scripts/verify_frontend_semantic_fields_backend_contract.py`
- `scripts/verify_admin_first_entry_shell.py`
- `scripts/verify_admin_first_default_module_contract.py`
- `scripts/verify_workspace_footer_actions_contract.py`
- `scripts/verify_direct_mode_ignores_warp_proxy.py`

### Chat / access gates / media

- `scripts/verify_chat_access_flow.py`
- `scripts/verify_chat_session_module_access_contract.py`
- `scripts/verify_admin_logout_chat_gate_contract.py`
- `scripts/verify_runtime_chat_session_reset_contract.py`
- `scripts/verify_chat_access_refresh_session_cleanup_contract.py`
- `scripts/verify_media_upload_flow.py`

### Refresh / workspace / probe flows

- `scripts/verify_refresh_action_success.py`
- `scripts/verify_create_workspace_success.py`
- `scripts/verify_refresh_probe_success.py`
- `scripts/verify_workspace_probe_success.py`

These scripts are intended to validate backend behavior without relying only on manual UI checks.

---

## Notes

- local account JSON files are meant to stay uncommitted
- safe views are the default for admin list/export flows
- raw views and raw exports are explicit and auditable
- `ADMIN_PASSWORD` is used as the admin login password and can also be changed later in the admin console
- `API_KEY` can be left blank for local/open deployments or set to a custom value for Bearer protection
- Chat password is a separate Runtime setting and is not the same thing as the admin password
- `media_public_base_url` controls the public-facing base used when generating media links; if left blank, the service address is used
- `media_storage_path` should usually point at a persistent volume so cached uploads survive restarts
- if you deploy on platforms such as Zeabur, it is safer to mount media cache and runtime data onto the same persistent volume
- workspace create currently exposes dry-run and diagnostic-oriented operator tooling
- the project still supports chat usage, but operational administration is now a first-class part of the product

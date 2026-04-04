# AGENTS.md

## Repo notes
- This project is a FastAPI service with an admin-first frontend under `frontend/` and backend routes under `app/api/`.
- Production auto-deploys on every push to the repository; the user said Zeabur deployment usually takes about 2 minutes.
- Admin username defaults to `admin`; password comes from `ADMIN_PASSWORD`.

## Useful local run commands
- Install deps: `python -m pip install -r requirements.txt`
- Run locally: `ADMIN_PASSWORD=test-admin-password python -m uvicorn app.server:app --host 0.0.0.0 --port 8011`
- Health check: `curl http://127.0.0.1:8011/health`

## Browser verification notes
- The user prefers real browser-based validation over pure script/command verification.
- A real production browser session exposed a frontend bug where zero-valued summary pills rendered as blank text.

## Implementation learnings
- `frontend/js/api/settings.js`: use `value ?? ''` instead of `value || ''` in HTML escaping so numeric zero renders correctly.
- `app/api/chat.py`: avoid `imghdr`; Python 3.13 removes it. A small signature-based detector for PNG/JPEG/GIF/WEBP keeps media upload detection working and lets the app start in this environment.
- Browser-testing also exposed that account-level request template previews could leak real account/user/workspace identifiers even while the admin UI claimed `safe` view mode. Backend-side recursive redaction in `app/api/admin.py` should be applied before returning template preview payloads.
- Safe-mode inconsistencies can also appear in `admin/snapshot` and `admin/report`: recent action/probe/operation logs may still expose raw account/workspace identifiers unless those log payloads are recursively redacted before returning them to the frontend.
- The diagnostics `导出完整报告` flow has stricter redaction needs than the normal account cards: the exported safe report can still leak raw `id`/`user_id`/`space_id` and nested probe or health metadata unless report-only account redaction is applied before serializing the textarea JSON.
- The diagnostics side panels for `刷新诊断` and `工作区诊断` also write full JSON into the shared textarea, so their safe responses must mask `account_id` and `user_id` even if the visible summary cards only show email/readiness fields.
- Safe-mode `alerts` sections in `admin/report` and the `导出账号` JSON export can leak raw account/workspace IDs if they reuse the normal account-card payload shape; export/report-only paths should use stricter report redaction than the interactive account list.
- The diagnostics tool button `工作区创建状态` uses `/v1/admin/accounts/workspaces/status` and writes a safe-summary JSON blob into the shared textarea; mask `account_id`, `user_id`, and `space_id` there too, not just in the newer diagnostics endpoints.
- Account-card `刷新探测` and `工作区探测` actions also dump their full response JSON into the shared textarea. Even for dry-run probes, mask top-level `account_id` and recursively redact nested request-template identifiers, especially any `*_id` fields plus transaction `actor_id`, before returning to the browser.
- `GET /v1/admin/accounts/{account_id}` 不应默认返回原始账号明细；更安全的做法是默认返回脱敏 `safe_detail`，只有编辑器这类显式后台操作再附带 `?raw=true` 请求原始字段。
- `GET /v1/admin/accounts/safe` 虽然名字叫 safe，但如果只套用普通账号脱敏仍会泄漏 `user_id`、`space_id`、`pending-signup-*` 等标识；这个接口应直接复用更严格的报告级账号脱敏。
- `GET /v1/admin/config` 也会返回账号列表和健康明细，不能只做普通 token/session 脱敏；这里同样要按报告级别掩码 `user_id`、`space_id`、`pending-signup-*` 以及健康项中的 `account_id` 等字段。
- `GET /v1/admin/accounts` 不能默认当成内部原始接口暴露；即使前端主流程当前走 `/admin/accounts/safe`，这个主列表接口本身也应默认返回安全视图，只把 `?raw=true` 留给显式后台调试用途。












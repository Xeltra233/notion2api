## [LRN-20260401-001] best_practice

**Logged**: 2026-04-01T00:00:00Z
**Priority**: high
**Status**: pending
**Area**: backend

### Summary
Do not treat an intended fix as complete until the target file is re-read and the final code path is verified.

### Details
During this maintenance pass, one admin-side issue was believed to be fixed based on a prior patch plan, but a later file read showed the actual runtime path in `app/api/admin.py` still used the old proxy selection logic for email-login browser flow. The project already contains several similar configuration-sensitive paths, so relying on patch intent or partial diff memory is not enough. Final verification must include reading the updated function and, when practical, running a minimal behavior check.

### Suggested Action
After each non-trivial fix, re-read the exact target function and verify the expected symbol or branch is present before claiming the issue is resolved.

### Metadata
- Source: conversation
- Related Files: app/api/admin.py
- Tags: verification, maintenance, regression-prevention

---

## [LRN-20260401-008] best_practice

**Logged**: 2026-04-01T00:00:00Z
**Priority**: low
**Status**: pending
**Area**: testing

### Summary
For proxy-mode regressions, verify the constructed service configuration directly before attempting browser automation.

### Details
Email-login proxy behavior depends on `_build_email_login_register_service()` selecting the right proxy for the current runtime mode. Testing that through the full browser-verification flow would add unnecessary external and browser dependencies. A smaller and more reliable regression check is to assert the service is constructed with the expected proxy for warp, socks5, and http modes.

### Suggested Action
Prefer constructor-level verification for proxy selection logic, and reserve browser automation for user-facing flow coverage.

### Metadata
- Source: conversation
- Related Files: scripts/verify_email_login_proxy_selection.py, app/api/admin.py, app/api/register.py
- Tags: testing, proxy, isolation

---

## [LRN-20260401-007] best_practice

**Logged**: 2026-04-01T00:00:00Z
**Priority**: medium
**Status**: pending
**Area**: testing

### Summary
When verifying auth/session invalidation, choose a gate-protected endpoint that does not depend on upstream provider health.

### Details
Using `/v1/chat/completions` to verify chat-session invalidation introduced flaky failures because upstream Notion account state could return 5xx/401 unrelated to the gate itself. Switching the test to a local gate-protected endpoint (`DELETE /v1/conversations/{id}` in non-heavy mode) isolated the intended contract: valid session reaches the mode-specific 400, invalid session is rejected at the auth gate with 401.

### Suggested Action
For auth and session tests, prefer local endpoints whose success/failure is determined before any external network dependency.

### Metadata
- Source: conversation
- Related Files: scripts/verify_chat_session_reset_on_password_change.py, app/api/chat.py
- Tags: testing, auth, isolation, regression

---

## [LRN-20260401-006] best_practice

**Logged**: 2026-04-01T00:00:00Z
**Priority**: medium
**Status**: pending
**Area**: testing

### Summary
Browser tests that mutate admin credentials must reset the test server state before each run, or the next run will fail for the wrong reason.

### Details
The admin must-change-password browser test changes the active admin password as part of the flow. Re-running the browser script against the same live dev server without resetting credentials caused false failures that looked like frontend state bugs but were actually stale credentials. For stateful admin flows, test setup must explicitly restore the starting password and restart or isolate the server process.

### Suggested Action
Before any browser test that changes auth state, reset admin credentials and restart the dedicated test server on a fixed port.

### Metadata
- Source: conversation
- Related Files: scripts/playwright_verify_admin_must_change_password.js, scripts/verify_admin_must_change_password_flow.py
- Tags: browser-test, stateful-tests, auth

---

## [LRN-20260401-005] best_practice

**Logged**: 2026-04-01T00:00:00Z
**Priority**: medium
**Status**: pending
**Area**: docs

### Summary
Compatibility work is not finished when validation is added; the public README must describe the exact supported subset.

### Details
This project exposes OpenAI, Responses, Anthropic, and Gemini-compatible endpoints. Several earlier fixes tightened unsupported fields at runtime, but without updating README the external contract would still look broader than the real implementation. For compatibility layers, documentation drift is almost equivalent to an API bug because client integrators rely on capability claims when selecting SDK options.

### Suggested Action
Whenever a compatibility endpoint explicitly rejects or narrows part of an upstream schema, update the README examples and support matrix in the same maintenance pass.

### Metadata
- Source: conversation
- Related Files: README.md, app/api/chat.py
- Tags: docs, compatibility, contract

---

## [LRN-20260401-004] best_practice

**Logged**: 2026-04-01T00:00:00Z
**Priority**: high
**Status**: pending
**Area**: backend

### Summary
When compatibility schemas expose fields for a feature that is not truly implemented, fail explicitly instead of pretending partial support.

### Details
This project accepted OpenAI-style `tools` and `tool_choice` fields in chat/responses schemas, but only had meaningful handling for search-style tools. Leaving general tool-calling silently accepted would mislead clients into assuming broader compatibility and create harder-to-debug downstream behavior. Rejecting unsupported variants at the request boundary is safer than false compatibility.

### Suggested Action
For every compatibility surface, either implement the advertised capability end-to-end or reject unsupported variants with a clear 4xx error.

### Metadata
- Source: conversation
- Related Files: app/api/chat.py, app/schemas.py
- Tags: compatibility, api-contract, validation

---

## [LRN-20260401-003] best_practice

**Logged**: 2026-04-01T00:00:00Z
**Priority**: high
**Status**: pending
**Area**: backend

### Summary
In account pools that support workspace expansion or cloned views, `user_id` alone is not a safe persistence key.

### Details
This project can represent multiple active client views for the same logical Notion user across different `space_id` or `space_view_id` values. Metadata sync originally updated the first persisted account matching `user_id`, which risks writing workspace and session state onto the wrong account. Stable matching must prefer persisted `account_id`, then narrower composite keys like `user_id + space_id` or `user_id + space_view_id`, and only use bare `user_id` as a last resort.

### Suggested Action
Whenever account state is synchronized back into persisted config, match on the narrowest stable identity available before falling back to broader identifiers.

### Metadata
- Source: conversation
- Related Files: app/account_pool.py, app/notion_client.py
- Tags: account-identity, workspace, persistence, diagnostics

---

## [LRN-20260401-002] best_practice

**Logged**: 2026-04-01T00:00:00Z
**Priority**: high
**Status**: pending
**Area**: backend

### Summary
Any runtime setting exposed in admin UI must be wired into the actual scheduling or authorization gate, not just persisted and displayed.

### Details
This project exposed `auto_register_interval_seconds`, `auto_register_idle_only`, and `must_change_password` in runtime/admin responses, but parts of the backend were not using them in the execution path. That creates a dangerous mismatch: operators believe the system enforces a policy because the config exists in the UI, while the runtime still behaves according to older defaults.

### Suggested Action
For every new runtime setting, verify three links before considering it complete: persistence, API serialization, and runtime decision path.

### Metadata
- Source: conversation
- Related Files: app/api/register.py, app/api/admin.py, app/server.py
- Tags: config-drift, admin, scheduling, auth

---

# AGENTS.md

## Repo notes
- This project is a FastAPI service with an admin-first frontend under `frontend/` and backend routes under `app/api/`.
- Production auto-deploys on every push to the repository; the user said Zeabur deployment usually takes about 2 minutes.
- Admin username defaults to `admin`; password comes from `ADMIN_PASSWORD`.

## Useful local run commands
- Install deps: `python -m pip install -r requirements.txt`
- Run locally: `ADMIN_PASSWORD=test-admin-password python -m uvicorn app.server:app --host 0.0.0.0 --port 8011`
- Health check: `curl http://127.0.0.1:8011/health`
- Run unit tests: `python -m unittest discover -s tests -p 'test_*.py' -v`

## Browser verification notes
- The user prefers real browser-based validation over pure script/command verification.
- A real production browser session exposed a frontend bug where zero-valued summary pills rendered as blank text.
- On the production admin overview, browser automation can mis-target densely packed filter buttons; unique alert button labels added in commit `11f9b31`, and direct button handler binding added in commit `8e954ac` helped distinguish real app behavior from automation click offset.

- 当前浏览器自动化里，`browser_get_content` 对登录态后台页面的提取可能滞后于真实 DOM；验证账号区快捷筛选是否真正生效时，要更信任 `browser_get_state` 中的统计变化（例如总数 10 → 8 → 2）与页面 notice/banner，而不是只看内容提取结果。
- 本地账号/总览筛选链路里，账号区三枚快捷筛选按钮（缺少工作区 / 工作区待处理 / 探测失败）最好保留 inline `onclick` 兜底；仅靠集中绑定时，在真实浏览器和自动化里都出现过点击命中但未可靠触发筛选的问题。
- `frontend/js/api/settings.js` 的 `applyQuickFilter('probe_failures')` 以前只刷新页面、不写入真实筛选条件；如果以后再改账号筛选链路，记得同时维护前端状态下拉、后端 `state=probe_failures` 过滤和 banner 文案三处。
- 后台 `probe_failures` 的真实口径不要再用“任何 `last_probe_ok=false` 都算失败”；`app/api/admin.py` 里应统一走 `_has_probe_failure(...)`，排除 dry-run / live-template / blocked / unsupported 等未真正发起上游探测的结果，并让列表过滤、summary、alerts 共用同一 helper。
- `frontend/js/api/settings.js` 的账号区空结果态不能吞掉 `当前筛选 / 来源 / 视图模式` banner；真实浏览器里筛选到 0 条时，仍应保留 banner，并把文案写成“当前筛选条件下没有匹配账号”。
- 账号筛选只应影响账号区本地摘要，不应污染总览/当前配置里的全局数字；前端 `refreshAdminPanel()` 如需在筛选状态下刷新全局统计，应额外拉一次未筛选的 accounts summary。
- 账号卡的 badge 与 tag 容易在 pending 场景重复（如 `hydration:pending` 同时出现在 badge 和 tag）；前端要对 badge/tag 做去重，但如果去重后 tag 为空，仍应回退显示 `account.source`（例如 `register_flow`），避免来源信息丢失。


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
- 安全账号列表如果把 `id` 一并脱敏，前端不能再把 `account.id` 当作动作主键；更稳妥的做法是前端仅持有已展示的公开引用（如 `user_email`），后端统一把这个引用解析回真实账号，再执行编辑/探测/批量动作。
- 只要前端把邮箱这类公开引用放进按账号路径的 URL（如 `/admin/accounts/{account_ref}/...`），就必须统一做 `encodeURIComponent(account_ref)`；否则真实浏览器里某些按钮会出现接口手工调用正常、但页面点击却报 `Account not found` 的不一致。
- 后台账号卡里不止 `模板 / 刷新探测 / 工作区探测` 需要把结果写回共享文本框；`同步工作区 / 补全重试 / 创建工作区` 这类按钮也要把脱敏后的结果写回，并给出明确 notice，否则真实浏览器里会出现动作接口实际成功、但用户看不到新反馈的假象。
- 那些会把执行结果写回页面文本框或批量结果面板的动作接口，也要默认返回安全脱敏后的 `result/results`，否则即使列表安全，按钮点击后仍会把原始 `account_id/space_id/workspace ids` 再次泄漏到浏览器。

- 生产真实浏览器回归中，诊断区固定按钮 `工作区诊断` 与 `请求模板 / 查看通用模板` 已确认会把脱敏 JSON 正常写入共享文本框，并显示可见 notice；可直接用它们验证“共享文本框/JSON 展示路径未因脱敏失效”。
- 同一轮生产浏览器中，`工作区创建状态` 对应接口 `/v1/admin/workspaces/create-status` 返回正常且为安全摘要，但按钮点击偶发未在页面形成新的可见更新；排查这类问题时要区分“接口健康”与“真实浏览器点击稳定命中”两个层面。
- 在当前浏览器自动化环境里，诊断区与动作日志里所有“复制 … JSON”按钮都依赖 `navigator.clipboard.writeText(...)`；若真实浏览器里出现“复制失败”，要先考虑自动化/权限限制，不能直接判定为前端业务逻辑坏。
- 生产 `519cdf5` 后，`工作区创建状态` 至少已在真实浏览器中确认能触发可见 notice；但与相邻按钮一样，当前自动化仍可能存在命中串位或文本框可见切换不稳定，需要结合页面 notice 与其他稳定入口交叉验证。
- `bc2e40f` 之后，诊断/模板/快照/报告按钮命中失效后台会话时，真实浏览器现在会正确退回登录态并锁定后台模块；不再出现“顶部仍显示后台已登录，但按钮一按就 Invalid admin session”的失真状态。
- 生产真实浏览器里，最近动作日志的 `查看 JSON` 已确认可正常展开，按钮会切换成 `隐藏 JSON`；展开后的 `refresh_probe` 与 `create_workspace` JSON 里，顶层和深层 `account_id`、`user_id`、`space_id`、`request_id`、`actor_id`、`workspaceId` 等字段持续保持 `********` 脱敏。
- 总览告警卡里 `缺少工作区` / `工作区待处理` 的示例行现在能直接显示 `workspace_creation_pending`，不再只是空数组 `[]`，更利于真实浏览器里判断当前状态口径。














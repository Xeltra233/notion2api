from typing import Any
import ipaddress
import secrets
import socket
import time
from urllib.parse import urlencode
from urllib.parse import urlparse
from urllib.parse import urlunparse

import requests
from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from app.account_pool import AccountPool
from app.api.register import (
    get_register_automation_state,
    get_register_automation_snapshot,
    list_due_pending_hydration_account_ids,
    maybe_start_auto_register,
    retry_pending_register_hydration,
)
from app.register.mail_client import build_runtime_proxy_dict
from app.usage import UsageStore
from app.config import (
    ACCOUNTS_PATH,
    CONFIG_PATH,
    get_accounts,
    get_admin_auth,
    get_app_mode,
    get_chat_auth,
    get_chat_session_ttl_seconds,
    get_config_store,
    should_auto_select_workspace,
    update_admin_credentials,
    update_chat_password,
    validate_runtime_request_url,
    verify_admin_credentials,
    verify_chat_password,
)


router = APIRouter(tags=["admin"])


_SECRET_MASK = "********"
_RUNTIME_SECRET_FIELDS = {
    "api_key",
    "siliconflow_api_key",
    "auto_register_mail_api_key",
    "refresh_client_secret",
}
_ACCOUNT_SECRET_FIELDS = {"token_v2"}
_OAUTH_SECRET_FIELDS = {"access_token", "refresh_token"}
_OAUTH_CALLBACK_TTL_SECONDS = 10 * 60


def _mask_secret(value: Any) -> str:
    return _SECRET_MASK if str(value or "").strip() else ""


def _redact_runtime_settings(settings: dict[str, Any]) -> dict[str, Any]:
    redacted = dict(settings)
    for field in _RUNTIME_SECRET_FIELDS:
        raw_value = redacted.get(field, "")
        redacted[field] = _mask_secret(raw_value)
        redacted[f"has_{field}"] = bool(str(raw_value or "").strip())
    if "chat_password" in redacted:
        raw_chat_password = redacted.get("chat_password", "")
        redacted["chat_password"] = _mask_secret(raw_chat_password)
        redacted["has_chat_password"] = bool(str(raw_chat_password or "").strip())
    return redacted


def _redact_oauth_payload(oauth: dict[str, Any]) -> dict[str, Any]:
    redacted = dict(oauth)
    for field in _OAUTH_SECRET_FIELDS:
        raw_value = redacted.get(field, "")
        redacted[field] = _mask_secret(raw_value)
        redacted[f"has_{field}"] = bool(str(raw_value or "").strip())
    return redacted


def _redact_account_payload(account: dict[str, Any]) -> dict[str, Any]:
    redacted = dict(account)
    for field in _ACCOUNT_SECRET_FIELDS:
        raw_value = redacted.get(field, "")
        redacted[field] = _mask_secret(raw_value)
        redacted[f"has_{field}"] = bool(str(raw_value or "").strip())
    oauth = redacted.get("oauth") if isinstance(redacted.get("oauth"), dict) else {}
    redacted["oauth"] = _redact_oauth_payload(oauth)
    health = (
        redacted.get("health") if isinstance(redacted.get("health"), dict) else None
    )
    if health is not None:
        redacted["health"] = _redact_health_payload(health)
    return redacted


def _redact_health_payload(health: dict[str, Any]) -> dict[str, Any]:
    redacted = dict(health)
    oauth = redacted.get("oauth") if isinstance(redacted.get("oauth"), dict) else {}
    redacted["oauth"] = _redact_oauth_payload(oauth)
    return redacted


def _redact_account_list(accounts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [_redact_account_payload(account) for account in accounts]


def _build_proxy_health_summary(config: dict[str, Any]) -> dict[str, Any]:
    settings = {
        "upstream_proxy": config.get("upstream_proxy", ""),
        "upstream_http_proxy": config.get("upstream_http_proxy", ""),
        "upstream_https_proxy": config.get("upstream_https_proxy", ""),
        "upstream_socks5_proxy": config.get("upstream_socks5_proxy", ""),
        "upstream_proxy_mode": config.get("upstream_proxy_mode", "direct"),
        "upstream_warp_enabled": config.get("upstream_warp_enabled", False),
        "upstream_warp_proxy": config.get("upstream_warp_proxy", ""),
    }
    proxies = build_runtime_proxy_dict()
    return {
        "mode": settings["upstream_proxy_mode"],
        "warp_enabled": bool(settings["upstream_warp_enabled"]),
        "warp_configured": bool(str(settings["upstream_warp_proxy"] or "").strip()),
        "socks5_configured": bool(str(settings["upstream_socks5_proxy"] or "").strip()),
        "http_configured": bool(str(settings["upstream_http_proxy"] or "").strip()),
        "https_configured": bool(str(settings["upstream_https_proxy"] or "").strip()),
        "active": bool(proxies),
        "resolved": proxies or {},
        "operator_state": (
            "degraded"
            if settings["upstream_proxy_mode"] != "direct" and not proxies
            else ("ready" if proxies else "direct")
        ),
        "hint": (
            "Warp mode is selected but the local warp proxy is not configured yet."
            if settings["upstream_proxy_mode"] == "warp"
            and not str(settings["upstream_warp_proxy"] or "").strip()
            else (
                "Proxy mode is direct; requests bypass any configured proxy endpoints."
                if settings["upstream_proxy_mode"] == "direct"
                else "Runtime proxy settings are configured and ready for health checks."
            )
        ),
    }


def _build_runtime_operations_panel(
    proxy_health_payload: dict[str, Any], automation: dict[str, Any]
) -> dict[str, Any]:
    summary = (
        proxy_health_payload.get("summary")
        if isinstance(proxy_health_payload.get("summary"), dict)
        else {}
    )
    checks = (
        proxy_health_payload.get("checks")
        if isinstance(proxy_health_payload.get("checks"), dict)
        else {}
    )
    current_reason = (
        str(
            automation.get("current_reason")
            or automation.get("last_decision_reason")
            or "unknown"
        )
        .strip()
        .lower()
    )
    blocking_due = int(automation.get("pending_hydration_due") or 0)
    pending_total = int(automation.get("pending_hydration_total") or 0)
    pending_due_reauthorize = int(
        automation.get("pending_hydration_due_reauthorize") or 0
    )
    pending_due_transient = int(automation.get("pending_hydration_due_transient") or 0)
    pending_due_config = int(automation.get("pending_hydration_due_config") or 0)
    pending_due_unknown = int(automation.get("pending_hydration_due_unknown") or 0)
    pending_primary_focus = (
        str(automation.get("pending_hydration_primary_focus") or "").strip().lower()
    )
    eligible = bool(automation.get("eligible", False))
    available_targets = [
        label
        for label, payload in checks.items()
        if isinstance(payload, dict)
        and bool(payload.get("configured"))
        and bool(payload.get("reachable"))
    ]
    headline = "Runtime ready for controlled auto-register runs."
    operator_focus = "ready_to_register"
    if current_reason == "proxy_unconfigured":
        headline = (
            "Proxy mode requires more configuration before auto-register can run."
        )
        operator_focus = "proxy_blocked"
    elif current_reason == "proxy_unreachable":
        headline = (
            "Configured proxy endpoint is unreachable, so auto-register stays paused."
        )
        operator_focus = "proxy_blocked"
    elif current_reason == "pending_hydration_due":
        headline = "Pending hydration retries are due now; resolve them before creating more accounts."
        operator_focus = pending_primary_focus or "pending_due"
    elif current_reason == "busy_cooldown_active":
        headline = "Recent refresh or hydration activity is protecting the next auto-register window."
        operator_focus = "cooldown"
    elif blocking_due > 0:
        headline = "Pending hydration retries are due now; stabilize them before adding more accounts."
        operator_focus = pending_primary_focus or "pending_due"
    elif pending_total > 0:
        headline = "Pending hydration accounts are still cooling down before their next retry window."
        operator_focus = "pending_waiting"
    elif not eligible:
        headline = "Auto-register is currently paused by runtime guardrails."
        operator_focus = "guarded"
    recommended_action = (
        "Monitor proxy health and use auto-trigger only during a known idle window."
    )
    if current_reason == "proxy_unconfigured":
        recommended_action = "Switch to direct mode or fill the selected proxy endpoint before triggering auto-register."
    elif current_reason == "proxy_unreachable":
        recommended_action = (
            "Start the local proxy or Warp listener, then re-run 'Check proxy'."
        )
    elif current_reason == "busy_cooldown_active":
        recommended_action = "Let hydration or workspace actions settle before the next registration batch."
    elif blocking_due > 0:
        recommended_action = "Run pending hydration retry first so background workers do not compete with fresh signups."
    elif pending_total > 0:
        recommended_action = "Wait for the pending hydration retry window, then re-run hydration before opening fresh registrations."
    if operator_focus == "pending_reauth_due":
        headline = "Pending hydration is blocked by expired authorization; reauthorize those accounts before new signups."
        recommended_action = "Reauthorize the due accounts first, then run hydration retry after credentials are valid again."
    elif operator_focus == "pending_transient_due":
        headline = "Pending hydration is blocked by transport or upstream instability; clear connectivity before new signups."
        recommended_action = "Inspect proxy or network reachability, then rerun hydration retry for the due accounts."
    elif operator_focus == "pending_config_due":
        headline = "Pending hydration is blocked by runtime config or resource mismatches; inspect the failed workspace targets first."
        recommended_action = "Review workspace endpoint or resource configuration, then retry hydration after correcting it."
    elif operator_focus == "pending_mixed_due":
        headline = "Pending hydration has mixed blockers; split reauthorization and transport issues before new signups."
        recommended_action = "Handle reauthorization blockers first, then address transient connectivity failures before retrying hydration."
    return {
        "headline": headline,
        "recommended_action": recommended_action,
        "operator_focus": operator_focus,
        "proxy_mode": summary.get("mode", "direct"),
        "proxy_operator_state": summary.get("operator_state", "direct"),
        "reachable_proxy_targets": available_targets,
        "current_reason": current_reason,
        "gate_reason": str(automation.get("gate_reason") or current_reason),
        "latest_task_status": str(automation.get("latest_task_status") or ""),
        "eligible": eligible,
        "pending_hydration_due": blocking_due,
        "pending_hydration_total": int(automation.get("pending_hydration_total") or 0),
        "pending_hydration_due_reauthorize": pending_due_reauthorize,
        "pending_hydration_due_transient": pending_due_transient,
        "pending_hydration_due_config": pending_due_config,
        "pending_hydration_due_unknown": pending_due_unknown,
        "spacing_remaining_seconds": int(
            automation.get("spacing_remaining_seconds") or 0
        ),
        "busy_cooldown_remaining_seconds": int(
            automation.get("busy_cooldown_remaining_seconds") or 0
        ),
        "next_eligible_at": int(automation.get("next_eligible_at") or 0),
    }


def _summarize_pending_hydration_from_account_view(
    account_view: dict[str, Any] | list[Any] | None,
    action_history: list[dict[str, Any]] | None = None,
) -> dict[str, int | str]:
    latest_action_summary_by_account = _build_pending_hydration_action_history_index(
        action_history
    )
    if isinstance(account_view, dict):
        summary = account_view.get("summary")
        if isinstance(summary, dict):
            pending_due = int(summary.get("workspace_hydration_due") or 0)
            return {
                "pending_total": int(summary.get("workspace_creation_pending") or 0),
                "pending_due": pending_due,
                "pending_due_reauthorize": 0,
                "pending_due_transient": 0,
                "pending_due_config": 0,
                "pending_due_unknown": pending_due,
                "primary_due_focus": (
                    "pending_mixed_due" if pending_due > 0 else "pending_waiting"
                ),
            }
        accounts = account_view.get("accounts")
        if isinstance(accounts, list):
            account_view = accounts
    if isinstance(account_view, list):
        effective_now = int(time.time())
        pending_total = 0
        pending_due = 0
        pending_due_reauthorize = 0
        pending_due_transient = 0
        pending_due_config = 0
        pending_due_unknown = 0
        for item in account_view:
            if not isinstance(item, dict):
                continue
            account_id = str(item.get("id") or "").strip()
            status = item.get("status") if isinstance(item.get("status"), dict) else {}
            workspace = (
                item.get("workspace") if isinstance(item.get("workspace"), dict) else {}
            )
            workspace_state = str(
                status.get("effective_state")
                or status.get("workspace_state")
                or workspace.get("state")
                or ""
            ).strip()
            if workspace_state != "workspace_creation_pending":
                continue
            pending_total += 1
            retry_after = int(status.get("workspace_hydration_retry_after") or 0)
            if retry_after <= 0 or retry_after <= effective_now:
                pending_due += 1
                retry_policy, failure_category = (
                    _resolve_pending_hydration_classification(
                        account_id,
                        status,
                        latest_action_summary_by_account,
                    )
                )
                if (
                    retry_policy == "reauthorize_or_permission_review"
                    or failure_category
                    in {
                        "unauthorized",
                        "forbidden",
                    }
                ):
                    pending_due_reauthorize += 1
                elif (
                    retry_policy == "upstream_transient_failure"
                    or failure_category
                    in {
                        "network_error",
                        "timeout",
                        "server_error",
                        "rate_limited",
                    }
                ):
                    pending_due_transient += 1
                elif (
                    retry_policy == "config_or_resource_review"
                    or failure_category
                    in {
                        "client_error",
                        "not_found",
                    }
                ):
                    pending_due_config += 1
                else:
                    pending_due_unknown += 1
        if pending_due <= 0:
            primary_due_focus = "pending_waiting"
        else:
            classified_due = [
                ("pending_reauth_due", pending_due_reauthorize),
                ("pending_transient_due", pending_due_transient),
                ("pending_config_due", pending_due_config),
                ("pending_unknown_due", pending_due_unknown),
            ]
            classified_due.sort(key=lambda item: item[1], reverse=True)
            top_focus, top_count = classified_due[0]
            primary_due_focus = (
                top_focus if top_count == pending_due else "pending_mixed_due"
            )
        return {
            "pending_total": pending_total,
            "pending_due": pending_due,
            "pending_due_reauthorize": pending_due_reauthorize,
            "pending_due_transient": pending_due_transient,
            "pending_due_config": pending_due_config,
            "pending_due_unknown": pending_due_unknown,
            "primary_due_focus": primary_due_focus,
        }
    return {
        "pending_total": 0,
        "pending_due": 0,
        "pending_due_reauthorize": 0,
        "pending_due_transient": 0,
        "pending_due_config": 0,
        "pending_due_unknown": 0,
        "primary_due_focus": "pending_waiting",
    }


def _build_pending_hydration_action_history_index(
    action_history: list[dict[str, Any]] | None,
) -> dict[str, dict[str, Any]]:
    latest_action_summary_by_account: dict[str, dict[str, Any]] = {}
    if not isinstance(action_history, list):
        return latest_action_summary_by_account
    for item in action_history:
        if not isinstance(item, dict):
            continue
        action_name = str(item.get("action") or "").strip().lower()
        if action_name not in {
            "sync_workspace",
            "register_hydration_retry",
            "create_workspace",
        }:
            continue
        payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
        summary = (
            payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
        )
        result = (
            payload.get("result") if isinstance(payload.get("result"), dict) else {}
        )
        account_id = str(
            payload.get("account_id")
            or summary.get("account_id")
            or result.get("account_id")
            or ""
        ).strip()
        if not account_id:
            continue
        latest_action_summary_by_account[account_id] = {
            "failure_category": str(
                summary.get("failure_category") or result.get("failure_category") or ""
            )
            .strip()
            .lower(),
            "reason": str(summary.get("reason") or result.get("reason") or "").strip(),
        }
    return latest_action_summary_by_account


def _resolve_pending_hydration_classification(
    account_id: str,
    status: dict[str, Any],
    latest_action_summary_by_account: dict[str, dict[str, Any]] | None = None,
) -> tuple[str, str]:
    retry_policy = (
        str(status.get("workspace_hydration_retry_policy") or "").strip().lower()
    )
    failure_category = (
        str(status.get("last_workspace_failure_category") or "").strip().lower()
    )
    latest_action = {}
    if account_id and isinstance(latest_action_summary_by_account, dict):
        latest_action = latest_action_summary_by_account.get(account_id) or {}
    if not failure_category:
        failure_category = (
            str(latest_action.get("failure_category") or "").strip().lower()
        )
    if not retry_policy:
        latest_reason = str(latest_action.get("reason") or "").lower()
        if failure_category == "rate_limited" or "429" in latest_reason:
            retry_policy = "upstream_rate_limit"
        elif failure_category in {"network_error", "timeout", "server_error"}:
            retry_policy = "upstream_transient_failure"
        elif failure_category in {"unauthorized", "forbidden"}:
            retry_policy = "reauthorize_or_permission_review"
        elif failure_category in {"client_error", "not_found"}:
            retry_policy = "config_or_resource_review"
    return retry_policy, failure_category


def _build_pending_hydration_operator_guidance(
    status: dict[str, Any],
) -> tuple[str, str]:
    retry_policy = (
        str(status.get("workspace_hydration_retry_policy") or "").strip().lower()
    )
    operator_class = (
        str(status.get("workspace_hydration_operator_classification") or "")
        .strip()
        .lower()
    )
    failure_category = (
        str(status.get("last_workspace_failure_category") or "").strip().lower()
    )
    refresh_recovery_attempted = bool(
        status.get("workspace_hydration_refresh_recovery_attempted", False)
    )
    refresh_recovery_ok = bool(
        status.get("workspace_hydration_refresh_recovery_ok", False)
    )
    last_refresh_action = str(status.get("last_refresh_action") or "").strip().lower()
    last_refresh_failure = (
        str(status.get("last_refresh_failure_category") or "").strip().lower()
    )
    if (
        retry_policy == "reauthorize_or_permission_review"
        or operator_class == "reauthorize"
    ):
        if refresh_recovery_attempted and not refresh_recovery_ok:
            if last_refresh_action == "manual_reauthorize":
                return (
                    "Refresh recovery already confirmed this account needs manual reauthorization.",
                    "Open a new OAuth callback flow for this account, finalize it, then rerun hydration retry.",
                )
            if last_refresh_failure in {
                "unauthorized",
                "forbidden",
                "invalid_grant",
                "unknown_error",
            }:
                return (
                    "Refresh recovery failed, so this account now needs manual reauthorization.",
                    "Replace the invalid OAuth session through callback finalize, then rerun hydration retry.",
                )
        return (
            "Reauthorize this account before retrying hydration.",
            "Refresh OAuth credentials or rerun callback finalize, then run hydration retry again.",
        )
    if (
        retry_policy in {"upstream_transient_failure", "upstream_rate_limit"}
        or operator_class == "transient"
    ):
        return (
            "Stabilize proxy or network reachability before retrying hydration.",
            "Check proxy reachability for Warp or SOCKS, or switch to a healthy path, then rerun hydration retry.",
        )
    if retry_policy == "config_or_resource_review" or operator_class == "config":
        return (
            "Review workspace request configuration before retrying hydration.",
            "Check workspace endpoint templates or resource identifiers, then rerun hydration retry.",
        )
    if failure_category:
        return (
            f"Inspect the last hydration failure: {failure_category}.",
            "Review the last workspace error and action history before retrying hydration.",
        )
    return ("", "")


def _build_runtime_automation_payload(
    request: Request, store: Any, config: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    health_rows = request.app.state.account_pool.get_detailed_status()
    account_view = _build_account_view_with_history(
        store.get_accounts(),
        health_rows,
        config.get("action_history")
        if isinstance(config.get("action_history"), list)
        else [],
    )
    proxy_health_payload = get_proxy_health_payload(config)
    automation = get_register_automation_snapshot()
    pending_summary = _summarize_pending_hydration_from_account_view(
        account_view,
        config.get("action_history")
        if isinstance(config.get("action_history"), list)
        else [],
    )
    automation["pending_hydration_total"] = int(pending_summary["pending_total"] or 0)
    automation["pending_hydration_due"] = int(pending_summary["pending_due"] or 0)
    automation["pending_hydration_due_reauthorize"] = int(
        pending_summary["pending_due_reauthorize"] or 0
    )
    automation["pending_hydration_due_transient"] = int(
        pending_summary["pending_due_transient"] or 0
    )
    automation["pending_hydration_due_config"] = int(
        pending_summary["pending_due_config"] or 0
    )
    automation["pending_hydration_due_unknown"] = int(
        pending_summary["pending_due_unknown"] or 0
    )
    automation["pending_hydration_primary_focus"] = str(
        pending_summary["primary_due_focus"] or "pending_waiting"
    )
    automation["pending_hydration_blocking"] = bool(
        int(pending_summary["pending_due"] or 0) > 0
    )
    runtime_panel = _build_runtime_operations_panel(proxy_health_payload, automation)
    automation["operator_focus"] = str(runtime_panel.get("operator_focus") or "")
    return automation, runtime_panel, account_view, proxy_health_payload


def _build_register_automation_guidance(automation: dict[str, Any]) -> dict[str, Any]:
    reason = (
        str(
            automation.get("current_reason")
            or automation.get("last_decision_reason")
            or "unknown"
        )
        .strip()
        .lower()
    )
    pending_due = int(automation.get("pending_hydration_due") or 0)
    pending_total = int(automation.get("pending_hydration_total") or 0)
    pending_due_reauthorize = int(
        automation.get("pending_hydration_due_reauthorize") or 0
    )
    pending_due_transient = int(automation.get("pending_hydration_due_transient") or 0)
    pending_due_config = int(automation.get("pending_hydration_due_config") or 0)
    pending_due_unknown = int(automation.get("pending_hydration_due_unknown") or 0)
    operator_focus = str(automation.get("operator_focus") or "").strip().lower()
    spacing_remaining = int(automation.get("spacing_remaining_seconds") or 0)
    busy_remaining = int(automation.get("busy_cooldown_remaining_seconds") or 0)
    message_map = {
        "never_run": "Auto-register has not been triggered yet.",
        "auto_register_disabled": "Enable auto-register in runtime config before expecting background registrations.",
        "auto_register_active": "An auto-register task is already running; wait for it to finish.",
        "auto_register_spacing": "The minimum spacing window is still active; wait before starting another registration.",
        "register_task_active": "A manual or background register task is already running; auto-register is paused until it completes.",
        "pending_hydration_due": "Pending hydration retries are already due, so auto-register is paused to avoid competing for the same upstream window.",
        "busy_cooldown_active": "Recent heavy operations or hydration retries triggered the busy cooldown; wait for idle time.",
        "proxy_unconfigured": "The selected proxy mode requires proxy endpoints that are not configured yet.",
        "proxy_unreachable": "The selected proxy endpoint is configured but unreachable; fix local proxy/Warp before retrying.",
        "queued": "Auto-register queued successfully.",
        "ok": "Auto-register is currently eligible to start.",
    }
    next_step_map = {
        "never_run": "Use 'Trigger auto-register' once to validate the workflow before relying on background scheduling.",
        "auto_register_disabled": "Enable auto-register and keep batch size conservative at 1.",
        "auto_register_active": "Wait for the current task to finish before starting another one.",
        "auto_register_spacing": "Wait for the minimum spacing window to expire.",
        "register_task_active": "Do not queue another registration until the current task completes.",
        "pending_hydration_due": "Run hydration retry first and confirm the pending accounts settle before starting another registration batch.",
        "busy_cooldown_active": "Wait for refresh/workspace activity to calm down, then retry during an idle window.",
        "proxy_unconfigured": "Configure direct mode or provide a valid Warp/HTTP/HTTPS/SOCKS5 proxy first.",
        "proxy_unreachable": "Start the local proxy/Warp endpoint or switch runtime proxy mode back to direct before retrying.",
        "queued": "Monitor the register task log and avoid triggering another registration until it finishes.",
        "ok": "The environment is ready for a controlled registration run.",
    }
    severity = (
        "warning"
        if reason
        in {
            "proxy_unconfigured",
            "proxy_unreachable",
            "busy_cooldown_active",
            "pending_hydration_due",
        }
        else ("success" if reason in {"queued", "ok"} else "info")
    )
    message = message_map.get(reason, "Auto-register decision recorded.")
    next_step = next_step_map.get(
        reason, "Review runtime config and recent automation activity."
    )
    blockers: list[str] = []
    if pending_due > 0:
        blockers.append(f"{pending_due} hydration retry due")
        if pending_due_reauthorize > 0:
            blockers.append(f"{pending_due_reauthorize} require reauthorization")
        if pending_due_transient > 0:
            blockers.append(f"{pending_due_transient} transient transport failures")
        if pending_due_config > 0:
            blockers.append(f"{pending_due_config} config or resource issues")
        if pending_due_unknown > 0:
            blockers.append(f"{pending_due_unknown} need manual inspection")
    elif pending_total > 0:
        blockers.append(f"{pending_total} hydration pending")
    if spacing_remaining > 0:
        blockers.append(f"spacing {spacing_remaining}s")
    if busy_remaining > 0:
        blockers.append(f"cooldown {busy_remaining}s")
    if operator_focus == "pending_reauth_due":
        message = (
            f"{pending_due_reauthorize or pending_due} due hydration account(s) require reauthorization because authorization review failed. "
            "Auto-register stays paused until their OAuth or permission state is repaired."
        )
        next_step = "Reauthorize those accounts first, then rerun hydration retry to confirm they can reach a real workspace."
        severity = "warning"
    elif operator_focus == "pending_transient_due":
        message = (
            f"{pending_due_transient or pending_due} due hydration account(s) are failing on network or upstream transport errors. "
            "Auto-register stays paused so the same unstable path is not reused for new signups."
        )
        next_step = "Validate proxy or direct connectivity, then rerun hydration retry for the transient failures."
        severity = "warning"
    elif operator_focus == "pending_config_due":
        message = (
            f"{pending_due_config or pending_due} due hydration account(s) are blocked by config or resource review issues. "
            "Auto-register remains paused until the workspace request path is corrected."
        )
        next_step = "Inspect workspace configuration or target resources, then retry hydration after the mismatch is fixed."
        severity = "warning"
    elif operator_focus == "pending_mixed_due":
        message = (
            "Due hydration accounts are split across reauthorization and transient failures, so auto-register is paused "
            "until each blocker is handled with the right playbook."
        )
        next_step = "Reauthorize invalid accounts first, then repair network or proxy reachability before rerunning hydration retry."
        severity = "warning"
    elif pending_due > 0 and reason in {
        "ok",
        "busy_cooldown_active",
        "auto_register_spacing",
        "pending_hydration_due",
    }:
        message = f"{message} There are {pending_due} pending hydration account(s) already due for retry."
        next_step = "Run hydration retry before adding more fresh registrations."
        severity = "warning"
    elif pending_total > 0 and operator_focus == "pending_waiting":
        message = (
            f"{pending_total} pending hydration account(s) are still waiting for their retry window. "
            "Auto-register can stay conservative until they cool down."
        )
        next_step = "Wait for the retry window or refresh status before starting more registrations."
        severity = "info"
    elif operator_focus == "proxy_blocked" and pending_total > 0:
        split_notes: list[str] = []
        if pending_due_reauthorize > 0:
            split_notes.append(f"{pending_due_reauthorize} need reauthorization")
        if pending_due_transient > 0:
            split_notes.append(f"{pending_due_transient} have transport failures")
        if pending_due_config > 0:
            split_notes.append(f"{pending_due_config} need config review")
        if split_notes:
            message = (
                f"Proxy access is currently blocked and {pending_total} hydration account(s) are also pending. "
                f"Due split: {', '.join(split_notes)}."
            )
            next_step = "Restore proxy reachability first, then re-check the due split and handle reauthorization before retrying hydration."
        else:
            message = f"Proxy access is currently blocked and {pending_total} hydration account(s) are also pending."
            next_step = "Restore proxy reachability first, then re-check pending hydration before triggering auto-register."
        severity = "warning"
    return {
        "reason": reason,
        "message": message,
        "severity": severity,
        "next_step": next_step,
        "blockers": blockers,
        "operator_focus": operator_focus,
        "eligible": bool(automation.get("eligible", False)),
        "pending_hydration_due": pending_due,
        "pending_hydration_total": pending_total,
        "pending_hydration_due_reauthorize": pending_due_reauthorize,
        "pending_hydration_due_transient": pending_due_transient,
        "pending_hydration_due_config": pending_due_config,
        "pending_hydration_due_unknown": pending_due_unknown,
        "spacing_remaining_seconds": spacing_remaining,
        "busy_cooldown_remaining_seconds": busy_remaining,
        "next_eligible_at": int(automation.get("next_eligible_at") or 0),
    }


def _check_proxy_endpoint(proxy_url: str) -> dict[str, Any]:
    value = str(proxy_url or "").strip()
    if not value:
        return {"configured": False, "reachable": False, "host": "", "port": 0}
    parsed = urlparse(value)
    host = str(parsed.hostname or "")
    port = int(parsed.port or 0)
    if not host or not port:
        return {
            "configured": True,
            "reachable": False,
            "host": host,
            "port": port,
            "error": "invalid_proxy_url",
        }
    try:
        with socket.create_connection((host, port), timeout=2):
            return {
                "configured": True,
                "reachable": True,
                "host": host,
                "port": port,
            }
    except OSError as exc:
        return {
            "configured": True,
            "reachable": False,
            "host": host,
            "port": port,
            "error": str(exc),
        }


def get_proxy_health_payload(config: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "summary": _build_proxy_health_summary(config),
        "checks": {
            "warp": _check_proxy_endpoint(str(config.get("upstream_warp_proxy") or "")),
            "socks5": _check_proxy_endpoint(
                str(config.get("upstream_socks5_proxy") or "")
            ),
            "http": _check_proxy_endpoint(str(config.get("upstream_http_proxy") or "")),
            "https": _check_proxy_endpoint(
                str(config.get("upstream_https_proxy") or "")
            ),
        },
    }
    reachable = [
        label
        for label, item in payload["checks"].items()
        if item.get("configured") and item.get("reachable")
    ]
    payload["summary"]["reachable_targets"] = reachable
    payload["summary"]["reachable_target_count"] = len(reachable)
    mode = str(payload["summary"].get("mode") or "direct").strip().lower()
    configured_any = any(
        bool(item.get("configured")) for item in payload["checks"].values()
    )
    if mode == "direct":
        payload["summary"]["operator_state"] = "direct"
    elif reachable:
        payload["summary"]["operator_state"] = "ready"
    elif configured_any:
        payload["summary"]["operator_state"] = "degraded"
        payload["summary"]["hint"] = (
            "Proxy endpoints are configured, but none of them are reachable right now."
        )
    else:
        payload["summary"]["operator_state"] = "degraded"
    return payload


class AdminLoginRequest(BaseModel):
    username: str = Field(default="")
    password: str = Field(default="")


class AdminChangePasswordRequest(BaseModel):
    current_password: str = Field(default="")
    new_password: str | None = Field(default=None)
    new_username: str | None = Field(default=None)


class ChatLoginRequest(BaseModel):
    password: str = Field(default="")


class RuntimeSettingsRequest(BaseModel):
    app_mode: str = Field(default="standard")
    api_key: str | None = Field(default=None)
    allowed_origins: list[str] = Field(default_factory=list)
    siliconflow_api_key: str | None = Field(default=None)
    upstream_proxy: str = Field(default="")
    upstream_http_proxy: str = Field(default="")
    upstream_https_proxy: str = Field(default="")
    upstream_socks5_proxy: str = Field(default="")
    upstream_proxy_mode: str = Field(default="direct")
    upstream_warp_enabled: bool = False
    upstream_warp_proxy: str = Field(default="")
    auto_create_workspace: bool = False
    auto_select_workspace: bool = True
    workspace_create_dry_run: bool = True
    workspace_creation_template_space_id: str = Field(default="")
    account_probe_interval_seconds: int = 300
    refresh_execution_mode: str = Field(default="manual")
    refresh_request_url: str = Field(default="")
    refresh_client_id: str = Field(default="")
    refresh_client_secret: str | None = Field(default=None)
    workspace_execution_mode: str = Field(default="manual")
    workspace_request_url: str = Field(default="")
    allow_real_probe_requests: bool = False
    chat_enabled: bool = False
    media_public_base_url: str = Field(default="")
    media_storage_path: str = Field(default="")
    chat_password_enabled: bool = False
    chat_password: str | None = Field(default=None)
    auto_register_enabled: bool = False
    auto_register_idle_only: bool = True
    auto_register_interval_seconds: int = 1800
    auto_register_min_spacing_seconds: int = 900
    auto_register_busy_cooldown_seconds: int = 1200
    auto_register_batch_size: int = 1
    auto_register_headless: bool = False
    auto_register_use_api: bool = True
    auto_register_mail_provider: str = Field(default="freemail")
    auto_register_mail_base_url: str = Field(default="")
    auto_register_mail_api_key: str | None = Field(default=None)
    auto_register_domain: str = Field(default="")


class AccountUpsertRequest(BaseModel):
    id: str | None = None
    token_v2: str
    space_id: str
    user_id: str
    space_view_id: str = ""
    user_name: str = "user"
    user_email: str = ""
    plan_type: str = "unknown"
    enabled: bool = True
    source: str = "manual"
    notes: str = ""
    tags: list[str] = Field(default_factory=list)
    oauth: dict[str, Any] = Field(default_factory=dict)
    workspace: dict[str, Any] = Field(default_factory=dict)
    status: dict[str, Any] = Field(default_factory=dict)


class AccountImportRequest(BaseModel):
    accounts: list[AccountUpsertRequest] = Field(default_factory=list)


class AccountReplaceRequest(BaseModel):
    accounts: list[AccountUpsertRequest] = Field(default_factory=list)


class OAuthAccountImportRequest(BaseModel):
    token_v2: str
    space_id: str = ""
    user_id: str
    space_view_id: str = ""
    user_name: str = "user"
    user_email: str = ""
    plan_type: str = "unknown"
    access_token: str = ""
    refresh_token: str = ""
    expires_at: int | None = None
    provider: str = "notion-web"
    scopes: list[str] = Field(default_factory=list)
    source: str = "oauth"
    notes: str = ""
    tags: list[str] = Field(default_factory=list)


class OAuthCallbackPayload(BaseModel):
    token_v2: str
    user_id: str
    space_id: str = ""
    space_view_id: str = ""
    user_name: str = "user"
    user_email: str = ""
    access_token: str = ""
    refresh_token: str = ""
    expires_at: int | None = None
    provider: str = "notion-web"
    scopes: list[str] = Field(default_factory=list)
    plan_type: str = "unknown"
    source: str = "oauth_callback"
    notes: str = ""
    tags: list[str] = Field(default_factory=list)


class OAuthStartRequest(BaseModel):
    redirect_uri: str = ""
    state: str = ""
    provider: str = "notion-web"


class OAuthFinalizeRequest(BaseModel):
    token_v2: str
    user_id: str
    redirect_uri: str = ""
    state: str = ""
    provider: str = "notion-web"
    space_id: str = ""
    space_view_id: str = ""
    user_name: str = "user"
    user_email: str = ""
    access_token: str = ""
    refresh_token: str = ""
    expires_at: int | None = None
    scopes: list[str] = Field(default_factory=list)
    plan_type: str = "unknown"
    notes: str = ""
    tags: list[str] = Field(default_factory=list)


class AccountPatchRequest(BaseModel):
    enabled: bool | None = None
    notes: str | None = None
    tags: list[str] | None = None


class AccountActionRequest(BaseModel):
    account_id: str


class BulkAccountActionRequest(BaseModel):
    account_ids: list[str] | None = None
    action: str


class UsageQueryFilters(BaseModel):
    start_ts: int | None = None
    end_ts: int | None = None
    model: str | None = None
    account_id: str | None = None
    request_type: str | None = None
    limit: int = Field(default=100, ge=1, le=500)
    offset: int = Field(default=0, ge=0)


def _prune_admin_sessions(request: Request) -> None:
    sessions = getattr(request.app.state, "admin_sessions", {})
    now_ts = int(time.time())
    expired_tokens = [
        token
        for token, session in sessions.items()
        if int(session.get("expires_at") or 0) <= now_ts
    ]
    for token in expired_tokens:
        sessions.pop(token, None)


def _create_admin_session(request: Request, username: str) -> dict[str, Any]:
    _prune_admin_sessions(request)
    token = secrets.token_urlsafe(32)
    now_ts = int(time.time())
    ttl_seconds = int(
        getattr(request.app.state, "admin_session_ttl_seconds", 43200) or 43200
    )
    session = {
        "token": token,
        "username": str(username or "").strip(),
        "created_at": now_ts,
        "expires_at": now_ts + max(300, ttl_seconds),
    }
    getattr(request.app.state, "admin_sessions", {})[token] = session
    return session


def _prune_chat_sessions(request: Request) -> None:
    sessions = getattr(request.app.state, "chat_sessions", {})
    now_ts = int(time.time())
    expired_tokens = [
        token
        for token, session in sessions.items()
        if int(session.get("expires_at") or 0) <= now_ts
    ]
    for token in expired_tokens:
        sessions.pop(token, None)


def _create_chat_session(request: Request) -> dict[str, Any]:
    _prune_chat_sessions(request)
    token = secrets.token_urlsafe(32)
    now_ts = int(time.time())
    ttl_seconds = int(
        getattr(
            request.app.state,
            "chat_session_ttl_seconds",
            get_chat_session_ttl_seconds(),
        )
        or get_chat_session_ttl_seconds()
    )
    session = {
        "token": token,
        "created_at": now_ts,
        "expires_at": now_ts + max(300, ttl_seconds),
    }
    getattr(request.app.state, "chat_sessions", {})[token] = session
    return session


def _build_admin_auth_status(request: Request) -> dict[str, Any]:
    admin_auth = get_admin_auth()
    request.app.state.admin_auth = admin_auth
    configured = bool(
        str(admin_auth.get("password_hash") or "").strip()
        and str(admin_auth.get("password_salt") or "").strip()
    )
    initialized_from_default = bool(admin_auth.get("initialized_from_default", True))
    auth_source = (
        "bootstrap_admin_password" if initialized_from_default else "persisted"
    )
    auth_source_label = (
        "bootstrap from ADMIN_PASSWORD"
        if initialized_from_default
        else "persisted runtime config"
    )
    return {
        "username": str(admin_auth.get("username") or ""),
        "must_change_password": bool(admin_auth.get("must_change_password", False)),
        "initialized_from_default": initialized_from_default,
        "configured": configured,
        "auth_source": auth_source,
        "auth_source_label": auth_source_label,
        "updated_at": int(admin_auth.get("updated_at") or 0),
    }


def _current_admin_session(
    request: Request,
    session_token: str | None,
    *,
    allow_password_change_required: bool = False,
) -> dict[str, Any]:
    token = str(session_token or "").strip()
    if not token:
        raise HTTPException(status_code=401, detail="Admin session required")
    _prune_admin_sessions(request)
    sessions = getattr(request.app.state, "admin_sessions", {})
    session = sessions.get(token)
    if not isinstance(session, dict):
        raise HTTPException(status_code=401, detail="Invalid admin session")
    auth_status = _build_admin_auth_status(request)
    if session.get("username") != auth_status["username"]:
        sessions.pop(token, None)
        raise HTTPException(status_code=401, detail="Admin session is no longer valid")
    return session


def _ensure_admin(
    request: Request,
    session_token: str | None,
    *,
    allow_password_change_required: bool = False,
) -> dict[str, Any]:
    return _current_admin_session(
        request,
        session_token,
        allow_password_change_required=allow_password_change_required,
    )


def _build_chat_auth_status(request: Request) -> dict[str, Any]:
    chat_auth = get_chat_auth()
    configured = bool(
        str(chat_auth.get("password_hash") or "").strip()
        and str(chat_auth.get("password_salt") or "").strip()
    )
    enabled = bool(chat_auth.get("enabled", False) and configured)
    return {
        "configured": configured,
        "enabled": enabled,
        "updated_at": int(chat_auth.get("updated_at") or 0),
    }


def _current_chat_session(
    request: Request, session_token: str | None
) -> dict[str, Any]:
    token = str(session_token or "").strip()
    if not token:
        raise HTTPException(status_code=401, detail="Chat session required")
    _prune_chat_sessions(request)
    sessions = getattr(request.app.state, "chat_sessions", {})
    session = sessions.get(token)
    if not isinstance(session, dict):
        raise HTTPException(status_code=401, detail="Invalid chat session")
    return session


def _ensure_chat_access(request: Request, session_token: str | None) -> dict[str, Any]:
    chat_status = _build_chat_auth_status(request)
    if not chat_status["enabled"]:
        return {"mode": "open"}
    admin_session = request.headers.get("X-Admin-Session") or ""
    if admin_session:
        try:
            _ensure_admin(request, admin_session)
            return {"mode": "admin"}
        except HTTPException:
            pass
    _current_chat_session(request, session_token)
    return {"mode": "password"}


def _rebuild_pool(request: Request) -> None:
    pool = AccountPool(get_accounts())
    pool.expand_workspaces()
    pool.probe_accounts()
    request.app.state.account_pool = pool


def _get_usage_store(request: Request) -> UsageStore:
    usage_store = getattr(request.app.state, "usage_store", None)
    if usage_store is None:
        usage_store = UsageStore()
        request.app.state.usage_store = usage_store
    return usage_store


def _build_saved_oauth_account(
    payload: OAuthAccountImportRequest | OAuthCallbackPayload,
) -> dict[str, Any]:
    return {
        "token_v2": payload.token_v2,
        "space_id": payload.space_id,
        "user_id": payload.user_id,
        "space_view_id": payload.space_view_id,
        "user_name": payload.user_name,
        "user_email": payload.user_email,
        "plan_type": payload.plan_type,
        "enabled": True,
        "source": payload.source,
        "notes": payload.notes,
        "tags": payload.tags,
        "oauth": {
            "provider": payload.provider,
            "access_token": payload.access_token,
            "refresh_token": payload.refresh_token,
            "expires_at": payload.expires_at,
            "scopes": payload.scopes,
        },
    }


def _prune_oauth_callback_sessions(request: Request) -> None:
    sessions = getattr(request.app.state, "oauth_callback_sessions", {})
    now_ts = int(time.time())
    expired_tokens = [
        token
        for token, session in sessions.items()
        if int(session.get("expires_at") or 0) <= now_ts
    ]
    for token in expired_tokens:
        sessions.pop(token, None)


def _register_oauth_callback_session(
    request: Request, *, provider: str, redirect_uri: str, state: str
) -> dict[str, Any]:
    _prune_oauth_callback_sessions(request)
    now_ts = int(time.time())
    session = {
        "provider": str(provider or "notion-web").strip() or "notion-web",
        "redirect_uri": redirect_uri,
        "state": state,
        "created_at": now_ts,
        "expires_at": now_ts + _OAUTH_CALLBACK_TTL_SECONDS,
        "callback_payload": {},
        "status": "pending_callback",
    }
    getattr(request.app.state, "oauth_callback_sessions", {})[state] = session
    return session


def _get_oauth_callback_session(request: Request, state: str) -> dict[str, Any] | None:
    _prune_oauth_callback_sessions(request)
    return getattr(request.app.state, "oauth_callback_sessions", {}).get(state)


def _store_oauth_callback_session_payload(
    request: Request, *, state: str, payload: dict[str, Any]
) -> dict[str, Any]:
    sessions = getattr(request.app.state, "oauth_callback_sessions", {})
    session = _get_oauth_callback_session(request, state)
    if not session:
        raise HTTPException(status_code=404, detail="OAuth callback session not found")
    session["callback_payload"] = payload
    session["status"] = "callback_received"
    sessions[state] = session
    return session


def _consume_oauth_callback_session_payload(
    request: Request, state: str
) -> dict[str, Any] | None:
    sessions = getattr(request.app.state, "oauth_callback_sessions", {})
    session = _get_oauth_callback_session(request, state)
    if not session:
        return None
    payload = (
        session.get("callback_payload")
        if isinstance(session.get("callback_payload"), dict)
        else None
    )
    sessions.pop(state, None)
    return payload


def _normalize_callback_redirect_uri(value: str, fallback: str) -> str:
    candidate = str(value or "").strip()
    if not candidate:
        return fallback
    parsed = urlparse(candidate)
    scheme = str(parsed.scheme or "").lower()
    hostname = str(parsed.hostname or "").strip().lower()
    if scheme not in {"http", "https"} or not hostname:
        return fallback
    if hostname in {"localhost", "127.0.0.1", "::1"}:
        return candidate
    try:
        ip = ipaddress.ip_address(hostname)
    except ValueError:
        ip = None
    if ip and (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved):
        return fallback
    safe_path = parsed.path or "/"
    safe_query = (
        parsed.query if parsed.query and "redirect_uri=" not in parsed.query else ""
    )
    return urlunparse((scheme, parsed.netloc, safe_path, "", safe_query, ""))


def _default_local_redirect_uri(request: Request) -> str:
    fallback = "http://localhost:8000"
    host = (request.headers.get("host") or "").strip()
    if host:
        forwarded_proto = (
            str(request.headers.get("x-forwarded-proto") or "")
            .split(",")[0]
            .strip()
            .lower()
        )
        scheme = forwarded_proto or request.url.scheme or "http"
        return _normalize_callback_redirect_uri(f"{scheme}://{host}", fallback)
    return fallback


def _build_callback_redirect_url(
    target_redirect_uri: str, query_params: dict[str, str]
) -> str:
    filtered = {
        key: value
        for key, value in query_params.items()
        if value and key not in {"redirect_uri"}
    }
    if not filtered:
        return target_redirect_uri
    separator = "&" if "?" in target_redirect_uri else "?"
    return f"{target_redirect_uri}{separator}{urlencode(filtered)}"


def _build_oauth_start_payload(
    request: Request,
    *,
    redirect_uri: str,
    state: str,
    provider: str,
) -> dict[str, Any]:
    base_redirect_uri = _default_local_redirect_uri(request).rstrip("/")
    callback_bridge_url = _build_callback_redirect_url(
        f"{base_redirect_uri}/v1/admin/oauth/callback",
        {"redirect_uri": redirect_uri, "state": state, "provider": provider},
    )
    return {
        "provider": provider,
        "redirect_uri": redirect_uri,
        "state": state,
        "authorization_url": "",
        "callback_bridge_url": callback_bridge_url,
        "status": "manual_callback_required",
        "message": "当前环境未配置真实 Notion OAuth App。请在完成网页登录后，将 localhost callback URL 粘回管理面板完成导入。",
    }


@router.get("/admin/request-templates")
async def request_templates(
    request: Request,
    x_admin_session: str | None = Header(default=None, alias="X-Admin-Session"),
):
    _ensure_admin(request, x_admin_session)
    config = get_config_store().get_config()
    template_space_id = str(config.get("workspace_creation_template_space_id") or "")
    return {
        "ok": True,
        "response_mode": "template_preview",
        "contains_secrets": False,
        "refresh": _build_generic_refresh_request_template(),
        "workspace_create": _build_generic_workspace_request_template(
            template_space_id
        ),
    }


@router.get("/admin/report")
async def admin_report(
    request: Request,
    action_account: str | None = Query(default=None),
    x_admin_session: str | None = Header(default=None, alias="X-Admin-Session"),
):
    _ensure_admin(request, x_admin_session)
    store = get_config_store()
    config = store.get_config()
    health_rows = request.app.state.account_pool.get_detailed_status()
    accounts = _build_account_view_with_history(
        config.get("accounts", []),
        health_rows,
        config.get("action_history")
        if isinstance(config.get("action_history"), list)
        else [],
    )
    alerts = _build_alerts(accounts)
    refresh_diagnostics = _build_refresh_diagnostics(accounts)
    workspace_diagnostics = _build_workspace_diagnostics(accounts)
    operation_logs = (
        config.get("operation_logs")
        if isinstance(config.get("operation_logs"), list)
        else []
    )
    probe_logs = (
        config.get("probe_logs") if isinstance(config.get("probe_logs"), list) else []
    )
    action_history = (
        config.get("action_history")
        if isinstance(config.get("action_history"), list)
        else []
    )
    filtered_action_history = _filter_action_history(action_history, action_account)
    settings = _redact_runtime_settings(
        {
            "app_mode": config.get("app_mode", get_app_mode()),
            "allowed_origins": config.get("allowed_origins", []),
            "upstream_proxy": config.get("upstream_proxy", ""),
            "upstream_http_proxy": config.get("upstream_http_proxy", ""),
            "upstream_https_proxy": config.get("upstream_https_proxy", ""),
            "upstream_socks5_proxy": config.get("upstream_socks5_proxy", ""),
            "upstream_proxy_mode": config.get("upstream_proxy_mode", "direct"),
            "upstream_warp_enabled": config.get("upstream_warp_enabled", False),
            "upstream_warp_proxy": config.get("upstream_warp_proxy", ""),
            "auto_create_workspace": config.get("auto_create_workspace", False),
            "auto_select_workspace": config.get("auto_select_workspace", True),
            "workspace_create_dry_run": config.get("workspace_create_dry_run", True),
            "workspace_creation_template_space_id": config.get(
                "workspace_creation_template_space_id", ""
            ),
            "account_probe_interval_seconds": config.get(
                "account_probe_interval_seconds", 300
            ),
            "refresh_execution_mode": config.get("refresh_execution_mode", "manual"),
            "refresh_request_url": config.get("refresh_request_url", ""),
            "refresh_client_id": config.get("refresh_client_id", ""),
            "refresh_client_secret": config.get("refresh_client_secret", ""),
            "workspace_execution_mode": config.get(
                "workspace_execution_mode", "manual"
            ),
            "workspace_request_url": config.get("workspace_request_url", ""),
            "allow_real_probe_requests": config.get("allow_real_probe_requests", False),
            "chat_enabled": config.get("chat_enabled", False),
        }
    )
    return {
        "ok": True,
        "generated_at": int(time.time()),
        "redaction_mode": "safe",
        "settings_view_mode": "safe",
        "accounts_view_mode": "safe",
        "storage": {
            "runtime_config_path": str(CONFIG_PATH),
            "accounts_path": str(ACCOUNTS_PATH),
        },
        "settings": settings,
        "accounts": _redact_account_list(accounts),
        "alerts": alerts,
        "refresh_diagnostics": refresh_diagnostics,
        "workspace_diagnostics": workspace_diagnostics,
        "request_templates": {
            "refresh": _build_generic_refresh_request_template(),
            "workspace_create": _build_generic_workspace_request_template(
                str(config.get("workspace_creation_template_space_id") or "")
            ),
        },
        "operation_logs": operation_logs,
        "probe_logs": probe_logs,
        "action_history": filtered_action_history,
        "action_history_filters": {
            "account": str(action_account or "").strip(),
        },
    }


@router.get("/admin/overview")
async def admin_overview(
    request: Request,
    action_account: str | None = Query(default=None),
    x_admin_session: str | None = Header(default=None, alias="X-Admin-Session"),
):
    return await admin_snapshot(
        request=request,
        action_account=action_account,
        x_admin_session=x_admin_session,
    )


@router.get("/admin/snapshot")
async def admin_snapshot(
    request: Request,
    action_account: str | None = Query(default=None),
    x_admin_session: str | None = Header(default=None, alias="X-Admin-Session"),
):
    _ensure_admin(request, x_admin_session)
    store = get_config_store()
    config = store.get_config()
    health_rows = request.app.state.account_pool.get_detailed_status()
    accounts = _build_account_view_with_history(
        config.get("accounts", []),
        health_rows,
        config.get("action_history")
        if isinstance(config.get("action_history"), list)
        else [],
    )
    alerts = _build_alerts(accounts)
    refresh_diagnostics = _build_refresh_diagnostics(accounts)
    workspace_diagnostics = _build_workspace_diagnostics(accounts)
    operation_logs = (
        config.get("operation_logs")
        if isinstance(config.get("operation_logs"), list)
        else []
    )
    probe_logs = (
        config.get("probe_logs") if isinstance(config.get("probe_logs"), list) else []
    )
    action_history = (
        config.get("action_history")
        if isinstance(config.get("action_history"), list)
        else []
    )
    filtered_action_history = _filter_action_history(action_history, action_account)
    return {
        "ok": True,
        "generated_at": int(time.time()),
        "redaction_mode": "safe",
        "settings_view_mode": "safe",
        "accounts_view_mode": "safe",
        "summary": {
            "accounts": len(accounts),
            "usable": sum(
                1 for item in accounts if item.get("status", {}).get("usable")
            ),
            "alerts": alerts.get("summary", {}).get("total_alerts", 0),
            "refresh_ready": refresh_diagnostics.get("summary", {}).get(
                "refresh_ready", 0
            ),
            "workspace_ready": workspace_diagnostics.get("summary", {}).get("ready", 0),
            "workspace_hydration_due": sum(
                1
                for item in accounts
                if item.get("status", {}).get("effective_state")
                == "workspace_creation_pending"
                and int(
                    item.get("status", {}).get("workspace_hydration_retry_after") or 0
                )
                <= int(time.time())
            ),
            "operations": len(operation_logs),
            "actions": len(filtered_action_history),
        },
        "alerts": alerts.get("summary", {}),
        "refresh": refresh_diagnostics.get("summary", {}),
        "workspace": workspace_diagnostics.get("summary", {}),
        "recent_operations": operation_logs[-10:],
        "recent_probes": probe_logs[-10:],
        "recent_actions": filtered_action_history[-10:],
        "action_history_filters": {
            "account": str(action_account or "").strip(),
        },
        "request_templates": {
            "refresh": _build_generic_refresh_request_template(),
            "workspace_create": _build_generic_workspace_request_template(
                str(config.get("workspace_creation_template_space_id") or "")
            ),
        },
    }


def _build_account_view(
    accounts: list[dict[str, Any]],
    health_rows: list[dict[str, Any]],
    action_history: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    health_by_id = {
        str(row.get("account_id") or ""): row
        for row in health_rows
        if str(row.get("account_id") or "")
    }
    health_by_user_id = {
        str(row.get("user_id") or ""): row
        for row in health_rows
        if str(row.get("user_id") or "")
    }

    latest_action_summary_by_account = _build_pending_hydration_action_history_index(
        action_history
    )
    merged_rows: list[dict[str, Any]] = []
    for account in accounts:
        account_id = str(account.get("id") or "")
        user_id = str(account.get("user_id") or "")
        health = health_by_id.get(account_id) or health_by_user_id.get(user_id) or {}
        workspace = (
            account.get("workspace")
            if isinstance(account.get("workspace"), dict)
            else {}
        )
        status = (
            account.get("status") if isinstance(account.get("status"), dict) else {}
        )
        oauth = account.get("oauth") if isinstance(account.get("oauth"), dict) else {}
        status_has_needs_reauth = "needs_reauth" in status
        status_has_reauthorize_required = "reauthorize_required" in status

        enabled = bool(account.get("enabled", True))
        workspace_state = str(
            workspace.get("state") or status.get("workspace_state") or "missing"
        )
        if (
            str(status.get("workspace_state") or "").strip()
            == "workspace_creation_failed"
        ):
            workspace_state = "workspace_creation_failed"
        persisted_workspace_count = int(workspace.get("workspace_count") or 0)
        health_workspace_count = int(health.get("workspace_count") or 0)
        workspace_count = max(persisted_workspace_count, health_workspace_count)
        health_plan_type = str(health.get("plan_type") or "").strip()
        stored_plan_type = str(account.get("plan_type") or "").strip()
        plan_type = (
            health_plan_type
            if health_plan_type and health_plan_type.lower() != "unknown"
            else (stored_plan_type or "unknown")
        )
        health_subscription_tier = str(health.get("subscription_tier") or "").strip()
        stored_subscription_tier = str(workspace.get("subscription_tier") or "").strip()
        subscription_tier = health_subscription_tier or stored_subscription_tier
        normalized_plan = plan_type.lower().strip()
        normalized_tier = subscription_tier.lower().strip()
        if (
            "education" in normalized_plan
            or "education" in normalized_tier
            or "student" in normalized_tier
        ):
            plan_category = "education"
        elif normalized_plan in {"free", "unknown", ""} and normalized_tier in {
            "free",
            "",
            "unknown",
        }:
            plan_category = "free"
        elif normalized_plan in {
            "plus",
            "business",
            "enterprise",
            "pro",
        } or normalized_tier in {"plus", "business", "enterprise", "pro"}:
            plan_category = "paid"
        else:
            plan_category = normalized_plan or normalized_tier or "unknown"
        oauth_expired = bool(
            status.get("oauth_expired")
            if "oauth_expired" in status
            else oauth.get("expired", False)
        )
        needs_refresh = bool(
            status.get("needs_refresh")
            if "needs_refresh" in status
            else oauth.get("needs_refresh", False)
        )
        raw_pool_state = str(health.get("state") or status.get("state") or "unknown")
        no_workspace = workspace_count == 0
        refresh_probe_ok = bool(
            status.get("last_probe_ok", False)
            and status.get("last_probe_action") == "refresh_probe"
        )
        workspace_probe_ok = bool(
            status.get("last_probe_ok", False)
            and status.get("last_probe_action") == "workspace_probe"
        )
        if workspace_state == "ready" or workspace_count > 0:
            pool_state = "active"
        elif refresh_probe_ok and not oauth_expired and not needs_refresh:
            pool_state = "no_workspace" if no_workspace else "active"
        elif (
            not oauth_expired
            and not needs_refresh
            and str(status.get("last_refresh_action") or "").strip()
        ):
            pool_state = "no_workspace" if no_workspace else "active"
        elif oauth_expired or needs_refresh:
            pool_state = raw_pool_state
        else:
            pool_state = raw_pool_state

        if not enabled:
            effective_state = "disabled"
        elif oauth_expired and workspace_state != "ready" and no_workspace:
            effective_state = "oauth_expired"
        elif needs_refresh and workspace_state != "ready" and no_workspace:
            effective_state = "needs_refresh"
        elif workspace_state in {
            "workspace_creation_pending",
            "workspace_creation_unimplemented",
            "workspace_creation_unverified",
        }:
            effective_state = workspace_state
        elif pool_state in {"invalid", "cooling", "active", "no_workspace"}:
            effective_state = pool_state
        elif no_workspace:
            effective_state = "no_workspace"
        else:
            effective_state = "unknown"

        usable = enabled and not no_workspace and pool_state == "active"
        hydration_retry_policy, hydration_failure_category = (
            _resolve_pending_hydration_classification(
                account_id,
                status,
                latest_action_summary_by_account,
            )
        )
        hydration_operator_classification = ""
        if hydration_retry_policy == "reauthorize_or_permission_review":
            hydration_operator_classification = "reauthorize"
        elif hydration_retry_policy in {
            "upstream_transient_failure",
            "upstream_rate_limit",
        }:
            hydration_operator_classification = "transient"
        elif hydration_retry_policy == "config_or_resource_review":
            hydration_operator_classification = "config"
        elif hydration_failure_category:
            hydration_operator_classification = "inspect"
        hydration_guidance, hydration_next_step = (
            _build_pending_hydration_operator_guidance(
                {
                    **status,
                    "workspace_hydration_retry_policy": hydration_retry_policy,
                    "workspace_hydration_operator_classification": hydration_operator_classification,
                    "last_workspace_failure_category": hydration_failure_category,
                }
            )
        )

        merged_rows.append(
            {
                **account,
                "workspace": {
                    **workspace,
                    "state": workspace_state,
                    "workspace_count": workspace_count,
                    "workspaces": workspace.get("workspaces")
                    or health.get("workspaces")
                    or [],
                    "subscription_tier": subscription_tier,
                },
                "oauth": {
                    **(
                        health.get("oauth")
                        if isinstance(health.get("oauth"), dict)
                        else {}
                    ),
                    **oauth,
                },
                "status": {
                    **status,
                    "plan_category": plan_category,
                    "pool_state": pool_state,
                    "effective_state": effective_state,
                    "usable": usable,
                    "enabled": enabled,
                    "no_workspace": no_workspace,
                    "oauth_expired": oauth_expired,
                    "needs_refresh": needs_refresh,
                    "needs_reauth": bool(
                        status.get("needs_reauth", False)
                        if status_has_needs_reauth
                        else health.get("needs_reauth", False)
                    ),
                    "workspace_state": workspace_state,
                    "cooldown_until": health.get(
                        "cooldown_until", status.get("cooldown_until", 0)
                    ),
                    "invalid_until": health.get(
                        "invalid_until", status.get("invalid_until", 0)
                    ),
                    "last_status_code": health.get(
                        "last_status_code", status.get("last_status_code")
                    ),
                    "last_error": health.get(
                        "last_error", status.get("last_error", "")
                    ),
                    "last_success_at": health.get(
                        "last_success_at", status.get("last_success_at", 0)
                    ),
                    "last_refresh_at": status.get("last_refresh_at")
                    or health.get("last_refresh_at", 0),
                    "last_refresh_error": status.get("last_refresh_error", "")
                    if "last_refresh_error" in status
                    else health.get("last_refresh_error", ""),
                    "last_refresh_action": status.get("last_refresh_action", "")
                    if "last_refresh_action" in status
                    else health.get("last_refresh_action", ""),
                    "last_refresh_failure_category": status.get(
                        "last_refresh_failure_category", ""
                    ),
                    "last_workspace_check_at": health.get("last_workspace_check_at")
                    or status.get("last_workspace_check_at", 0),
                    "last_workspace_action": health.get("last_workspace_action")
                    or status.get("last_workspace_action", ""),
                    "last_workspace_error": health.get("last_workspace_error")
                    or status.get("last_workspace_error", ""),
                    "last_workspace_failure_category": hydration_failure_category,
                    "workspace_hydration_retry_policy": hydration_retry_policy,
                    "workspace_hydration_operator_classification": hydration_operator_classification,
                    "workspace_hydration_guidance": hydration_guidance,
                    "workspace_hydration_next_step": hydration_next_step,
                    "workspace_hydration_refresh_recovery_attempted": bool(
                        status.get(
                            "workspace_hydration_refresh_recovery_attempted", False
                        )
                    ),
                    "workspace_hydration_refresh_recovery_ok": bool(
                        status.get("workspace_hydration_refresh_recovery_ok", False)
                    ),
                    "workspace_hydration_retry_after": status.get(
                        "workspace_hydration_retry_after", 0
                    ),
                    "workspace_hydration_pending": bool(
                        status.get("workspace_hydration_pending", False)
                    ),
                    "workspace_hydration_backoff_seconds": status.get(
                        "workspace_hydration_backoff_seconds", 0
                    ),
                    "workspace_poll_count": health.get("workspace_poll_count")
                    if health.get("workspace_poll_count") is not None
                    else status.get("workspace_poll_count", 0),
                    "keepalive_failures": health.get("keepalive_failures")
                    if health.get("keepalive_failures") is not None
                    else status.get("keepalive_failures", 0),
                    "workspace_expand_error": status.get("workspace_expand_error", "")
                    if "workspace_expand_error" in status
                    else health.get("workspace_expand_error", ""),
                    "workspace_expand_status_code": status.get(
                        "workspace_expand_status_code"
                    )
                    if "workspace_expand_status_code" in status
                    else health.get("workspace_expand_status_code"),
                    "reauthorize_required": bool(
                        status.get("reauthorize_required", False)
                        if status_has_reauthorize_required
                        else health.get("reauthorize_required", False)
                    )
                    or hydration_retry_policy == "reauthorize_or_permission_review",
                    "oauth_expires_at": oauth.get("expires_at")
                    or status.get("oauth_expires_at")
                    or health.get("oauth_expires_at")
                    or 0,
                    "last_probe_content_type": status.get(
                        "last_probe_content_type", ""
                    ),
                    "last_probe_response_format": status.get(
                        "last_probe_response_format", ""
                    ),
                    "last_probe_response_excerpt": status.get(
                        "last_probe_response_excerpt", ""
                    ),
                    "last_probe_parse_error": status.get("last_probe_parse_error", ""),
                    "last_probe_recognized_fields": status.get(
                        "last_probe_recognized_fields", {}
                    ),
                    "last_refresh_probe": status.get("last_refresh_probe", {}),
                    "last_workspace_probe": status.get("last_workspace_probe", {}),
                },
                "plan_type": plan_type,
                "plan_category": plan_category,
                "health": health,
            }
        )

    merged_rows.sort(
        key=lambda item: (
            0 if item.get("status", {}).get("usable") else 1,
            0 if item.get("enabled", True) else 1,
            -int(item.get("updated_at") or 0),
        )
    )
    return merged_rows


def _build_account_view_with_history(
    accounts: list[dict[str, Any]],
    health_rows: list[dict[str, Any]],
    action_history: list[dict[str, Any]] | None = None,
) -> Any:
    try:
        return _build_account_view(accounts, health_rows, action_history)
    except TypeError:
        return _build_account_view(accounts, health_rows)


def _build_alerts(accounts: list[dict[str, Any]]) -> dict[str, Any]:
    alert_types = {
        "invalid": [],
        "oauth_expired": [],
        "needs_refresh": [],
        "no_workspace": [],
        "workspace_creation_pending": [],
        "workspace_hydration_due": [],
        "probe_failures": [],
        "action_failures": [],
        "action_reauth_required": [],
        "action_rate_limited": [],
        "workspace_expand_warnings": [],
    }

    for account in accounts:
        status = (
            account.get("status") if isinstance(account.get("status"), dict) else {}
        )
        effective_state = str(status.get("effective_state") or "")
        alert_payload = {
            "account_id": account.get("id"),
            "user_id": account.get("user_id"),
            "user_email": account.get("user_email"),
            "plan_category": account.get("plan_category"),
            "last_error": status.get("last_error", ""),
            "last_refresh_error": status.get("last_refresh_error", ""),
            "workspace_state": status.get("workspace_state", ""),
            "workspace_expand_error": status.get("workspace_expand_error", ""),
            "workspace_expand_status_code": status.get("workspace_expand_status_code"),
        }

        if effective_state == "invalid" or str(status.get("state") or "") == "invalid":
            alert_types["invalid"].append(alert_payload)
        if effective_state == "oauth_expired" or bool(
            status.get("oauth_expired", False)
        ):
            alert_types["oauth_expired"].append(alert_payload)
        if effective_state == "needs_refresh" or bool(
            status.get("needs_refresh", False)
        ):
            alert_types["needs_refresh"].append(alert_payload)
        if effective_state == "no_workspace" or bool(status.get("no_workspace", False)):
            alert_types["no_workspace"].append(alert_payload)
        if (
            effective_state == "workspace_creation_pending"
            or str(status.get("workspace_state") or "") == "workspace_creation_pending"
        ):
            alert_types["workspace_creation_pending"].append(alert_payload)
            retry_after = int(status.get("workspace_hydration_retry_after") or 0)
            if retry_after <= int(time.time()):
                alert_types["workspace_hydration_due"].append(alert_payload)
        if bool(status.get("last_probe_action")) and not bool(
            status.get("last_probe_ok", True)
        ):
            alert_types["probe_failures"].append(
                {
                    **alert_payload,
                    "probe_failure_category": status.get(
                        "last_probe_failure_category", ""
                    ),
                    "probe_reason": status.get("last_probe_reason", ""),
                }
            )
        refresh_failure = str(status.get("last_refresh_failure_category") or "").strip()
        workspace_failure = str(
            status.get("last_workspace_failure_category") or ""
        ).strip()
        if refresh_failure and refresh_failure != "success":
            alert_types["action_failures"].append(
                {
                    **alert_payload,
                    "action_type": "refresh",
                    "failure_category": refresh_failure,
                }
            )
        if workspace_failure and workspace_failure != "success":
            alert_types["action_failures"].append(
                {
                    **alert_payload,
                    "action_type": "create_workspace",
                    "failure_category": workspace_failure,
                }
            )
        if bool(status.get("reauthorize_required", False)):
            alert_types["action_reauth_required"].append(
                {
                    **alert_payload,
                    "failure_category": refresh_failure or workspace_failure,
                }
            )
        if refresh_failure == "rate_limited" or workspace_failure == "rate_limited":
            alert_types["action_rate_limited"].append(
                {
                    **alert_payload,
                    "refresh_failure_category": refresh_failure,
                    "workspace_failure_category": workspace_failure,
                }
            )
        if str(status.get("workspace_expand_error") or "").strip():
            alert_types["workspace_expand_warnings"].append(alert_payload)

    summary = {key: len(value) for key, value in alert_types.items()}
    summary["total_alerts"] = sum(summary.values())
    return {"summary": summary, "items": alert_types}


def _append_operation_log(action: str, result: dict[str, Any]) -> None:
    store = get_config_store()
    config = store.get_config()
    logs = (
        config.get("operation_logs")
        if isinstance(config.get("operation_logs"), list)
        else []
    )
    entry = {
        "action": action,
        "timestamp": int(time.time()),
        "count": result.get("count", 0),
        "success_count": result.get("success_count", 0),
        "failed_count": result.get("failed_count", 0),
    }
    for key, value in result.items():
        if key not in entry:
            entry[key] = value
    logs.append(entry)
    config["operation_logs"] = logs[-50:]
    store.save_config(config)


def _append_probe_log(action: str, payload: dict[str, Any]) -> None:
    store = get_config_store()
    config = store.get_config()
    logs = (
        config.get("probe_logs") if isinstance(config.get("probe_logs"), list) else []
    )
    logs.append(
        {
            "action": action,
            "timestamp": int(time.time()),
            "payload": payload,
        }
    )
    config["probe_logs"] = logs[-100:]
    store.save_config(config)


def _append_action_history_log(action: str, payload: dict[str, Any]) -> None:
    store = get_config_store()
    config = store.get_config()
    logs = (
        config.get("action_history")
        if isinstance(config.get("action_history"), list)
        else []
    )
    summary = (
        payload.get("summary") if isinstance(payload.get("summary"), dict) else None
    )
    if summary is not None:
        if not summary.get("action"):
            summary["action"] = action
        if not summary.get("account_id") and payload.get("account_id"):
            summary["account_id"] = payload.get("account_id")
        if not summary.get("user_id") and payload.get("user_id"):
            summary["user_id"] = payload.get("user_id")
        if not summary.get("user_email") and payload.get("user_email"):
            summary["user_email"] = payload.get("user_email")
    logs.append(
        {
            "action": action,
            "timestamp": int(time.time()),
            "payload": payload,
        }
    )
    config["action_history"] = logs[-100:]
    store.save_config(config)


def _summarize_action_payload(payload: dict[str, Any]) -> dict[str, Any]:
    recognized_fields = (
        payload.get("recognized_fields")
        if isinstance(payload.get("recognized_fields"), dict)
        else {}
    )
    reason = str(payload.get("reason") or "").strip()
    action = str(payload.get("action") or "").strip()
    raw_status_code = payload.get("status_code")
    try:
        status_code = int(raw_status_code) if raw_status_code is not None else None
    except (TypeError, ValueError):
        status_code = None
    state = str(payload.get("state") or "").strip().lower()
    category = str(payload.get("failure_category") or "").strip().lower()
    reason_lower = reason.lower()
    if not category and not bool(payload.get("ok", False)):
        if status_code in {401, 403}:
            category = "unauthorized" if status_code == 401 else "forbidden"
        elif (
            state == "invalid"
            or "unauthorized" in reason_lower
            or "token is invalid" in reason_lower
        ):
            category = "unauthorized"
        elif status_code == 404:
            category = "not_found"
        elif status_code is not None and status_code >= 500:
            category = "server_error"
        elif status_code == 429 or "429" in reason_lower:
            category = "rate_limited"
    reauthorize_required = bool(
        payload.get("reauthorize_required", False)
    ) or category in {
        "unauthorized",
        "forbidden",
    }
    retryable = category in {"rate_limited", "timeout", "server_error", "network_error"}
    if reauthorize_required:
        suggested_action = "reauthorize_account"
        remediation_message = "OAuth credentials are no longer accepted; run reauthorization before retrying."
    elif category == "rate_limited":
        suggested_action = "retry_later"
        remediation_message = (
            "Upstream rate limited the action; wait for cooldown before retrying."
        )
    elif category in {"timeout", "server_error", "network_error"}:
        suggested_action = "retry_after_inspection"
        remediation_message = "Transient upstream failure detected; retry after checking connectivity and request health."
    elif category in {"client_error", "not_found"}:
        suggested_action = "check_runtime_config"
        remediation_message = "Runtime request template or endpoint configuration likely needs correction."
    elif category == "success" or bool(payload.get("ok", False)):
        suggested_action = "none"
        remediation_message = "No remediation needed."
    elif "workspace" in action.lower():
        suggested_action = "check_workspace_template"
        remediation_message = "Workspace creation payload likely needs schema or template adjustments before retrying."
    else:
        suggested_action = "inspect_action_details"
        remediation_message = "Inspect action details and upstream payloads."
    return {
        "action": action,
        "ok": payload.get("ok"),
        "reason": reason,
        "account_id": payload.get("account_id", ""),
        "user_id": payload.get("user_id", ""),
        "user_email": payload.get("user_email", ""),
        "space_id": payload.get("space_id", ""),
        "failure_category": category,
        "status_code": status_code,
        "reauthorize_required": reauthorize_required,
        "retryable": retryable,
        "suggested_action": suggested_action,
        "remediation_message": remediation_message,
        "recognized_fields": recognized_fields,
    }


def _match_action_history_account(item: dict[str, Any], account_filter: str) -> bool:
    if not account_filter:
        return True
    payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    candidate_values = {
        str(payload.get("account_id") or "").strip().lower(),
        str(summary.get("account_id") or "").strip().lower(),
        str(payload.get("user_id") or "").strip().lower(),
        str(payload.get("user_email") or "").strip().lower(),
        str(summary.get("user_id") or "").strip().lower(),
        str(summary.get("user_email") or "").strip().lower(),
    }
    return account_filter in {value for value in candidate_values if value}


def _filter_action_history(
    action_history: list[dict[str, Any]], account_filter_raw: str | None
) -> list[dict[str, Any]]:
    account_filter = str(account_filter_raw or "").strip().lower()
    if not account_filter:
        return action_history
    return [
        item
        for item in action_history
        if isinstance(item, dict)
        and _match_action_history_account(item, account_filter)
    ]


def _classify_formal_action_outcome(
    action_type: str, result: dict[str, Any], recognized_fields: dict[str, Any]
) -> tuple[str, str, bool]:
    category = str(result.get("failure_category") or "").strip().lower()
    error_code = str(recognized_fields.get("error") or "").strip().lower()
    status_code = _safe_int(result.get("status_code")) or 0

    if action_type == "refresh":
        if error_code in {
            "invalid_grant",
            "invalid_refresh_token",
            "unauthorized_client",
            "invalid_client",
        } or category in {"unauthorized", "forbidden"}:
            return ("reauthorize_required", "invalid", True)
        if category == "rate_limited":
            return ("retry_later", "cooling", False)
        if category in {"timeout", "server_error", "network_error"}:
            return ("retry_later", "cooling", False)
        if category in {"client_error", "not_found"} and status_code >= 400:
            return ("config_error", "invalid", False)
        return ("unknown_error", "invalid", False)

    if action_type == "create_workspace":
        if category in {"unauthorized", "forbidden"}:
            return ("reauthorize_required", "invalid", True)
        if category == "rate_limited":
            return ("retry_later", "cooling", False)
        if category in {"timeout", "server_error", "network_error"}:
            return ("retry_later", "cooling", False)
        if category in {"client_error", "not_found"} and status_code >= 400:
            return ("config_error", "invalid", False)
        return ("workspace_create_failed", "invalid", False)

    return ("unknown_error", "invalid", False)


def _safe_int(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _hydrate_workspace_entries(
    existing_workspaces: Any,
    recognized_fields: dict[str, Any],
    current_space_id: str,
    current_space_view_id: str,
) -> list[dict[str, Any]]:
    hydrated: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    if isinstance(existing_workspaces, list):
        for item in existing_workspaces:
            if not isinstance(item, dict):
                continue
            workspace_id = str(item.get("id") or "").strip()
            if workspace_id and workspace_id not in seen_ids:
                seen_ids.add(workspace_id)
            hydrated.append(dict(item))

    candidate_ids: list[str] = []
    for key in ("workspace_ids", "space_ids"):
        value = recognized_fields.get(key)
        if isinstance(value, list):
            candidate_ids.extend(
                str(item).strip() for item in value if str(item).strip()
            )
    for key in ("workspace_id", "space_id", "created_space_id", "new_space_id"):
        value = str(recognized_fields.get(key) or "").strip()
        if value:
            candidate_ids.append(value)
    primary_workspace_id = next((item for item in candidate_ids if item), "")

    name = str(recognized_fields.get("workspace_name") or "").strip()
    slug = str(recognized_fields.get("workspace_slug") or "").strip()
    subscription_tier = str(recognized_fields.get("subscription_tier") or "").strip()
    recognized_view_id = str(recognized_fields.get("space_view_id") or "").strip()

    for workspace_id in candidate_ids:
        if not workspace_id:
            continue
        if workspace_id in seen_ids:
            for item in hydrated:
                if str(item.get("id") or "").strip() != workspace_id:
                    continue
                if name and not str(item.get("name") or "").strip():
                    item["name"] = name
                if slug and not str(item.get("slug") or "").strip():
                    item["slug"] = slug
                if (
                    subscription_tier
                    and not str(item.get("subscription_tier") or "").strip()
                ):
                    item["subscription_tier"] = subscription_tier
                if (
                    workspace_id == current_space_id
                    and current_space_view_id
                    and not str(item.get("space_view_id") or "").strip()
                ):
                    item["space_view_id"] = current_space_view_id
                elif (
                    workspace_id == primary_workspace_id
                    and recognized_view_id
                    and not str(item.get("space_view_id") or "").strip()
                ):
                    item["space_view_id"] = recognized_view_id
                elif (
                    workspace_id == current_space_id
                    and recognized_view_id
                    and not str(item.get("space_view_id") or "").strip()
                ):
                    item["space_view_id"] = recognized_view_id
            continue

        entry: dict[str, Any] = {"id": workspace_id}
        if name:
            entry["name"] = name
        if slug:
            entry["slug"] = slug
        if subscription_tier:
            entry["subscription_tier"] = subscription_tier
        if workspace_id == current_space_id:
            if current_space_view_id:
                entry["space_view_id"] = current_space_view_id
            elif recognized_view_id:
                entry["space_view_id"] = recognized_view_id
        elif workspace_id == primary_workspace_id and recognized_view_id:
            entry["space_view_id"] = recognized_view_id
        hydrated.append(entry)
        seen_ids.add(workspace_id)

    return hydrated


def _extract_workspace_candidate_ids(recognized_fields: dict[str, Any]) -> list[str]:
    workspace_ids: list[str] = []
    for key in ("workspace_ids", "space_ids"):
        value = recognized_fields.get(key)
        if isinstance(value, list):
            workspace_ids.extend(
                str(item).strip() for item in value if str(item).strip()
            )
    for key in ("workspace_id", "space_id", "created_space_id", "new_space_id"):
        value = str(recognized_fields.get(key) or "").strip()
        if value:
            workspace_ids.append(value)

    deduped_workspace_ids: list[str] = []
    seen_workspace_ids: set[str] = set()
    for item in workspace_ids:
        if item in seen_workspace_ids:
            continue
        seen_workspace_ids.add(item)
        deduped_workspace_ids.append(item)
    return deduped_workspace_ids


def _extract_workspace_transaction_ids(recognized_fields: dict[str, Any]) -> list[str]:
    transaction_ids: list[str] = []
    transaction_list = recognized_fields.get("transaction_ids")
    if isinstance(transaction_list, list):
        transaction_ids.extend(
            str(item).strip() for item in transaction_list if str(item).strip()
        )
    transaction_value = str(recognized_fields.get("transaction_id") or "").strip()
    if transaction_value:
        transaction_ids.append(transaction_value)
    return transaction_ids


def _apply_workspace_success_result(
    account: dict[str, Any],
    workspace: dict[str, Any],
    status: dict[str, Any],
    recognized_fields: dict[str, Any],
) -> None:
    current_space_id = str(account.get("space_id") or "").strip()
    current_space_view_id = str(account.get("space_view_id") or "").strip()
    workspace_candidate_ids = _extract_workspace_candidate_ids(recognized_fields)
    if workspace_candidate_ids:
        workspace["probe_workspace_candidates"] = workspace_candidate_ids

    transaction_ids = _extract_workspace_transaction_ids(recognized_fields)
    if transaction_ids:
        workspace["probe_transaction_ids"] = transaction_ids

    hydrated_workspaces = _hydrate_workspace_entries(
        workspace.get("workspaces"),
        recognized_fields,
        current_space_id,
        current_space_view_id,
    )
    if hydrated_workspaces:
        workspace["workspaces"] = hydrated_workspaces

    success_workspace_id = workspace_candidate_ids[0] if workspace_candidate_ids else ""
    if not success_workspace_id:
        workspace["state"] = "workspace_creation_pending"
        status["workspace_state"] = "workspace_creation_pending"
        status["last_workspace_error"] = ""
        return

    workspace["state"] = "ready"
    workspace["workspace_count"] = max(
        int(workspace.get("workspace_count") or 0),
        len(hydrated_workspaces) if hydrated_workspaces else 1,
    )
    workspace["last_created_workspace_id"] = success_workspace_id
    should_switch_workspace = bool(
        should_auto_select_workspace()
        or not current_space_id
        or current_space_id != success_workspace_id
    )
    if should_switch_workspace:
        account["space_id"] = success_workspace_id
        current_space_id = success_workspace_id
    if str(recognized_fields.get("space_view_id") or "").strip() and (
        should_switch_workspace or current_space_id == success_workspace_id
    ):
        account["space_view_id"] = str(
            recognized_fields.get("space_view_id") or ""
        ).strip()
        current_space_view_id = str(account.get("space_view_id") or "").strip()
    elif should_switch_workspace:
        account["space_view_id"] = ""
        current_space_view_id = ""
    if hydrated_workspaces:
        workspace["workspaces"] = _hydrate_workspace_entries(
            workspace.get("workspaces"),
            recognized_fields,
            current_space_id,
            current_space_view_id,
        )
    if str(recognized_fields.get("subscription_tier") or "").strip():
        workspace["subscription_tier"] = str(
            recognized_fields.get("subscription_tier") or ""
        ).strip()
    status["workspace_state"] = "ready"
    status["last_workspace_error"] = ""


def _write_workspace_action_result_to_account(
    account_id: str, action: str, result: dict[str, Any]
) -> None:
    store = get_config_store()
    accounts = store.get_accounts()
    updated = False
    for account in accounts:
        if str(account.get("id") or "") != str(account_id or ""):
            continue
        status = (
            account.get("status") if isinstance(account.get("status"), dict) else {}
        )
        workspace = (
            account.get("workspace")
            if isinstance(account.get("workspace"), dict)
            else {}
        )
        now_ts = int(time.time())
        status["last_workspace_check_at"] = now_ts
        status["last_workspace_action"] = str(result.get("action") or action)
        status["last_workspace_error"] = (
            "" if bool(result.get("ok", False)) else str(result.get("reason") or "")
        )

        recognized_fields = (
            result.get("recognized_fields")
            if isinstance(result.get("recognized_fields"), dict)
            else {}
        )
        workspaces = result.get("workspaces")
        if isinstance(workspaces, list) and workspaces:
            candidate_ids = [
                str(item.get("id") or "").strip()
                for item in workspaces
                if isinstance(item, dict) and str(item.get("id") or "").strip()
            ]
            if "workspace_ids" not in recognized_fields and candidate_ids:
                recognized_fields = {
                    **recognized_fields,
                    "workspace_ids": candidate_ids,
                }
            if (
                "workspace_id" not in recognized_fields
                and str(result.get("space_id") or "").strip()
            ):
                recognized_fields = {
                    **recognized_fields,
                    "workspace_id": str(result.get("space_id") or "").strip(),
                }
            current_selected = str(result.get("space_id") or "").strip()
            selected_workspace = next(
                (
                    item
                    for item in workspaces
                    if isinstance(item, dict)
                    and str(item.get("id") or "").strip() == current_selected
                ),
                None,
            )
            if isinstance(selected_workspace, dict):
                if (
                    "workspace_name" not in recognized_fields
                    and str(selected_workspace.get("name") or "").strip()
                ):
                    recognized_fields["workspace_name"] = str(
                        selected_workspace.get("name") or ""
                    ).strip()
                if (
                    "workspace_slug" not in recognized_fields
                    and str(selected_workspace.get("slug") or "").strip()
                ):
                    recognized_fields["workspace_slug"] = str(
                        selected_workspace.get("slug") or ""
                    ).strip()
                if (
                    "subscription_tier" not in recognized_fields
                    and str(selected_workspace.get("subscription_tier") or "").strip()
                ):
                    recognized_fields["subscription_tier"] = str(
                        selected_workspace.get("subscription_tier") or ""
                    ).strip()
                if (
                    "space_view_id" not in recognized_fields
                    and str(selected_workspace.get("space_view_id") or "").strip()
                ):
                    recognized_fields["space_view_id"] = str(
                        selected_workspace.get("space_view_id") or ""
                    ).strip()
            workspace["workspaces"] = [
                dict(item) for item in workspaces if isinstance(item, dict)
            ]
            workspace["workspace_count"] = max(
                int(workspace.get("workspace_count") or 0),
                len(workspace["workspaces"]),
            )

        if bool(result.get("ok", False)):
            _apply_workspace_success_result(
                account, workspace, status, recognized_fields
            )
            oauth = (
                account.get("oauth") if isinstance(account.get("oauth"), dict) else {}
            )
            if bool(str(oauth.get("access_token") or "").strip()) or bool(
                str(oauth.get("refresh_token") or "").strip()
            ):
                oauth["expired"] = False
                oauth["needs_refresh"] = False
                oauth["has_access_token"] = bool(
                    str(oauth.get("access_token") or "").strip()
                )
                oauth["has_refresh_token"] = bool(
                    str(oauth.get("refresh_token") or "").strip()
                )
                oauth["has_credentials"] = bool(
                    oauth.get("has_access_token") or oauth.get("has_refresh_token")
                )
                account["oauth"] = oauth
                status["oauth_expired"] = False
                status["needs_refresh"] = False
                status["needs_reauth"] = False
                status["reauthorize_required"] = False
                status["last_refresh_error"] = ""
        elif str(result.get("reason") or ""):
            outcome_label, pool_state, needs_reauth = _classify_formal_action_outcome(
                "create_workspace", result, recognized_fields
            )
            status["state"] = pool_state
            status["needs_reauth"] = needs_reauth
            status["reauthorize_required"] = needs_reauth
            if outcome_label == "retry_later":
                workspace["state"] = "workspace_creation_pending"
                status["workspace_state"] = "workspace_creation_pending"
            else:
                workspace["state"] = "workspace_creation_failed"
                status["workspace_state"] = "workspace_creation_failed"
            status["last_workspace_failure_category"] = str(
                result.get("failure_category") or outcome_label
            )

        account["workspace"] = workspace
        account["status"] = status
        updated = True
        break
    if updated:
        store.set_accounts(accounts)


def _write_refresh_action_result_to_account(
    account_id: str, action: str, result: dict[str, Any]
) -> None:
    store = get_config_store()
    accounts = store.get_accounts()
    updated = False
    for account in accounts:
        if str(account.get("id") or "") != str(account_id or ""):
            continue
        status = (
            account.get("status") if isinstance(account.get("status"), dict) else {}
        )
        oauth = account.get("oauth") if isinstance(account.get("oauth"), dict) else {}
        recognized_fields = (
            result.get("recognized_fields")
            if isinstance(result.get("recognized_fields"), dict)
            else {}
        )
        now_ts = int(time.time())
        previous_expires_at = _safe_int(oauth.get("expires_at"))

        status["last_refresh_at"] = now_ts
        status["last_refresh_action"] = str(result.get("action") or action)
        status["last_refresh_error"] = str(result.get("reason") or "")

        if "access_token" in recognized_fields:
            oauth["access_token"] = recognized_fields.get("access_token")
        if "refresh_token" in recognized_fields:
            oauth["refresh_token"] = recognized_fields.get("refresh_token")
        if "expires_in" in recognized_fields:
            expires_in = _safe_int(recognized_fields.get("expires_in"))
            oauth["expires_in"] = expires_in
            if expires_in is not None and expires_in >= 0:
                oauth["expires_at"] = now_ts + expires_in
        oauth_expires_at = _safe_int(oauth.get("expires_at"))
        if oauth_expires_at is None:
            oauth_expires_at = previous_expires_at
        if "scope" in recognized_fields:
            oauth["scope"] = recognized_fields.get("scope")
        if "token_type" in recognized_fields:
            oauth["token_type"] = recognized_fields.get("token_type")

        has_access_token = bool(str(oauth.get("access_token") or "").strip())
        has_refresh_token = bool(str(oauth.get("refresh_token") or "").strip())

        if bool(result.get("ok", False)) and has_access_token:
            oauth["expired"] = False
            oauth["needs_refresh"] = bool(
                oauth_expires_at is not None and oauth_expires_at - now_ts <= 600
            )
            oauth["has_access_token"] = True
            oauth["has_refresh_token"] = has_refresh_token
            oauth["has_credentials"] = True
            oauth["last_probe_error"] = ""
            oauth["last_probe_error_description"] = ""
            status["oauth_expired"] = False
            status["needs_refresh"] = bool(oauth.get("needs_refresh", False))
            status["needs_reauth"] = False
            status["reauthorize_required"] = False
            status["last_refresh_error"] = ""
            status["last_refresh_failure_category"] = "success"
        else:
            oauth["needs_refresh"] = bool(has_refresh_token)
            status["needs_refresh"] = bool(has_refresh_token)
            outcome_label, pool_state, needs_reauth = _classify_formal_action_outcome(
                "refresh", result, recognized_fields
            )
            status["state"] = pool_state
            status["last_refresh_failure_category"] = str(
                result.get("failure_category") or outcome_label
            )
            status["needs_reauth"] = needs_reauth
            status["reauthorize_required"] = needs_reauth
            if not needs_reauth:
                status["oauth_expired"] = bool(has_refresh_token)

        account["oauth"] = oauth
        account["status"] = status
        updated = True
        break
    if updated:
        store.set_accounts(accounts)


def _write_probe_result_to_account(
    account_id: str, action: str, result: dict[str, Any]
) -> None:
    store = get_config_store()
    accounts = store.get_accounts()
    updated = False
    for account in accounts:
        if str(account.get("id") or "") != str(account_id or ""):
            continue
        status = (
            account.get("status") if isinstance(account.get("status"), dict) else {}
        )
        recognized_fields = (
            result.get("recognized_fields")
            if isinstance(result.get("recognized_fields"), dict)
            else {}
        )
        response_json = result.get("response_json")
        status.update(
            {
                "last_probe_action": action,
                "last_probe_ok": bool(result.get("ok", False)),
                "last_probe_reason": str(result.get("reason") or ""),
                "last_probe_status_code": result.get("status_code"),
                "last_probe_failure_category": result.get("failure_category", ""),
                "probe_auth_error": result.get("failure_category")
                in {"unauthorized", "forbidden"},
                "probe_network_error": result.get("failure_category")
                == "network_error",
                "probe_server_error": result.get("failure_category") == "server_error",
                "probe_rate_limited": result.get("failure_category") == "rate_limited",
                "last_probe_content_type": str(result.get("content_type") or ""),
                "last_probe_response_format": str(result.get("response_format") or ""),
                "last_probe_response_excerpt": str(
                    result.get("response_excerpt") or ""
                ),
                "last_probe_parse_error": str(result.get("response_parse_error") or ""),
                "last_probe_response_length": int(result.get("response_length") or 0),
                "last_probe_recognized_fields": recognized_fields,
                "last_probe_response_json": response_json
                if isinstance(response_json, (dict, list))
                else None,
                "last_probe_at": int(time.time()),
            }
        )
        oauth = account.get("oauth") if isinstance(account.get("oauth"), dict) else {}
        workspace = (
            account.get("workspace")
            if isinstance(account.get("workspace"), dict)
            else {}
        )
        if action == "refresh_probe":
            refresh_info = (
                status.get("last_refresh_probe")
                if isinstance(status.get("last_refresh_probe"), dict)
                else {}
            )
            now_ts = int(time.time())
            previous_expires_at = _safe_int(oauth.get("expires_at"))
            refresh_info.update(
                {
                    "status_code": result.get("status_code"),
                    "content_type": str(result.get("content_type") or ""),
                    "response_format": str(result.get("response_format") or ""),
                    "response_excerpt": str(result.get("response_excerpt") or ""),
                    "parse_error": str(result.get("response_parse_error") or ""),
                    "recognized_fields": recognized_fields,
                    "updated_at": now_ts,
                }
            )
            status["last_refresh_probe"] = refresh_info
            status["last_refresh_at"] = now_ts
            status["last_refresh_action"] = str(result.get("action") or action)
            if "access_token" in recognized_fields:
                oauth["probe_access_token"] = recognized_fields.get("access_token")
                oauth["access_token"] = recognized_fields.get("access_token")
            if "refresh_token" in recognized_fields:
                oauth["probe_refresh_token"] = recognized_fields.get("refresh_token")
                oauth["refresh_token"] = recognized_fields.get("refresh_token")
            if "expires_in" in recognized_fields:
                expires_in = _safe_int(recognized_fields.get("expires_in"))
                oauth["probe_expires_in"] = recognized_fields.get("expires_in")
                oauth["expires_in"] = expires_in
                if expires_in is not None and expires_in >= 0:
                    oauth["expires_at"] = now_ts + expires_in
            oauth_expires_at = _safe_int(oauth.get("expires_at"))
            if oauth_expires_at is None:
                oauth_expires_at = previous_expires_at
            if recognized_fields.get("error"):
                oauth["last_probe_error"] = recognized_fields.get("error")
            if recognized_fields.get("error_description"):
                oauth["last_probe_error_description"] = recognized_fields.get(
                    "error_description"
                )
            if recognized_fields.get("scope"):
                oauth["scope"] = recognized_fields.get("scope")
            if recognized_fields.get("token_type"):
                oauth["token_type"] = recognized_fields.get("token_type")
            has_access_token = bool(str(oauth.get("access_token") or "").strip())
            has_refresh_token = bool(str(oauth.get("refresh_token") or "").strip())
            if bool(result.get("ok", False)) and has_access_token:
                oauth["expired"] = False
                oauth["needs_refresh"] = bool(
                    oauth_expires_at is not None and oauth_expires_at - now_ts <= 600
                )
                oauth["has_access_token"] = True
                oauth["has_refresh_token"] = has_refresh_token
                oauth["has_credentials"] = True
                oauth["last_probe_error"] = ""
                oauth["last_probe_error_description"] = ""
                status["oauth_expired"] = False
                status["needs_refresh"] = bool(oauth.get("needs_refresh", False))
                status["needs_reauth"] = False
                status["reauthorize_required"] = False
                status["last_refresh_error"] = ""
            elif recognized_fields.get("error") or recognized_fields.get(
                "error_description"
            ):
                error_message = str(
                    recognized_fields.get("error_description")
                    or recognized_fields.get("error")
                    or result.get("reason")
                    or ""
                )
                status["last_refresh_error"] = error_message
                oauth["needs_refresh"] = True
                status["needs_refresh"] = True
                if str(recognized_fields.get("error") or "").strip().lower() in {
                    "invalid_grant",
                    "invalid_refresh_token",
                    "unauthorized_client",
                    "invalid_client",
                }:
                    status["needs_reauth"] = True
                    status["reauthorize_required"] = True
            account["oauth"] = oauth
        if action == "workspace_probe":
            workspace_info = (
                status.get("last_workspace_probe")
                if isinstance(status.get("last_workspace_probe"), dict)
                else {}
            )
            now_ts = int(time.time())
            workspace_info.update(
                {
                    "status_code": result.get("status_code"),
                    "content_type": str(result.get("content_type") or ""),
                    "response_format": str(result.get("response_format") or ""),
                    "response_excerpt": str(result.get("response_excerpt") or ""),
                    "parse_error": str(result.get("response_parse_error") or ""),
                    "recognized_fields": recognized_fields,
                    "updated_at": now_ts,
                }
            )
            status["last_workspace_probe"] = workspace_info
            status["last_workspace_check_at"] = now_ts
            status["last_workspace_action"] = str(result.get("action") or action)
            workspace["last_probe_response"] = workspace_info
            for key in (
                "workspace_id",
                "workspace_ids",
                "space_id",
                "space_ids",
                "created_space_id",
                "new_space_id",
                "transaction_id",
                "transaction_ids",
                "workspace_name",
                "workspace_slug",
                "subscription_tier",
                "space_view_id",
            ):
                if key in recognized_fields:
                    workspace[f"probe_{key}"] = recognized_fields.get(key)
            if bool(result.get("ok", False)):
                _apply_workspace_success_result(
                    account, workspace, status, recognized_fields
                )
            elif recognized_fields.get("error") or recognized_fields.get(
                "error_description"
            ):
                status["workspace_state"] = "workspace_creation_failed"
                status["last_workspace_error"] = str(
                    recognized_fields.get("error_description")
                    or recognized_fields.get("error")
                    or result.get("reason")
                    or ""
                )
                workspace["state"] = "workspace_creation_failed"
            elif str(result.get("failure_category") or "") != "success":
                status["last_workspace_error"] = str(result.get("reason") or "")
                workspace.setdefault("state", "workspace_creation_pending")
                status["workspace_state"] = str(
                    workspace.get("state") or "workspace_creation_pending"
                )
            account["workspace"] = workspace
        account["status"] = status
        updated = True
        break
    if updated:
        store.set_accounts(accounts)


def _summarize_probe_payload(payload: dict[str, Any]) -> dict[str, Any]:
    result = (
        payload.get("result") if isinstance(payload.get("result"), dict) else payload
    )
    request_template = (
        result.get("request_template")
        if isinstance(result.get("request_template"), dict)
        else {}
    )
    return {
        "action": result.get("action", payload.get("action", "")),
        "ok": result.get("ok", payload.get("ok")),
        "probed": result.get("probed", False),
        "reason": result.get("reason", ""),
        "mode": request_template.get("mode", ""),
        "url": request_template.get("url", ""),
        "status_code": result.get("status_code"),
        "content_type": result.get("content_type", ""),
        "response_format": result.get("response_format", ""),
        "parse_error": result.get("response_parse_error", ""),
        "recognized_fields": result.get("recognized_fields", {}),
    }


def _build_refresh_diagnostics(accounts: list[dict[str, Any]]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    summary = {
        "total": 0,
        "refresh_ready": 0,
        "manual_reauthorize": 0,
        "expired": 0,
        "needs_refresh": 0,
    }

    for account in accounts:
        oauth = account.get("oauth") if isinstance(account.get("oauth"), dict) else {}
        status = (
            account.get("status") if isinstance(account.get("status"), dict) else {}
        )
        has_refresh_token = bool(
            oauth.get("has_refresh_token")
            or str(oauth.get("refresh_token") or "").strip()
        )
        expired = bool(
            oauth.get("expired", False) or status.get("oauth_expired", False)
        )
        needs_refresh = bool(
            oauth.get("needs_refresh", False) or status.get("needs_refresh", False)
        )
        reauthorize_required = bool(
            status.get("reauthorize_required", False)
            or status.get("needs_reauth", False)
        )
        if has_refresh_token:
            readiness = "refresh_ready"
        elif reauthorize_required or expired:
            readiness = "manual_reauthorize"
        elif needs_refresh:
            readiness = "manual_reauthorize"
        else:
            readiness = "healthy"

        summary["total"] += 1
        if readiness == "refresh_ready":
            summary["refresh_ready"] += 1
        if readiness == "manual_reauthorize":
            summary["manual_reauthorize"] += 1
        if expired:
            summary["expired"] += 1
        if needs_refresh:
            summary["needs_refresh"] += 1

        rows.append(
            {
                "account_id": account.get("id"),
                "user_id": account.get("user_id"),
                "user_email": account.get("user_email"),
                "plan_category": account.get("plan_category"),
                "readiness": readiness,
                "expired": expired,
                "needs_refresh": needs_refresh,
                "has_refresh_token": has_refresh_token,
                "reauthorize_required": reauthorize_required,
                "last_refresh_action": status.get("last_refresh_action", ""),
                "last_refresh_error": status.get("last_refresh_error", ""),
            }
        )

    return {"summary": summary, "accounts": rows}


def _build_workspace_diagnostics(accounts: list[dict[str, Any]]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    summary = {
        "total": 0,
        "ready": 0,
        "missing": 0,
        "pending": 0,
        "unimplemented": 0,
        "errors": 0,
    }

    for account in accounts:
        workspace = (
            account.get("workspace")
            if isinstance(account.get("workspace"), dict)
            else {}
        )
        status = (
            account.get("status") if isinstance(account.get("status"), dict) else {}
        )
        workspace_state = str(
            workspace.get("state") or status.get("workspace_state") or "missing"
        )
        if workspace_state == "ready":
            summary["ready"] += 1
        elif workspace_state == "missing":
            summary["missing"] += 1
        elif workspace_state == "workspace_creation_pending":
            summary["pending"] += 1
        elif workspace_state in {
            "workspace_creation_unimplemented",
            "workspace_creation_unverified",
        }:
            summary["unimplemented"] += 1
        if status.get("last_workspace_error"):
            summary["errors"] += 1

        summary["total"] += 1
        rows.append(
            {
                "account_id": account.get("id"),
                "user_id": account.get("user_id"),
                "user_email": account.get("user_email"),
                "plan_category": account.get("plan_category"),
                "workspace_state": workspace_state,
                "workspace_count": workspace.get("workspace_count", 0),
                "last_workspace_action": status.get("last_workspace_action", ""),
                "last_workspace_error": status.get("last_workspace_error", ""),
                "workspace_poll_count": status.get("workspace_poll_count", 0),
            }
        )

    return {"summary": summary, "accounts": rows}


def _build_generic_refresh_request_template() -> dict[str, Any]:
    return {
        "method": "POST",
        "url": "https://www.notion.so/api/v3/oauth/token",
        "headers": {
            "Content-Type": "application/json",
            "Authorization": "Bearer ***access-token-if-required***",
        },
        "body": {
            "grant_type": "refresh_token",
            "refresh_token": "***redacted***",
            "client_id": "***client-id-if-required***",
            "client_secret": "***client-secret-if-required***",
        },
        "field_hints": {
            "headers.Authorization": "Optional bearer header if the upstream endpoint still expects an authenticated browser session in addition to refresh credentials.",
            "body.refresh_token": "Secret. Keep fully masked in screenshots, logs, exports, and shared debug payloads.",
            "body.client_id": "Usually safe to share internally, but still treat as config and avoid publishing in public bug reports.",
            "body.client_secret": "Secret. Never expose in frontend logs or copy/paste snippets.",
        },
        "redactions": {
            "headers": ["Authorization"],
            "body": ["refresh_token", "client_secret"],
        },
        "notes": [
            "This is a dry-run template only.",
            "Exact Notion web refresh endpoint and auth requirements still need verification.",
            "Replace placeholders after upstream reverse engineering.",
            "Fields listed under redactions must stay masked in any exported or shared template JSON.",
            "field_hints explains which values are safe diagnostics versus secrets that must never leave the runtime config or account store.",
        ],
        "provider": "notion-web",
    }


def _build_generic_workspace_request_template(template_space_id: str) -> dict[str, Any]:
    return {
        "method": "POST",
        "url": "https://www.notion.so/api/v3/saveTransactions",
        "headers": {
            "Content-Type": "application/json",
            "x-notion-active-user-header": "***user-id***",
        },
        "body": {
            "operation": "create_workspace",
            "template_space_id": template_space_id or None,
            "source_space_id": "***source-space-id***",
            "user_id": "***user-id***",
            "space_view_id": "***space-view-id-if-available***",
            "transactions": [
                {
                    "id": "***workspace-creation-transaction-id***",
                    "space_id": "***source-space-id***",
                    "debug": "replace with real Notion transaction payload",
                }
            ],
        },
        "field_hints": {
            "headers.x-notion-active-user-header": "User identifier header. Safe to inspect internally, but avoid exposing in public examples.",
            "body.template_space_id": "Optional source template workspace. Safe to share inside the admin team if the template is not sensitive.",
            "body.source_space_id": "Current workspace context used to seed creation metadata.",
            "body.space_view_id": "Useful for replay/debugging when the source workspace has multiple views.",
            "body.transactions[0].id": "Transaction/request correlation id. Safe to regenerate per replay.",
            "body.transactions[0].operations": "Nested operations should mirror the eventual saveTransactions create/set structure for realistic replay.",
            "body.transactions[0].operations[1]": "Optional secondary operation can model ownership or permission bootstrap records for the new workspace.",
            "body.transactions[0].debug": "Placeholder marker only; replace with a verified Notion transaction before sending live traffic.",
        },
        "redactions": {
            "headers": [],
            "body": [],
        },
        "notes": [
            "This is a dry-run or preparation template only.",
            "Replace placeholders with a verified upstream Notion transaction payload.",
            "If you share this template externally, review template_space_id, source_space_id, and user_id for tenant sensitivity first.",
            "field_hints marks which fields are diagnostic context, placeholders, or identifiers that may need tenant-specific masking.",
        ],
        "provider": "notion-web",
        "template_space_id": template_space_id or None,
    }


@router.post("/admin/login")
async def admin_login(request: Request, payload: AdminLoginRequest):
    auth_status = _build_admin_auth_status(request)
    if not auth_status["configured"]:
        raise HTTPException(status_code=503, detail="Admin login is not configured")
    username = str(payload.username or "").strip()
    password = str(payload.password or "")
    if not verify_admin_credentials(username, password):
        raise HTTPException(status_code=401, detail="Invalid admin credentials")
    session = _create_admin_session(request, username)
    return {
        "ok": True,
        "username": auth_status["username"],
        "session_token": session["token"],
        "must_change_password": auth_status["must_change_password"],
        "initialized_from_default": auth_status["initialized_from_default"],
        "session_expires_at": session["expires_at"],
    }


@router.post("/admin/change-password")
async def admin_change_password(
    request: Request,
    payload: AdminChangePasswordRequest,
    x_admin_session: str | None = Header(default=None, alias="X-Admin-Session"),
):
    session = _ensure_admin(
        request, x_admin_session, allow_password_change_required=True
    )
    auth_status = _build_admin_auth_status(request)
    current_password = str(payload.current_password or "")
    if not verify_admin_credentials(auth_status["username"], current_password):
        raise HTTPException(status_code=401, detail="Current password is incorrect")
    new_username = str(payload.new_username or auth_status["username"]).strip()
    if not new_username:
        raise HTTPException(status_code=400, detail="Username cannot be empty")
    raw_new_password = payload.new_password
    new_password = str(raw_new_password or "")
    if raw_new_password is not None and new_password and len(new_password) < 8:
        raise HTTPException(
            status_code=400, detail="New password must be at least 8 characters"
        )
    current_admin_auth = get_admin_auth()
    updated_auth = update_admin_credentials(
        username=new_username,
        password=new_password or str(current_password or ""),
        must_change_password=False,
        initialized_from_default=bool(
            current_admin_auth.get("initialized_from_default", False)
            and not new_password
        ),
    )
    request.app.state.admin_auth = updated_auth
    getattr(request.app.state, "admin_sessions", {}).clear()
    new_session = _create_admin_session(request, updated_auth["username"])
    return {
        "ok": True,
        "username": updated_auth["username"],
        "session_token": new_session["token"],
        "must_change_password": False,
        "initialized_from_default": False,
        "session_expires_at": new_session["expires_at"],
        "message": "Admin credentials updated successfully.",
    }


@router.post("/chat/login")
async def chat_login(request: Request, payload: ChatLoginRequest):
    chat_status = _build_chat_auth_status(request)
    if not chat_status["enabled"]:
        return {
            "ok": True,
            "session_token": "",
            "session_expires_at": 0,
            "enabled": False,
        }
    password = str(payload.password or "")
    if not verify_chat_password(password):
        raise HTTPException(status_code=401, detail="Invalid chat password")
    session = _create_chat_session(request)
    return {
        "ok": True,
        "enabled": True,
        "session_token": session["token"],
        "session_expires_at": session["expires_at"],
    }


@router.get("/chat/access")
async def get_chat_access(request: Request):
    chat_status = _build_chat_auth_status(request)
    return {
        "ok": True,
        "chat_enabled": bool(
            get_config_store().get_config().get("chat_enabled", False)
        ),
        "password_enabled": chat_status["enabled"],
        "configured": chat_status["configured"],
    }


@router.get("/admin/config")
async def get_admin_config(
    request: Request,
    x_admin_session: str | None = Header(default=None, alias="X-Admin-Session"),
):
    _ensure_admin(request, x_admin_session)
    auth_status = _build_admin_auth_status(request)
    store = get_config_store()
    config = store.get_config()
    register_automation, runtime_panel, account_view, proxy_health_payload = (
        _build_runtime_automation_payload(request, store, config)
    )
    chat_auth = get_chat_auth()
    settings = _redact_runtime_settings(
        {
            "app_mode": config.get("app_mode", get_app_mode()),
            "api_key": config.get("api_key", ""),
            "allowed_origins": config.get("allowed_origins", []),
            "siliconflow_api_key": config.get("siliconflow_api_key", ""),
            "upstream_proxy": config.get("upstream_proxy", ""),
            "upstream_http_proxy": config.get("upstream_http_proxy", ""),
            "upstream_https_proxy": config.get("upstream_https_proxy", ""),
            "upstream_socks5_proxy": config.get("upstream_socks5_proxy", ""),
            "upstream_proxy_mode": config.get("upstream_proxy_mode", "direct"),
            "upstream_warp_enabled": config.get("upstream_warp_enabled", False),
            "upstream_warp_proxy": config.get("upstream_warp_proxy", ""),
            "auto_create_workspace": config.get("auto_create_workspace", False),
            "auto_select_workspace": config.get("auto_select_workspace", True),
            "workspace_create_dry_run": config.get("workspace_create_dry_run", True),
            "workspace_creation_template_space_id": config.get(
                "workspace_creation_template_space_id", ""
            ),
            "account_probe_interval_seconds": config.get(
                "account_probe_interval_seconds", 300
            ),
            "auto_register_enabled": config.get("auto_register_enabled", False),
            "auto_register_idle_only": config.get("auto_register_idle_only", True),
            "auto_register_interval_seconds": config.get(
                "auto_register_interval_seconds", 1800
            ),
            "auto_register_min_spacing_seconds": config.get(
                "auto_register_min_spacing_seconds", 900
            ),
            "auto_register_busy_cooldown_seconds": config.get(
                "auto_register_busy_cooldown_seconds", 1200
            ),
            "auto_register_batch_size": config.get("auto_register_batch_size", 1),
            "auto_register_headless": config.get("auto_register_headless", False),
            "auto_register_use_api": config.get("auto_register_use_api", True),
            "auto_register_mail_provider": config.get(
                "auto_register_mail_provider", "freemail"
            ),
            "auto_register_mail_base_url": config.get(
                "auto_register_mail_base_url", ""
            ),
            "auto_register_mail_api_key": config.get("auto_register_mail_api_key", ""),
            "auto_register_domain": config.get("auto_register_domain", ""),
            "refresh_execution_mode": config.get("refresh_execution_mode", "manual"),
            "refresh_request_url": config.get("refresh_request_url", ""),
            "refresh_client_id": config.get("refresh_client_id", ""),
            "refresh_client_secret": config.get("refresh_client_secret", ""),
            "workspace_execution_mode": config.get(
                "workspace_execution_mode", "manual"
            ),
            "workspace_request_url": config.get("workspace_request_url", ""),
            "allow_real_probe_requests": config.get("allow_real_probe_requests", False),
            "chat_enabled": config.get("chat_enabled", False),
            "media_public_base_url": config.get("media_public_base_url", ""),
            "media_storage_path": config.get("media_storage_path", ""),
            "chat_password_enabled": bool(chat_auth.get("enabled", False)),
            "chat_password": "********"
            if str(chat_auth.get("password_hash") or "").strip()
            else "",
            "has_chat_password": bool(
                str(chat_auth.get("password_hash") or "").strip()
            ),
        }
    )
    return {
        "ok": True,
        "redaction_mode": "safe",
        "settings_view_mode": "safe",
        "accounts_view_mode": "safe",
        "settings": settings,
        "admin_auth": auth_status,
        "storage": {
            "runtime_config_path": str(CONFIG_PATH),
            "accounts_path": str(ACCOUNTS_PATH),
        },
        "proxy_health": proxy_health_payload["summary"],
        "proxy_health_checks": proxy_health_payload["checks"],
        "register_automation": register_automation,
        "register_automation_guidance": _build_register_automation_guidance(
            register_automation
        ),
        "runtime_operations_panel": runtime_panel,
        "accounts": _redact_account_list(account_view),
        "health": [
            _redact_health_payload(item)
            for item in request.app.state.account_pool.get_detailed_status()
        ],
    }


@router.get("/admin/config/proxy-health")
async def get_proxy_health(
    request: Request,
    x_admin_session: str | None = Header(default=None, alias="X-Admin-Session"),
):
    _ensure_admin(request, x_admin_session)
    config = get_config_store().get_config()
    payload = get_proxy_health_payload(config)
    return {
        "ok": True,
        "response_mode": "status_summary",
        "contains_secrets": False,
        **payload,
    }


@router.post("/admin/register/auto-trigger")
async def trigger_auto_register_now(
    request: Request,
    x_admin_session: str | None = Header(default=None, alias="X-Admin-Session"),
):
    _ensure_admin(request, x_admin_session)
    return maybe_start_auto_register(request)


@router.get("/admin/register/auto-status")
async def get_auto_register_status(
    request: Request,
    x_admin_session: str | None = Header(default=None, alias="X-Admin-Session"),
):
    _ensure_admin(request, x_admin_session)
    store = get_config_store()
    config = store.get_config()
    automation, runtime_panel, _account_view, proxy_health_payload = (
        _build_runtime_automation_payload(request, store, config)
    )
    return {
        "ok": True,
        "response_mode": "status_summary",
        "contains_secrets": False,
        "enabled": bool(config.get("auto_register_enabled", False)),
        "settings": {
            "idle_only": bool(config.get("auto_register_idle_only", True)),
            "interval_seconds": int(config.get("auto_register_interval_seconds", 1800)),
            "min_spacing_seconds": int(
                config.get("auto_register_min_spacing_seconds", 900)
            ),
            "busy_cooldown_seconds": int(
                config.get("auto_register_busy_cooldown_seconds", 1200)
            ),
            "batch_size": int(config.get("auto_register_batch_size", 1)),
            "headless": bool(config.get("auto_register_headless", False)),
            "use_api": bool(config.get("auto_register_use_api", True)),
            "mail_provider": str(config.get("auto_register_mail_provider", "freemail")),
        },
        "automation": automation,
        "guidance": _build_register_automation_guidance(automation),
        "proxy_health": proxy_health_payload,
        "runtime_operations_panel": runtime_panel,
    }


@router.put("/admin/config/settings")
async def update_runtime_settings(
    request: Request,
    payload: RuntimeSettingsRequest,
    x_admin_session: str | None = Header(default=None, alias="X-Admin-Session"),
):
    _ensure_admin(request, x_admin_session)
    store = get_config_store()
    config = store.get_config()
    try:
        refresh_request_url = validate_runtime_request_url(
            payload.refresh_request_url, "refresh_request_url"
        )
        workspace_request_url = validate_runtime_request_url(
            payload.workspace_request_url, "workspace_request_url"
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    updates = {
        "app_mode": payload.app_mode,
        "allowed_origins": payload.allowed_origins,
        "upstream_proxy": payload.upstream_proxy,
        "upstream_http_proxy": payload.upstream_http_proxy,
        "upstream_https_proxy": payload.upstream_https_proxy,
        "upstream_socks5_proxy": payload.upstream_socks5_proxy,
        "upstream_proxy_mode": payload.upstream_proxy_mode,
        "upstream_warp_enabled": payload.upstream_warp_enabled,
        "upstream_warp_proxy": payload.upstream_warp_proxy,
        "auto_create_workspace": payload.auto_create_workspace,
        "auto_select_workspace": payload.auto_select_workspace,
        "workspace_create_dry_run": payload.workspace_create_dry_run,
        "workspace_creation_template_space_id": payload.workspace_creation_template_space_id,
        "account_probe_interval_seconds": payload.account_probe_interval_seconds,
        "auto_register_enabled": payload.auto_register_enabled,
        "auto_register_idle_only": payload.auto_register_idle_only,
        "auto_register_interval_seconds": payload.auto_register_interval_seconds,
        "auto_register_min_spacing_seconds": payload.auto_register_min_spacing_seconds,
        "auto_register_busy_cooldown_seconds": payload.auto_register_busy_cooldown_seconds,
        "auto_register_batch_size": payload.auto_register_batch_size,
        "auto_register_headless": payload.auto_register_headless,
        "auto_register_use_api": payload.auto_register_use_api,
        "auto_register_mail_provider": payload.auto_register_mail_provider,
        "auto_register_mail_base_url": payload.auto_register_mail_base_url,
        "auto_register_domain": payload.auto_register_domain,
        "refresh_execution_mode": payload.refresh_execution_mode,
        "refresh_request_url": refresh_request_url,
        "refresh_client_id": payload.refresh_client_id,
        "workspace_execution_mode": payload.workspace_execution_mode,
        "workspace_request_url": workspace_request_url,
        "allow_real_probe_requests": payload.allow_real_probe_requests,
        "chat_enabled": payload.chat_enabled,
        "media_public_base_url": str(payload.media_public_base_url or "").strip(),
        "media_storage_path": str(payload.media_storage_path or "").strip(),
    }
    if payload.api_key is not None:
        updates["api_key"] = payload.api_key
    if payload.siliconflow_api_key is not None:
        updates["siliconflow_api_key"] = payload.siliconflow_api_key
    if payload.auto_register_mail_api_key is not None:
        updates["auto_register_mail_api_key"] = payload.auto_register_mail_api_key
    if payload.refresh_client_secret is not None:
        updates["refresh_client_secret"] = payload.refresh_client_secret
    config.update(updates)
    saved = store.save_config(config)
    if payload.chat_password is not None:
        chat_password_value = str(payload.chat_password or "").strip()
        has_existing_chat_password = bool(
            str(get_chat_auth().get("password_hash") or "").strip()
        )
        if chat_password_value and chat_password_value != "********":
            update_chat_password(
                password=chat_password_value, enabled=payload.chat_password_enabled
            )
        elif not chat_password_value:
            update_chat_password(password="", enabled=False)
        elif chat_password_value == "********" and has_existing_chat_password:
            store.update_config(
                {
                    "chat_auth": {
                        **get_chat_auth(),
                        "enabled": bool(payload.chat_password_enabled),
                    }
                }
            )
    elif bool(payload.chat_password_enabled) != bool(
        get_chat_auth().get("enabled", False)
    ):
        store.update_config(
            {
                "chat_auth": {
                    **get_chat_auth(),
                    "enabled": bool(payload.chat_password_enabled),
                }
            }
        )
    saved = store.get_config()
    _rebuild_pool(request)
    return {
        "ok": True,
        "redaction_mode": "safe",
        "settings_view_mode": "safe",
        "settings": _redact_runtime_settings(saved),
    }


@router.post("/admin/accounts")
async def upsert_account(
    request: Request,
    payload: AccountUpsertRequest,
    x_admin_session: str | None = Header(default=None, alias="X-Admin-Session"),
):
    _ensure_admin(request, x_admin_session)
    saved = get_config_store().upsert_account(payload.model_dump(exclude_none=True))
    _rebuild_pool(request)
    return {"ok": True, "account": _redact_account_payload(saved)}


@router.patch("/admin/accounts/{account_id}")
async def patch_account(
    account_id: str,
    request: Request,
    payload: AccountPatchRequest,
    x_admin_session: str | None = Header(default=None, alias="X-Admin-Session"),
):
    _ensure_admin(request, x_admin_session)
    store = get_config_store()
    accounts = store.get_accounts()
    target = next(
        (account for account in accounts if account.get("id") == account_id), None
    )
    if target is None:
        raise HTTPException(status_code=404, detail="Account not found")
    updates = payload.model_dump(exclude_none=True)
    target.update(updates)
    saved = store.upsert_account(target)
    _rebuild_pool(request)
    return {"ok": True, "account": _redact_account_payload(saved)}


@router.delete("/admin/accounts/{account_id}")
async def delete_account(
    account_id: str,
    request: Request,
    x_admin_session: str | None = Header(default=None, alias="X-Admin-Session"),
):
    _ensure_admin(request, x_admin_session)
    deleted = get_config_store().delete_account(account_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Account not found")
    _rebuild_pool(request)
    return {"ok": True, "deleted": True, "account_id": account_id}


@router.post("/admin/accounts/import")
async def import_accounts(
    request: Request,
    payload: AccountImportRequest,
    x_admin_session: str | None = Header(default=None, alias="X-Admin-Session"),
):
    _ensure_admin(request, x_admin_session)
    store = get_config_store()
    saved_accounts = []
    for item in payload.accounts:
        saved_accounts.append(store.upsert_account(item.model_dump(exclude_none=True)))
    _rebuild_pool(request)
    return {
        "ok": True,
        "count": len(saved_accounts),
        "accounts": _redact_account_list(saved_accounts),
    }


@router.post("/admin/accounts/replace")
async def replace_accounts(
    request: Request,
    payload: AccountReplaceRequest,
    x_admin_session: str | None = Header(default=None, alias="X-Admin-Session"),
):
    _ensure_admin(request, x_admin_session)
    store = get_config_store()
    saved = store.set_accounts(
        [item.model_dump(exclude_none=True) for item in payload.accounts]
    )
    _rebuild_pool(request)
    return {
        "ok": True,
        "count": len(saved.get("accounts", [])),
        "accounts": _redact_account_list(saved.get("accounts", [])),
    }


@router.get("/admin/accounts/export")
async def export_accounts(
    request: Request,
    raw: bool = Query(default=False),
    x_admin_session: str | None = Header(default=None, alias="X-Admin-Session"),
):
    _ensure_admin(request, x_admin_session)
    store = get_config_store()
    config = store.get_config()
    accounts = config.get("accounts", [])
    export_mode = "raw" if raw else "safe"
    _append_operation_log(
        "accounts_export",
        {
            "count": len(accounts),
            "success_count": len(accounts),
            "failed_count": 0,
            "export_mode": export_mode,
        },
    )
    return {
        "ok": True,
        "count": len(accounts),
        "accounts": accounts if raw else _redact_account_list(accounts),
        "export_mode": export_mode,
        "view_mode": export_mode,
        "storage": {
            "accounts_path": str(ACCOUNTS_PATH),
        },
    }


@router.post("/admin/accounts/oauth")
async def import_oauth_account(
    request: Request,
    payload: OAuthAccountImportRequest,
    x_admin_session: str | None = Header(default=None, alias="X-Admin-Session"),
):
    _ensure_admin(request, x_admin_session)
    saved = get_config_store().upsert_account(_build_saved_oauth_account(payload))
    _rebuild_pool(request)
    return {"ok": True, "account": _redact_account_payload(saved)}


@router.post("/admin/oauth/callback")
async def oauth_callback_import(
    request: Request,
    payload: OAuthCallbackPayload,
    x_admin_session: str | None = Header(default=None, alias="X-Admin-Session"),
):
    _ensure_admin(request, x_admin_session)
    saved = get_config_store().upsert_account(_build_saved_oauth_account(payload))
    _rebuild_pool(request)
    return {
        "ok": True,
        "account": _redact_account_payload(saved),
        "source": "oauth_callback",
    }


@router.get("/admin/oauth/callback")
async def oauth_callback_redirect(request: Request):
    fallback_redirect_uri = _default_local_redirect_uri(request)
    redirect_uri = _normalize_callback_redirect_uri(
        request.query_params.get("redirect_uri") or "",
        fallback_redirect_uri,
    )
    state = str(request.query_params.get("state") or "").strip()
    if state:
        callback_payload = {
            "token_v2": str(request.query_params.get("token_v2") or "").strip(),
            "user_id": str(request.query_params.get("user_id") or "").strip(),
            "space_id": str(request.query_params.get("space_id") or "").strip(),
            "user_email": str(
                request.query_params.get("user_email")
                or request.query_params.get("email")
                or ""
            ).strip(),
            "access_token": str(request.query_params.get("access_token") or "").strip(),
            "refresh_token": str(
                request.query_params.get("refresh_token") or ""
            ).strip(),
            "expires_at": str(request.query_params.get("expires_at") or "").strip(),
            "state": state,
            "provider": str(
                request.query_params.get("provider") or "notion-web"
            ).strip()
            or "notion-web",
            "source": "oauth_callback_redirect",
        }
        session = _get_oauth_callback_session(request, state)
        if session:
            _store_oauth_callback_session_payload(
                request, state=state, payload=callback_payload
            )
    redirect_url = _build_callback_redirect_url(
        redirect_uri,
        {key: value for key, value in request.query_params.items()},
    )
    return RedirectResponse(url=redirect_url, status_code=307)


@router.post("/admin/oauth/start")
async def start_oauth(
    request: Request,
    payload: OAuthStartRequest,
    x_admin_session: str | None = Header(default=None, alias="X-Admin-Session"),
):
    _ensure_admin(request, x_admin_session)
    redirect_uri = payload.redirect_uri.strip() or _default_local_redirect_uri(request)
    state = payload.state.strip() or secrets.token_urlsafe(16)
    _register_oauth_callback_session(
        request,
        provider=payload.provider,
        redirect_uri=redirect_uri,
        state=state,
    )
    return {
        "ok": True,
        **_build_oauth_start_payload(
            request, redirect_uri=redirect_uri, state=state, provider=payload.provider
        ),
    }


@router.get("/admin/oauth/refresh-status")
async def oauth_refresh_status(
    request: Request,
    x_admin_session: str | None = Header(default=None, alias="X-Admin-Session"),
):
    _ensure_admin(request, x_admin_session)
    return {
        "ok": True,
        "response_mode": "status_summary",
        "contains_secrets": False,
        "refresh_supported": False,
        "status": "manual_reauthorize_or_upstream_reverse_engineering_required",
        "refresh_execution_mode": str(
            get_config_store().get_config().get("refresh_execution_mode") or "manual"
        ),
        "message": "A real Notion OAuth refresh exchange is not implemented yet. Accounts with refresh tokens are tracked and flagged, but reauthorization is still required or the upstream refresh call must be reverse engineered.",
    }


@router.get("/admin/workspaces/create-status")
async def workspace_create_status(
    request: Request,
    x_admin_session: str | None = Header(default=None, alias="X-Admin-Session"),
):
    _ensure_admin(request, x_admin_session)
    config = get_config_store().get_config()
    auto_create_enabled = bool(config.get("auto_create_workspace", False))
    dry_run_enabled = bool(config.get("workspace_create_dry_run", True))
    template_space_id = str(config.get("workspace_creation_template_space_id") or "")
    if dry_run_enabled:
        status = "dry_run_only"
        message = "Workspace creation is configured in dry-run mode. Requests are tracked and reported, but no upstream creation transaction is executed."
    elif template_space_id:
        status = "template_configured_but_unimplemented"
        message = "A template workspace is configured, but the upstream Notion creation transaction is not implemented yet."
    else:
        status = "upstream_transaction_unverified"
        message = "Workspace auto-create is enabled, but the real Notion creation transaction still needs to be reverse engineered and verified."

    return {
        "ok": True,
        "response_mode": "status_summary",
        "contains_secrets": False,
        "workspace_create_supported": False,
        "status": status,
        "auto_create_workspace": auto_create_enabled,
        "workspace_create_dry_run": dry_run_enabled,
        "workspace_creation_template_space_id": template_space_id,
        "request_template": {
            "method": "POST",
            "url": "https://www.notion.so/api/v3/saveTransactions",
            "headers": {
                "Content-Type": "application/json",
                "x-notion-active-user-header": "***user-id***",
            },
            "body": {
                "operation": "create_workspace",
                "template_space_id": template_space_id or None,
                "source_space_id": "***source-space-id***",
                "user_id": "***user-id***",
                "space_view_id": "***space-view-id-if-available***",
                "transactions": [
                    {
                        "id": "***workspace-creation-transaction-id***",
                        "space_id": "***source-space-id***",
                        "debug": "replace with real Notion transaction payload",
                    }
                ],
            },
            "notes": [
                "This is a dry-run or preparation template only.",
                "Replace placeholders with a verified upstream Notion transaction payload.",
            ],
            "operation": "create_workspace",
            "provider": "notion-web",
            "template_space_id": template_space_id or None,
        },
        "message": message,
    }


@router.post("/admin/oauth/finalize")
async def finalize_oauth(
    request: Request,
    payload: OAuthFinalizeRequest,
    x_admin_session: str | None = Header(default=None, alias="X-Admin-Session"),
):
    _ensure_admin(request, x_admin_session)
    callback_payload = _consume_oauth_callback_session_payload(request, payload.state)
    effective_payload = payload
    if callback_payload:
        effective_payload = OAuthFinalizeRequest(
            token_v2=str(callback_payload.get("token_v2") or payload.token_v2 or ""),
            user_id=str(callback_payload.get("user_id") or payload.user_id or ""),
            redirect_uri=payload.redirect_uri,
            state=payload.state,
            provider=str(
                callback_payload.get("provider") or payload.provider or "notion-web"
            ),
            space_id=str(callback_payload.get("space_id") or payload.space_id or ""),
            user_email=str(
                callback_payload.get("user_email") or payload.user_email or ""
            ),
            access_token=str(
                callback_payload.get("access_token") or payload.access_token or ""
            ),
            refresh_token=str(
                callback_payload.get("refresh_token") or payload.refresh_token or ""
            ),
            expires_at=int(
                str(callback_payload.get("expires_at") or payload.expires_at or 0) or 0
            )
            or None,
            space_view_id=payload.space_view_id,
            user_name=payload.user_name,
            scopes=payload.scopes,
            plan_type=payload.plan_type,
            notes=payload.notes,
            tags=payload.tags,
        )
    if (
        not str(effective_payload.token_v2 or "").strip()
        or not str(effective_payload.user_id or "").strip()
    ):
        raise HTTPException(
            status_code=400,
            detail="OAuth callback data is incomplete. Paste a valid localhost callback URL or use the fallback fields.",
        )
    saved = get_config_store().upsert_account(
        _build_saved_oauth_account(
            OAuthCallbackPayload(
                token_v2=effective_payload.token_v2,
                user_id=effective_payload.user_id,
                space_id=effective_payload.space_id,
                space_view_id=effective_payload.space_view_id,
                user_name=effective_payload.user_name,
                user_email=effective_payload.user_email,
                access_token=effective_payload.access_token,
                refresh_token=effective_payload.refresh_token,
                expires_at=effective_payload.expires_at,
                provider=effective_payload.provider,
                scopes=effective_payload.scopes,
                plan_type=effective_payload.plan_type,
                source="oauth_finalize",
                notes=effective_payload.notes,
                tags=effective_payload.tags,
            )
        )
    )
    _rebuild_pool(request)
    return {
        "ok": True,
        "account": _redact_account_payload(saved),
        "redirect_uri": effective_payload.redirect_uri.strip()
        or _default_local_redirect_uri(request),
        "state": effective_payload.state,
        "source": "oauth_finalize",
    }


@router.post("/admin/accounts/probe")
async def probe_accounts(
    request: Request,
    x_admin_session: str | None = Header(default=None, alias="X-Admin-Session"),
):
    _ensure_admin(request, x_admin_session)
    results = request.app.state.account_pool.keepalive_accounts()
    return {"ok": True, "results": results}


@router.post("/admin/accounts/{account_id}/probe")
async def probe_single_account(
    account_id: str,
    request: Request,
    x_admin_session: str | None = Header(default=None, alias="X-Admin-Session"),
):
    _ensure_admin(request, x_admin_session)
    pool = request.app.state.account_pool
    try:
        result = pool.probe_account_by_id(account_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    _append_action_history_log(
        "probe",
        {
            "account_id": account_id,
            "user_id": next(
                (
                    item.user_id
                    for item in pool.clients
                    if item.account_id == account_id
                ),
                "",
            ),
            "user_email": next(
                (
                    item.user_email
                    for item in pool.clients
                    if item.account_id == account_id
                ),
                "",
            ),
            "summary": _summarize_action_payload(result),
            "result": result,
        },
    )
    _append_operation_log(
        "probe",
        {
            "count": 1,
            "success_count": 1 if result.get("ok") is not False else 0,
            "failed_count": 1 if result.get("ok") is False else 0,
        },
    )
    return {"ok": True, "result": result}


@router.post("/admin/accounts/refresh")
async def refresh_accounts(
    request: Request,
    x_admin_session: str | None = Header(default=None, alias="X-Admin-Session"),
):
    _ensure_admin(request, x_admin_session)
    results = request.app.state.account_pool.refresh_account_sessions()
    for item in results:
        if str(item.get("account_id") or "").strip():
            _write_refresh_action_result_to_account(
                str(item.get("account_id") or ""), "refresh", item
            )
            _append_action_history_log(
                "refresh",
                {
                    "account_id": str(item.get("account_id") or ""),
                    "summary": _summarize_action_payload(item),
                    "result": item,
                },
            )
    _append_operation_log(
        "refresh",
        {
            "count": len(results),
            "success_count": sum(1 for item in results if item.get("ok") is not False),
            "failed_count": sum(1 for item in results if item.get("ok") is False),
        },
    )
    return {"ok": True, "results": results}


@router.post("/admin/accounts/{account_id}/refresh")
async def refresh_single_account(
    account_id: str,
    request: Request,
    x_admin_session: str | None = Header(default=None, alias="X-Admin-Session"),
):
    _ensure_admin(request, x_admin_session)
    pool = request.app.state.account_pool
    try:
        result = pool.refresh_account_by_id(account_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    _write_refresh_action_result_to_account(account_id, "refresh", result)
    _append_action_history_log(
        "refresh",
        {
            "account_id": account_id,
            "user_id": next(
                (
                    item.user_id
                    for item in pool.clients
                    if item.account_id == account_id
                ),
                "",
            ),
            "user_email": next(
                (
                    item.user_email
                    for item in pool.clients
                    if item.account_id == account_id
                ),
                "",
            ),
            "summary": _summarize_action_payload(result),
            "result": result,
        },
    )
    _append_operation_log(
        "refresh",
        {
            "count": 1,
            "success_count": 1 if result.get("ok") is not False else 0,
            "failed_count": 1 if result.get("ok") is False else 0,
        },
    )
    return {"ok": True, "result": result}


@router.post("/admin/accounts/workspaces/sync")
async def sync_account_workspaces(
    request: Request,
    x_admin_session: str | None = Header(default=None, alias="X-Admin-Session"),
):
    _ensure_admin(request, x_admin_session)
    results = request.app.state.account_pool.sync_workspaces()
    return {"ok": True, "results": results}


@router.post("/admin/accounts/{account_id}/workspaces/sync")
async def sync_single_account_workspaces(
    account_id: str,
    request: Request,
    x_admin_session: str | None = Header(default=None, alias="X-Admin-Session"),
):
    _ensure_admin(request, x_admin_session)
    pool = request.app.state.account_pool
    try:
        result = pool.sync_workspace_by_id(account_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except requests.RequestException as exc:
        error_detail = f"Workspace sync failed: {str(exc)[:300]}"
        summary = _summarize_action_payload(
            {
                "action": "sync_workspace",
                "ok": False,
                "account_id": account_id,
                "user_id": next(
                    (
                        item.user_id
                        for item in pool.clients
                        if item.account_id == account_id
                    ),
                    "",
                ),
                "user_email": next(
                    (
                        item.user_email
                        for item in pool.clients
                        if item.account_id == account_id
                    ),
                    "",
                ),
                "reason": error_detail,
                "failure_category": "network_error",
                "status_code": 502,
                "retryable": True,
            }
        )
        _append_action_history_log(
            "sync_workspace",
            {
                "account_id": account_id,
                "user_id": summary.get("user_id", ""),
                "user_email": summary.get("user_email", ""),
                "summary": summary,
                "result": {
                    "action": "sync_workspace",
                    "ok": False,
                    "account_id": account_id,
                    "user_id": summary.get("user_id", ""),
                    "user_email": summary.get("user_email", ""),
                    "error": error_detail,
                    "status_code": 502,
                    "failure_category": "network_error",
                },
            },
        )
        _append_operation_log(
            "sync_workspace",
            {
                "count": 1,
                "success_count": 0,
                "failed_count": 1,
                "account_id": account_id,
                "status_code": 502,
                "error": error_detail,
            },
        )
        raise HTTPException(
            status_code=502,
            detail=error_detail,
        ) from exc
    _append_action_history_log(
        "sync_workspace",
        {
            "account_id": account_id,
            "user_id": next(
                (
                    item.user_id
                    for item in pool.clients
                    if item.account_id == account_id
                ),
                "",
            ),
            "user_email": next(
                (
                    item.user_email
                    for item in pool.clients
                    if item.account_id == account_id
                ),
                "",
            ),
            "summary": _summarize_action_payload(result),
            "result": result,
        },
    )
    _append_operation_log(
        "sync_workspace",
        {
            "count": 1,
            "success_count": 1 if result.get("ok") is not False else 0,
            "failed_count": 1 if result.get("ok") is False else 0,
        },
    )
    return {"ok": True, "result": result}


@router.post("/admin/accounts/{account_id}/register-hydration-retry")
async def retry_single_account_register_hydration(
    account_id: str,
    request: Request,
    x_admin_session: str | None = Header(default=None, alias="X-Admin-Session"),
):
    _ensure_admin(request, x_admin_session)
    pool = request.app.state.account_pool
    try:
        result = retry_pending_register_hydration(request, account_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    _append_action_history_log(
        "register_hydration_retry",
        {
            "account_id": account_id,
            "user_id": next(
                (
                    item.user_id
                    for item in pool.clients
                    if item.account_id == account_id
                ),
                "",
            ),
            "user_email": next(
                (
                    item.user_email
                    for item in pool.clients
                    if item.account_id == account_id
                ),
                "",
            ),
            "summary": _summarize_action_payload(result),
            "result": result,
        },
    )
    _append_operation_log(
        "register_hydration_retry",
        {
            "count": 1,
            "success_count": 1 if result.get("ok") is not False else 0,
            "failed_count": 1 if result.get("ok") is False else 0,
        },
    )
    return {"ok": True, "result": result}


@router.post("/admin/accounts/workspaces/create")
async def create_account_workspaces(
    request: Request,
    x_admin_session: str | None = Header(default=None, alias="X-Admin-Session"),
):
    _ensure_admin(request, x_admin_session)
    results = request.app.state.account_pool.create_missing_workspaces()
    for item in results:
        if str(item.get("account_id") or "").strip():
            _write_workspace_action_result_to_account(
                str(item.get("account_id") or ""), "create_workspace", item
            )
            _append_action_history_log(
                "create_workspace",
                {
                    "account_id": str(item.get("account_id") or ""),
                    "summary": _summarize_action_payload(item),
                    "result": item,
                },
            )
    _append_operation_log(
        "create_workspace",
        {
            "count": len(results),
            "success_count": sum(1 for item in results if item.get("ok") is not False),
            "failed_count": sum(1 for item in results if item.get("ok") is False),
        },
    )
    return {"ok": True, "results": results}


@router.post("/admin/accounts/{account_id}/workspaces/create")
async def create_single_account_workspace(
    account_id: str,
    request: Request,
    x_admin_session: str | None = Header(default=None, alias="X-Admin-Session"),
):
    _ensure_admin(request, x_admin_session)
    pool = request.app.state.account_pool
    try:
        result = pool.create_workspace_by_id(account_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except requests.RequestException as exc:
        error_detail = f"Workspace creation failed: {str(exc)[:300]}"
        summary = _summarize_action_payload(
            {
                "action": "create_workspace",
                "ok": False,
                "account_id": account_id,
                "user_id": next(
                    (
                        item.user_id
                        for item in pool.clients
                        if item.account_id == account_id
                    ),
                    "",
                ),
                "user_email": next(
                    (
                        item.user_email
                        for item in pool.clients
                        if item.account_id == account_id
                    ),
                    "",
                ),
                "reason": error_detail,
                "failure_category": "network_error",
                "status_code": 502,
                "retryable": True,
            }
        )
        _append_action_history_log(
            "create_workspace",
            {
                "account_id": account_id,
                "user_id": summary.get("user_id", ""),
                "user_email": summary.get("user_email", ""),
                "summary": summary,
                "result": {
                    "action": "create_workspace",
                    "ok": False,
                    "account_id": account_id,
                    "user_id": summary.get("user_id", ""),
                    "user_email": summary.get("user_email", ""),
                    "error": error_detail,
                    "status_code": 502,
                    "failure_category": "network_error",
                },
            },
        )
        _append_operation_log(
            "create_workspace",
            {
                "count": 1,
                "success_count": 0,
                "failed_count": 1,
                "account_id": account_id,
                "status_code": 502,
                "error": error_detail,
            },
        )
        raise HTTPException(
            status_code=502,
            detail=error_detail,
        ) from exc
    _write_workspace_action_result_to_account(account_id, "create_workspace", result)
    _append_action_history_log(
        "create_workspace",
        {
            "account_id": account_id,
            "user_id": next(
                (
                    item.user_id
                    for item in pool.clients
                    if item.account_id == account_id
                ),
                "",
            ),
            "user_email": next(
                (
                    item.user_email
                    for item in pool.clients
                    if item.account_id == account_id
                ),
                "",
            ),
            "summary": _summarize_action_payload(result),
            "result": result,
        },
    )
    _append_operation_log(
        "create_workspace",
        {
            "count": 1,
            "success_count": 1 if result.get("ok") is not False else 0,
            "failed_count": 1 if result.get("ok") is False else 0,
        },
    )
    return {"ok": True, "result": result}


@router.get("/admin/accounts/{account_id}/request-templates")
async def get_account_request_templates(
    account_id: str,
    request: Request,
    x_admin_session: str | None = Header(default=None, alias="X-Admin-Session"),
):
    _ensure_admin(request, x_admin_session)
    pool = request.app.state.account_pool
    try:
        idx = pool._find_client_index(account_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    client = pool.clients[idx]
    refresh_preview = client.try_refresh_session()
    workspace_preview = client.maybe_create_workspace()
    return {
        "ok": True,
        "response_mode": "template_preview",
        "contains_secrets": False,
        "account_id": account_id,
        "user_id": client.user_id,
        "user_email": client.user_email,
        "refresh": refresh_preview,
        "workspace_create": workspace_preview,
    }


@router.post("/admin/accounts/{account_id}/refresh-probe")
async def refresh_probe_account(
    account_id: str,
    request: Request,
    x_admin_session: str | None = Header(default=None, alias="X-Admin-Session"),
):
    _ensure_admin(request, x_admin_session)
    pool = request.app.state.account_pool
    try:
        idx = pool._find_client_index(account_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    client = pool.clients[idx]
    result = client.try_refresh_session_probe()
    _write_probe_result_to_account(account_id, "refresh_probe", result)
    _append_operation_log(
        "refresh_probe",
        {
            "count": 1,
            "success_count": 1 if result.get("ok") is not False else 0,
            "failed_count": 1 if result.get("ok") is False else 0,
        },
    )
    _append_probe_log(
        "refresh_probe",
        {
            "account_id": account_id,
            "summary": _summarize_probe_payload({"result": result}),
            "result": result,
        },
    )
    return {"ok": True, "account_id": account_id, "result": result}


@router.post("/admin/accounts/{account_id}/workspace-probe")
async def workspace_probe_account(
    account_id: str,
    request: Request,
    x_admin_session: str | None = Header(default=None, alias="X-Admin-Session"),
):
    _ensure_admin(request, x_admin_session)
    pool = request.app.state.account_pool
    try:
        idx = pool._find_client_index(account_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    client = pool.clients[idx]
    result = client.try_workspace_create_probe()
    _write_probe_result_to_account(account_id, "workspace_probe", result)
    _append_operation_log(
        "workspace_probe",
        {
            "count": 1,
            "success_count": 1 if result.get("ok") is not False else 0,
            "failed_count": 1 if result.get("ok") is False else 0,
        },
    )
    _append_probe_log(
        "workspace_probe",
        {
            "account_id": account_id,
            "summary": _summarize_probe_payload({"result": result}),
            "result": result,
        },
    )
    return {"ok": True, "account_id": account_id, "result": result}


@router.get("/admin/accounts/safe")
async def list_accounts_safe(
    request: Request,
    q: str = Query(default=""),
    state: str = Query(default=""),
    plan_category: str = Query(default=""),
    enabled: str = Query(default=""),
    sort_by: str = Query(default="updated_at"),
    sort_order: str = Query(default="desc"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    x_admin_session: str | None = Header(default=None, alias="X-Admin-Session"),
):
    _ensure_admin(request, x_admin_session)
    payload = _list_accounts_payload(
        request,
        q=q,
        state=state,
        plan_category=plan_category,
        enabled=enabled,
        sort_by=sort_by,
        sort_order=sort_order,
        page=page,
        page_size=page_size,
    )
    payload["accounts"] = _redact_account_list(payload.get("accounts", []))
    payload["view_mode"] = "safe"
    return payload


@router.get("/admin/accounts/{account_id}")
async def get_account(
    account_id: str,
    request: Request,
    x_admin_session: str | None = Header(default=None, alias="X-Admin-Session"),
):
    _ensure_admin(request, x_admin_session)
    accounts = get_config_store().get_accounts()
    target = next(
        (account for account in accounts if account.get("id") == account_id), None
    )
    if target is None:
        raise HTTPException(status_code=404, detail="Account not found")
    return {"ok": True, "account": target, "view_mode": "raw"}


@router.get("/admin/accounts/workspaces/status")
async def account_workspace_status(
    request: Request,
    x_admin_session: str | None = Header(default=None, alias="X-Admin-Session"),
):
    _ensure_admin(request, x_admin_session)
    store = get_config_store()
    accounts = store.get_config().get("accounts", [])
    rows = []
    for account in accounts:
        workspace = (
            account.get("workspace")
            if isinstance(account.get("workspace"), dict)
            else {}
        )
        rows.append(
            {
                "account_id": account.get("id"),
                "user_id": account.get("user_id"),
                "user_email": account.get("user_email"),
                "space_id": account.get("space_id"),
                "workspace_state": workspace.get("state", "missing"),
                "workspace_count": workspace.get("workspace_count", 0),
                "subscription_tier": workspace.get("subscription_tier", ""),
            }
        )
    return {
        "ok": True,
        "response_mode": "safe_summary",
        "contains_secrets": False,
        "workspaces": rows,
    }


def _list_accounts_payload(
    request: Request,
    q: str = "",
    state: str = "",
    plan_category: str = "",
    enabled: str = "",
    sort_by: str = "updated_at",
    sort_order: str = "desc",
    page: int = 1,
    page_size: int = 20,
) -> dict[str, Any]:
    store = get_config_store()
    config = store.get_config()
    health_rows = request.app.state.account_pool.get_detailed_status()
    accounts = _build_account_view_with_history(
        config.get("accounts", []),
        health_rows,
        config.get("action_history")
        if isinstance(config.get("action_history"), list)
        else [],
    )
    query = q.strip().lower()
    state_filter = state.strip().lower()
    plan_filter = plan_category.strip().lower()
    enabled_filter = enabled.strip().lower()

    if query:
        accounts = [
            item
            for item in accounts
            if query in str(item.get("user_email") or "").lower()
            or query in str(item.get("user_id") or "").lower()
            or query in str(item.get("space_id") or "").lower()
            or query in str(item.get("plan_type") or "").lower()
            or query in str(item.get("plan_category") or "").lower()
            or query in str(item.get("notes") or "").lower()
            or any(query in str(tag).lower() for tag in (item.get("tags") or []))
        ]
    if state_filter:
        if state_filter == "workspace_hydration_due":
            now_ts = int(time.time())
            accounts = [
                item
                for item in accounts
                if str(item.get("status", {}).get("effective_state") or "").lower()
                == "workspace_creation_pending"
                and int(
                    item.get("status", {}).get("workspace_hydration_retry_after") or 0
                )
                <= now_ts
            ]
        else:
            accounts = [
                item
                for item in accounts
                if str(item.get("status", {}).get("effective_state") or "").lower()
                == state_filter
            ]
    if plan_filter:
        accounts = [
            item
            for item in accounts
            if str(item.get("plan_category") or "").lower() == plan_filter
        ]
    if enabled_filter in {"true", "false"}:
        expected = enabled_filter == "true"
        accounts = [
            item for item in accounts if bool(item.get("enabled", True)) == expected
        ]

    reverse = sort_order.strip().lower() != "asc"
    sortable_fields = {
        "updated_at": lambda item: int(item.get("updated_at") or 0),
        "created_at": lambda item: int(item.get("created_at") or 0),
        "email": lambda item: str(item.get("user_email") or "").lower(),
        "state": lambda item: str(
            item.get("status", {}).get("effective_state") or ""
        ).lower(),
        "plan": lambda item: str(item.get("plan_category") or "").lower(),
        "workspace_count": lambda item: int(
            item.get("workspace", {}).get("workspace_count") or 0
        ),
    }
    sort_key = sortable_fields.get(
        sort_by.strip().lower(), sortable_fields["updated_at"]
    )
    accounts = sorted(accounts, key=sort_key, reverse=reverse)

    total = len(accounts)
    start = (page - 1) * page_size
    end = start + page_size
    paged_accounts = accounts[start:end]

    summary = {
        "total": len(accounts),
        "usable": sum(1 for item in accounts if item.get("status", {}).get("usable")),
        "disabled": sum(
            1
            for item in accounts
            if item.get("status", {}).get("effective_state") == "disabled"
        ),
        "invalid": sum(
            1
            for item in accounts
            if item.get("status", {}).get("effective_state") == "invalid"
        ),
        "cooling": sum(
            1
            for item in accounts
            if item.get("status", {}).get("effective_state") == "cooling"
        ),
        "oauth_expired": sum(
            1
            for item in accounts
            if item.get("status", {}).get("effective_state") == "oauth_expired"
        ),
        "needs_refresh": sum(
            1
            for item in accounts
            if item.get("status", {}).get("effective_state") == "needs_refresh"
        ),
        "workspace_creation_pending": sum(
            1
            for item in accounts
            if item.get("status", {}).get("effective_state")
            == "workspace_creation_pending"
        ),
        "workspace_hydration_due": sum(
            1
            for item in accounts
            if item.get("status", {}).get("effective_state")
            == "workspace_creation_pending"
            and int(item.get("status", {}).get("workspace_hydration_retry_after") or 0)
            <= int(time.time())
        ),
        "no_workspace": sum(
            1 for item in accounts if item.get("status", {}).get("no_workspace")
        ),
    }
    return {
        "ok": True,
        "summary": summary,
        "filters": {
            "q": q,
            "state": state,
            "plan_category": plan_category,
            "enabled": enabled,
            "sort_by": sort_by,
            "sort_order": sort_order,
        },
        "pagination": {
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": (total + page_size - 1) // page_size,
        },
        "accounts": paged_accounts,
    }


@router.get("/admin/accounts")
async def list_accounts(
    request: Request,
    q: str = Query(default=""),
    state: str = Query(default=""),
    plan_category: str = Query(default=""),
    enabled: str = Query(default=""),
    sort_by: str = Query(default="updated_at"),
    sort_order: str = Query(default="desc"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    x_admin_session: str | None = Header(default=None, alias="X-Admin-Session"),
):
    _ensure_admin(request, x_admin_session)
    payload = _list_accounts_payload(
        request,
        q=q,
        state=state,
        plan_category=plan_category,
        enabled=enabled,
        sort_by=sort_by,
        sort_order=sort_order,
        page=page,
        page_size=page_size,
    )
    payload["view_mode"] = "raw"
    _append_operation_log(
        "accounts_list_raw",
        {
            "count": len(payload.get("accounts", [])),
            "success_count": len(payload.get("accounts", [])),
            "failed_count": 0,
            "page": page,
            "page_size": page_size,
            "filters": {
                "q": q,
                "state": state,
                "plan_category": plan_category,
                "enabled": enabled,
                "sort_by": sort_by,
                "sort_order": sort_order,
            },
        },
    )
    return payload


@router.get("/admin/alerts")
async def get_admin_alerts(
    request: Request,
    x_admin_session: str | None = Header(default=None, alias="X-Admin-Session"),
):
    _ensure_admin(request, x_admin_session)
    store = get_config_store()
    config = store.get_config()
    health_rows = request.app.state.account_pool.get_detailed_status()
    accounts = _build_account_view_with_history(
        config.get("accounts", []),
        health_rows,
        config.get("action_history")
        if isinstance(config.get("action_history"), list)
        else [],
    )
    alerts = _build_alerts(accounts)
    return {
        "ok": True,
        "response_mode": "safe_summary",
        "contains_secrets": False,
        **alerts,
    }


@router.get("/admin/operations")
async def get_operation_logs(
    request: Request,
    x_admin_session: str | None = Header(default=None, alias="X-Admin-Session"),
):
    _ensure_admin(request, x_admin_session)
    config = get_config_store().get_config()
    logs = (
        config.get("operation_logs")
        if isinstance(config.get("operation_logs"), list)
        else []
    )
    operations = list(reversed(logs))
    return {
        "ok": True,
        "response_mode": "audit_log",
        "contains_secrets": False,
        "count": len(logs),
        "logs": logs,
        "operations": operations,
    }


@router.get("/admin/usage/summary")
async def get_usage_summary(
    request: Request,
    start_ts: int | None = Query(default=None),
    end_ts: int | None = Query(default=None),
    model: str | None = Query(default=None),
    account_id: str | None = Query(default=None),
    request_type: str | None = Query(default=None),
    x_admin_session: str | None = Header(default=None, alias="X-Admin-Session"),
):
    _ensure_admin(request, x_admin_session)
    usage_store = _get_usage_store(request)
    filters = UsageQueryFilters(
        start_ts=start_ts,
        end_ts=end_ts,
        model=model,
        account_id=account_id,
        request_type=request_type,
    )
    summary = usage_store.query_summary(
        start_ts=filters.start_ts,
        end_ts=filters.end_ts,
        model=filters.model,
        account_id=filters.account_id,
        request_type=filters.request_type,
    )
    return {
        "ok": True,
        "response_mode": "usage_summary",
        "contains_secrets": False,
        "filters": filters.model_dump(exclude={"limit", "offset"}),
        "summary": summary,
    }


@router.get("/admin/usage/events")
async def get_usage_events(
    request: Request,
    start_ts: int | None = Query(default=None),
    end_ts: int | None = Query(default=None),
    model: str | None = Query(default=None),
    account_id: str | None = Query(default=None),
    request_type: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    x_admin_session: str | None = Header(default=None, alias="X-Admin-Session"),
):
    _ensure_admin(request, x_admin_session)
    usage_store = _get_usage_store(request)
    filters = UsageQueryFilters(
        start_ts=start_ts,
        end_ts=end_ts,
        model=model,
        account_id=account_id,
        request_type=request_type,
        limit=limit,
        offset=offset,
    )
    result = usage_store.query_events(
        start_ts=filters.start_ts,
        end_ts=filters.end_ts,
        model=filters.model,
        account_id=filters.account_id,
        request_type=filters.request_type,
        limit=filters.limit,
        offset=filters.offset,
    )
    return {
        "ok": True,
        "response_mode": "usage_events",
        "contains_secrets": False,
        "filters": filters.model_dump(),
        **result,
    }


@router.get("/admin/oauth/refresh-diagnostics")
async def oauth_refresh_diagnostics(
    request: Request,
    x_admin_session: str | None = Header(default=None, alias="X-Admin-Session"),
):
    _ensure_admin(request, x_admin_session)
    store = get_config_store()
    config = store.get_config()
    health_rows = request.app.state.account_pool.get_detailed_status()
    accounts = _build_account_view_with_history(
        config.get("accounts", []),
        health_rows,
        config.get("action_history")
        if isinstance(config.get("action_history"), list)
        else [],
    )
    diagnostics = _build_refresh_diagnostics(accounts)
    return {
        "ok": True,
        "response_mode": "safe_summary",
        "contains_secrets": False,
        **diagnostics,
    }


@router.get("/admin/workspaces/diagnostics")
async def workspace_diagnostics(
    request: Request,
    x_admin_session: str | None = Header(default=None, alias="X-Admin-Session"),
):
    _ensure_admin(request, x_admin_session)
    store = get_config_store()
    config = store.get_config()
    health_rows = request.app.state.account_pool.get_detailed_status()
    accounts = _build_account_view_with_history(
        config.get("accounts", []),
        health_rows,
        config.get("action_history")
        if isinstance(config.get("action_history"), list)
        else [],
    )
    diagnostics = _build_workspace_diagnostics(accounts)
    return {
        "ok": True,
        "response_mode": "safe_summary",
        "contains_secrets": False,
        **diagnostics,
    }


@router.post("/admin/accounts/disable")
async def disable_account(
    request: Request,
    payload: AccountActionRequest,
    x_admin_session: str | None = Header(default=None, alias="X-Admin-Session"),
):
    _ensure_admin(request, x_admin_session)
    store = get_config_store()
    accounts = store.get_accounts()
    target = next(
        (account for account in accounts if account.get("id") == payload.account_id),
        None,
    )
    if target is None:
        raise HTTPException(status_code=404, detail="Account not found")
    target["enabled"] = False
    saved = store.upsert_account(target)
    _rebuild_pool(request)
    return {"ok": True, "account": saved}


@router.post("/admin/accounts/enable")
async def enable_account(
    request: Request,
    payload: AccountActionRequest,
    x_admin_session: str | None = Header(default=None, alias="X-Admin-Session"),
):
    _ensure_admin(request, x_admin_session)
    store = get_config_store()
    accounts = store.get_accounts()
    target = next(
        (account for account in accounts if account.get("id") == payload.account_id),
        None,
    )
    if target is None:
        raise HTTPException(status_code=404, detail="Account not found")
    target["enabled"] = True
    saved = store.upsert_account(target)
    _rebuild_pool(request)
    return {"ok": True, "account": saved}


@router.post("/admin/accounts/bulk-action")
async def bulk_account_action(
    request: Request,
    payload: BulkAccountActionRequest,
    x_admin_session: str | None = Header(default=None, alias="X-Admin-Session"),
):
    _ensure_admin(request, x_admin_session)
    action = payload.action.strip().lower()
    raw_account_ids = payload.account_ids or []
    account_ids = [str(item).strip() for item in raw_account_ids if str(item).strip()]
    if action == "register_hydration_retry" and not account_ids:
        account_ids = list_due_pending_hydration_account_ids()
        if not account_ids:
            return {
                "ok": True,
                "action": action,
                "count": 0,
                "success_count": 0,
                "failed_count": 0,
                "results": [],
                "note": "No pending hydration accounts are due for retry yet.",
            }
    if not account_ids:
        raise HTTPException(status_code=400, detail="account_ids cannot be empty")
    store = get_config_store()
    accounts = store.get_accounts()
    matched = [
        account for account in accounts if str(account.get("id") or "") in account_ids
    ]
    if not matched:
        raise HTTPException(status_code=404, detail="No matching accounts found")

    results: list[dict[str, Any]] = []
    rebuild_needed = False

    if action in {"enable", "disable"}:
        target_enabled = action == "enable"
        for account in matched:
            account["enabled"] = target_enabled
            saved = store.upsert_account(account)
            results.append(
                {
                    "account_id": saved.get("id"),
                    "action": action,
                    "enabled": saved.get("enabled"),
                }
            )
        rebuild_needed = True
    else:
        pool = request.app.state.account_pool
        for account in matched:
            account_id = str(account.get("id") or "")
            user_id = str(account.get("user_id") or "")
            user_email = str(account.get("user_email") or "")
            try:
                if action == "probe":
                    result = pool.probe_account_by_id(account_id)
                    _append_action_history_log(
                        "probe",
                        {
                            "account_id": account_id,
                            "user_id": user_id,
                            "user_email": user_email,
                            "summary": _summarize_action_payload(result),
                            "result": result,
                        },
                    )
                elif action == "refresh":
                    result = pool.refresh_account_by_id(account_id)
                    _write_refresh_action_result_to_account(
                        account_id, "refresh", result
                    )
                    _append_action_history_log(
                        "refresh",
                        {
                            "account_id": account_id,
                            "user_id": user_id,
                            "user_email": user_email,
                            "summary": _summarize_action_payload(result),
                            "result": result,
                        },
                    )
                elif action == "sync_workspace":
                    result = pool.sync_workspace_by_id(account_id)
                    _append_action_history_log(
                        "sync_workspace",
                        {
                            "account_id": account_id,
                            "user_id": user_id,
                            "user_email": user_email,
                            "summary": _summarize_action_payload(result),
                            "result": result,
                        },
                    )
                elif action == "register_hydration_retry":
                    result = retry_pending_register_hydration(request, account_id)
                    _append_action_history_log(
                        "register_hydration_retry",
                        {
                            "account_id": account_id,
                            "user_id": user_id,
                            "user_email": user_email,
                            "summary": _summarize_action_payload(result),
                            "result": result,
                        },
                    )
                elif action == "create_workspace":
                    result = pool.create_workspace_by_id(account_id)
                    _write_workspace_action_result_to_account(
                        account_id, "create_workspace", result
                    )
                    _append_action_history_log(
                        "create_workspace",
                        {
                            "account_id": account_id,
                            "user_id": user_id,
                            "user_email": user_email,
                            "summary": _summarize_action_payload(result),
                            "result": result,
                        },
                    )
                elif action == "refresh_probe":
                    idx = pool._find_client_index(account_id)
                    client = pool.clients[idx]
                    result = {
                        "account_id": account_id,
                        "account": client.account_key,
                        **client.try_refresh_session_probe(),
                    }
                    _write_probe_result_to_account(account_id, "refresh_probe", result)
                    _append_action_history_log(
                        "refresh_probe",
                        {
                            "account_id": account_id,
                            "user_id": user_id,
                            "user_email": user_email,
                            "summary": _summarize_action_payload(result),
                            "result": result,
                        },
                    )
                elif action == "workspace_probe":
                    idx = pool._find_client_index(account_id)
                    client = pool.clients[idx]
                    result = {
                        "account_id": account_id,
                        "account": client.account_key,
                        **client.try_workspace_create_probe(),
                    }
                    _write_probe_result_to_account(
                        account_id, "workspace_probe", result
                    )
                    _append_action_history_log(
                        "workspace_probe",
                        {
                            "account_id": account_id,
                            "user_id": user_id,
                            "user_email": user_email,
                            "summary": _summarize_action_payload(result),
                            "result": result,
                        },
                    )
                else:
                    raise HTTPException(
                        status_code=400, detail="Unsupported bulk action"
                    )
                results.append(result)
            except ValueError as exc:
                failure_result = {
                    "account_id": account_id,
                    "ok": False,
                    "error": str(exc),
                }
                _append_action_history_log(
                    action,
                    {
                        "account_id": account_id,
                        "user_id": user_id,
                        "user_email": user_email,
                        "summary": _summarize_action_payload(
                            {
                                "action": action,
                                "ok": False,
                                "account_id": account_id,
                                "user_id": user_id,
                                "user_email": user_email,
                                "reason": str(exc),
                                "failure_category": "not_found",
                                "status_code": 404,
                            }
                        ),
                        "result": failure_result,
                    },
                )
                results.append(failure_result)
            except requests.RequestException as exc:
                failure_reason = str(exc)[:300]
                failure_result = {
                    "account_id": account_id,
                    "ok": False,
                    "error": failure_reason,
                }
                _append_action_history_log(
                    action,
                    {
                        "account_id": account_id,
                        "user_id": user_id,
                        "user_email": user_email,
                        "summary": _summarize_action_payload(
                            {
                                "action": action,
                                "ok": False,
                                "account_id": account_id,
                                "user_id": user_id,
                                "user_email": user_email,
                                "reason": failure_reason,
                                "failure_category": "network_error",
                                "status_code": 502,
                            }
                        ),
                        "result": failure_result,
                    },
                )
                results.append(failure_result)

    if rebuild_needed:
        _rebuild_pool(request)

    success_count = sum(1 for item in results if item.get("ok", True) is not False)
    failed_count = len(results) - success_count
    response = {
        "ok": True,
        "action": action,
        "count": len(results),
        "success_count": success_count,
        "failed_count": failed_count,
        "results": results,
    }
    _append_operation_log(action, response)
    if action in {"refresh_probe", "workspace_probe"}:
        _append_probe_log(
            action,
            {
                "action": action,
                "count": len(results),
                "summaries": [_summarize_probe_payload(item) for item in results],
                "result": response,
            },
        )
    return response

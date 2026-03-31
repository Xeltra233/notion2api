import asyncio
import socket
import threading
import time
import uuid
import requests
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel

from app.config import get_config_store
from app.logger import logger
from app.register.mail_client import create_temp_mail_client
from app.register.notion_register import NotionRegisterService, NotionRegisterResult


def _is_proxy_url_reachable(proxy_url: str) -> bool:
    value = str(proxy_url or "").strip()
    if not value:
        return False
    parsed = urlparse(value)
    host = str(parsed.hostname or "")
    port = int(parsed.port or 0)
    if not host or not port:
        return False
    try:
        with socket.create_connection((host, port), timeout=2):
            return True
    except OSError:
        return False


def _proxy_gate_reason(config: dict[str, Any]) -> str | None:
    proxy_mode = str(config.get("upstream_proxy_mode") or "direct").strip().lower()
    if proxy_mode == "direct":
        return None
    warp_enabled = bool(config.get("upstream_warp_enabled", False))
    warp_proxy = str(config.get("upstream_warp_proxy") or "").strip()
    socks5_proxy = str(config.get("upstream_socks5_proxy") or "").strip()
    base_proxy = str(config.get("upstream_proxy") or "").strip()
    http_proxy = str(config.get("upstream_http_proxy") or "").strip()
    https_proxy = str(config.get("upstream_https_proxy") or "").strip()
    if proxy_mode == "warp":
        if not (warp_enabled and warp_proxy):
            return "proxy_unconfigured"
    if proxy_mode == "socks5" and not socks5_proxy:
        return "proxy_unconfigured"
    if proxy_mode == "http" and not (http_proxy or base_proxy):
        return "proxy_unconfigured"
    if proxy_mode == "https" and not (https_proxy or base_proxy):
        return "proxy_unconfigured"
    if proxy_mode == "mixed" and not (
        socks5_proxy or http_proxy or https_proxy or base_proxy
    ):
        return "proxy_unconfigured"
    if proxy_mode == "warp" and warp_enabled and warp_proxy:
        return "proxy_unreachable" if not _is_proxy_url_reachable(warp_proxy) else None
    if proxy_mode == "warp" and not _is_proxy_url_reachable(warp_proxy):
        return "proxy_unreachable"
    if proxy_mode == "socks5" and not _is_proxy_url_reachable(socks5_proxy):
        return "proxy_unreachable"
    if proxy_mode == "http" and not _is_proxy_url_reachable(http_proxy or base_proxy):
        return "proxy_unreachable"
    if proxy_mode == "https" and not _is_proxy_url_reachable(https_proxy or base_proxy):
        return "proxy_unreachable"
    if proxy_mode == "mixed" and not any(
        _is_proxy_url_reachable(item)
        for item in [socks5_proxy, http_proxy, https_proxy, base_proxy]
        if str(item or "").strip()
    ):
        return "proxy_unreachable"
    return None


router = APIRouter(tags=["register"])


def _ensure_admin(request: Request, session_token: str | None) -> dict[str, Any]:
    from app.api.admin import _ensure_admin as _shared_admin_guard

    return _shared_admin_guard(request, session_token)


REGISTER_TASKS: Dict[str, Dict[str, Any]] = {}
REGISTER_AUTOMATION_STATE: Dict[str, Any] = {
    "last_started_at": 0,
    "last_finished_at": 0,
    "last_task_id": "",
    "active": False,
    "last_decision_reason": "never_run",
}


def _collect_recent_busy_timestamps(config: dict[str, Any]) -> list[int]:
    recent_action_timestamps = [
        int(task.get("finished_at") or 0)
        for task in REGISTER_TASKS.values()
        if isinstance(task, dict)
    ]
    action_history = (
        config.get("action_history")
        if isinstance(config.get("action_history"), list)
        else []
    )
    for item in action_history[-20:]:
        if not isinstance(item, dict):
            continue
        action_name = str(item.get("action") or "").strip().lower()
        if action_name not in {
            "sync_workspace",
            "create_workspace",
            "register_hydration_retry",
            "refresh",
        }:
            continue
        recent_action_timestamps.append(int(item.get("timestamp") or 0))
    return [ts for ts in recent_action_timestamps if ts > 0]


def _count_pending_hydration_accounts(
    accounts: list[dict[str, Any]], effective_now: int
) -> tuple[int, int]:
    pending_total = 0
    pending_due = 0
    for account in accounts:
        status = (
            account.get("status") if isinstance(account.get("status"), dict) else {}
        )
        workspace = (
            account.get("workspace")
            if isinstance(account.get("workspace"), dict)
            else {}
        )
        if not bool(status.get("workspace_hydration_pending", False)):
            continue
        workspace_state = str(
            workspace.get("state") or status.get("workspace_state") or ""
        ).strip()
        if workspace_state != "workspace_creation_pending":
            continue
        pending_total += 1
        retry_after = int(status.get("workspace_hydration_retry_after") or 0)
        if retry_after <= 0 or retry_after <= effective_now:
            pending_due += 1
    return pending_total, pending_due


def _evaluate_auto_register_gate(now_ts: int | None = None) -> dict[str, Any]:
    effective_now = int(now_ts or time.time())
    store = get_config_store()
    config = store.get_config()
    if hasattr(store, "get_accounts"):
        accounts = store.get_accounts()
    else:
        accounts = (
            config.get("accounts") if isinstance(config.get("accounts"), list) else []
        )
    min_spacing = int(config.get("auto_register_min_spacing_seconds") or 900)
    interval_seconds = int(config.get("auto_register_interval_seconds") or 1800)
    idle_only = bool(config.get("auto_register_idle_only", True))
    busy_cooldown = int(config.get("auto_register_busy_cooldown_seconds") or 1200)
    proxy_mode = str(config.get("upstream_proxy_mode") or "direct").strip().lower()
    last_started_at = int(REGISTER_AUTOMATION_STATE.get("last_started_at") or 0)
    recent_action_timestamps = _collect_recent_busy_timestamps(config)
    active_register_task = any(
        task.get("status") in {"queued", "running"}
        for task in REGISTER_TASKS.values()
        if isinstance(task, dict)
    )
    pending_total, pending_due = _count_pending_hydration_accounts(
        accounts, effective_now
    )
    effective_spacing = max(min_spacing, interval_seconds)
    spacing_remaining = (
        max(0, effective_spacing - (effective_now - last_started_at))
        if last_started_at
        else 0
    )
    last_busy_at = max(recent_action_timestamps) if recent_action_timestamps else 0
    busy_remaining = (
        max(0, busy_cooldown - (effective_now - last_busy_at)) if last_busy_at else 0
    )
    proxy_gate = _proxy_gate_reason(config)
    reason = "ok"
    next_eligible_at = effective_now
    if not bool(config.get("auto_register_enabled", False)):
        reason = "auto_register_disabled"
        next_eligible_at = 0
    elif proxy_gate:
        reason = proxy_gate
        next_eligible_at = 0
    elif REGISTER_AUTOMATION_STATE.get("active"):
        reason = "auto_register_active"
        next_eligible_at = 0
    elif spacing_remaining > 0:
        reason = "auto_register_spacing"
        next_eligible_at = effective_now + spacing_remaining
    elif active_register_task:
        reason = "register_task_active"
        next_eligible_at = 0
    elif pending_due > 0:
        reason = "pending_hydration_due"
        next_eligible_at = 0
    elif idle_only and busy_remaining > 0:
        reason = "busy_cooldown_active"
        next_eligible_at = effective_now + busy_remaining
    return {
        "allowed": reason == "ok",
        "reason": reason,
        "effective_now": effective_now,
        "proxy_mode": proxy_mode,
        "proxy_gate_reason": proxy_gate or "",
        "last_started_at": last_started_at,
        "min_spacing_seconds": min_spacing,
        "interval_seconds": interval_seconds,
        "idle_only": idle_only,
        "effective_spacing_seconds": effective_spacing,
        "spacing_remaining_seconds": spacing_remaining,
        "busy_cooldown_seconds": busy_cooldown,
        "busy_cooldown_remaining_seconds": busy_remaining,
        "next_eligible_at": next_eligible_at,
        "register_task_active": active_register_task,
        "pending_hydration_total": pending_total,
        "pending_hydration_due": pending_due,
        "pending_hydration_blocking": pending_due > 0,
    }


class RegisterRequest(BaseModel):
    count: int = 1
    mail_provider: str = "moemail"
    domain: Optional[str] = None
    mail_base_url: Optional[str] = None
    mail_api_key: Optional[str] = None
    use_api: bool = True
    headless: bool = True
    proxy: Optional[str] = None


class RegisterTaskStatus(BaseModel):
    task_id: str
    status: str
    progress: int
    total: int
    success_count: int
    fail_count: int
    logs: List[Dict[str, Any]]
    results: List[Dict[str, Any]]


def _append_log(task_id: str, level: str, message: str) -> None:
    if task_id not in REGISTER_TASKS:
        return
    REGISTER_TASKS[task_id]["logs"].append(
        {
            "time": time.time(),
            "level": level,
            "message": message,
        }
    )
    logger.log(
        getattr(__import__("logging"), level.upper(), __import__("logging").INFO),
        f"[REGISTER-{task_id[:8]}] {message}",
    )


def _can_start_auto_register(now_ts: int | None = None) -> tuple[bool, str]:
    evaluation = _evaluate_auto_register_gate(now_ts)
    REGISTER_AUTOMATION_STATE["last_decision_reason"] = evaluation["reason"]
    return bool(evaluation["allowed"]), str(evaluation["reason"])


def _start_register_thread(
    request: Request,
    task_id: str,
    count: int,
    mail_provider: str,
    domain: Optional[str],
    mail_base_url: Optional[str],
    mail_api_key: Optional[str],
    use_api: bool,
    headless: bool,
    proxy: Optional[str],
) -> None:
    worker = threading.Thread(
        target=_run_register_task,
        args=(
            request,
            task_id,
            count,
            mail_provider,
            domain,
            mail_base_url,
            mail_api_key,
            use_api,
            headless,
            proxy,
        ),
        daemon=True,
        name=f"register-task-{task_id[:8]}",
    )
    worker.start()


def maybe_start_auto_register(request: Request) -> Dict[str, Any]:
    now_ts = int(time.time())
    allowed, reason = _can_start_auto_register(now_ts)
    if not allowed:
        return {"ok": False, "reason": reason}
    config = get_config_store().get_config()
    task_id = str(uuid.uuid4())
    count = int(config.get("auto_register_batch_size") or 1)
    REGISTER_TASKS[task_id] = {
        "task_id": task_id,
        "status": "queued",
        "progress": 0,
        "total": count,
        "success_count": 0,
        "fail_count": 0,
        "logs": [],
        "results": [],
        "created_at": time.time(),
        "cancelled": False,
        "auto": True,
    }
    REGISTER_AUTOMATION_STATE["last_started_at"] = now_ts
    REGISTER_AUTOMATION_STATE["last_task_id"] = task_id
    REGISTER_AUTOMATION_STATE["active"] = True
    REGISTER_AUTOMATION_STATE["last_decision_reason"] = "queued"
    _start_register_thread(
        request,
        task_id,
        count,
        str(config.get("auto_register_mail_provider") or "freemail"),
        str(config.get("auto_register_domain") or "") or None,
        str(config.get("auto_register_mail_base_url") or "") or None,
        str(config.get("auto_register_mail_api_key") or "") or None,
        bool(config.get("auto_register_use_api", True)),
        bool(config.get("auto_register_headless", False)),
        _effective_proxy(None) or None,
    )
    return {
        "ok": True,
        "status": "queued",
        "reason": "queued",
        "task_id": task_id,
        "count": count,
    }


def get_register_automation_state() -> Dict[str, Any]:
    return get_register_automation_snapshot()


def get_register_automation_snapshot(now_ts: int | None = None) -> Dict[str, Any]:
    snapshot = dict(REGISTER_AUTOMATION_STATE)
    evaluation = _evaluate_auto_register_gate(now_ts)
    task_id = str(snapshot.get("last_task_id") or "").strip()
    latest_task = REGISTER_TASKS.get(task_id) if task_id else None
    latest_task_status = (
        str(latest_task.get("status") or "").strip().lower()
        if isinstance(latest_task, dict)
        else ""
    )
    gate_reason = str(evaluation["reason"])
    current_reason = gate_reason
    if (
        gate_reason == "register_task_active"
        and str(snapshot.get("last_decision_reason") or "").strip().lower() == "queued"
        and latest_task_status in {"queued", "running"}
    ):
        current_reason = "queued"
    snapshot.update(
        {
            "eligible": bool(evaluation["allowed"]),
            "current_reason": current_reason,
            "gate_reason": gate_reason,
            "latest_task_status": latest_task_status,
            "proxy_mode": evaluation["proxy_mode"],
            "proxy_gate_reason": evaluation["proxy_gate_reason"],
            "register_task_active": bool(evaluation["register_task_active"]),
            "pending_hydration_total": int(evaluation["pending_hydration_total"]),
            "pending_hydration_due": int(evaluation["pending_hydration_due"]),
            "pending_hydration_blocking": bool(
                evaluation["pending_hydration_blocking"]
            ),
            "spacing_remaining_seconds": int(evaluation["spacing_remaining_seconds"]),
            "busy_cooldown_remaining_seconds": int(
                evaluation["busy_cooldown_remaining_seconds"]
            ),
            "next_eligible_at": int(evaluation["next_eligible_at"]),
        }
    )
    return snapshot


def _register_one(
    task_id: str,
    mail_provider: str,
    domain: Optional[str],
    mail_base_url: Optional[str],
    mail_api_key: Optional[str],
    use_api: bool,
    headless: bool,
    proxy: Optional[str],
) -> Dict[str, Any]:
    def log_cb(level: str, msg: str) -> None:
        _append_log(task_id, level, msg)

    log_cb("info", "开始注册新账户...")
    log_cb("info", f"邮箱提供商: {mail_provider}")
    mail_client = create_temp_mail_client(
        provider=mail_provider,
        base_url=mail_base_url or "",
        api_key=mail_api_key or "",
        proxy=proxy or "",
        domain=domain,
        log_callback=log_cb,
    )
    log_cb("info", "注册临时邮箱...")
    if not mail_client.register_account(domain=domain):
        log_cb("error", "临时邮箱注册失败")
        return {"success": False, "error": "临时邮箱注册失败"}
    mail_poll_start = time.time()
    log_cb("info", f"邮箱注册成功: {mail_client.email}")
    log_cb("info", "开始 Notion 注册...")
    register_service = NotionRegisterService(
        proxy=proxy or "",
        headless=headless,
        timeout=180,
        log_callback=log_cb,
    )
    setattr(mail_client, "notion_poll_start", mail_poll_start)
    result = register_service.register(mail_client, use_api=use_api)
    if result.success:
        log_cb("info", f"Notion 注册成功: {result.email}")
        resolved_space_id = (
            str(result.space_id or "").strip()
            or f"pending-signup-{str(uuid.uuid4())[:12]}"
        )
        account = {
            "token_v2": result.token_v2,
            "space_id": resolved_space_id,
            "user_id": result.user_id,
            "space_view_id": result.space_view_id,
            "user_name": result.email.split("@")[0],
            "user_email": result.email,
            "source": "register_flow",
            "notes": "Created by register flow; workspace metadata may still be hydrating.",
        }
        account = register_service.finalize_account_record(account)
        if not str(account.get("space_id") or "").strip():
            account["space_id"] = resolved_space_id
        account = _apply_pending_hydration_metadata(account)
        _save_account(account)
        return {
            "success": True,
            "email": result.email,
            "token_v2": result.token_v2[:20] + "..." if result.token_v2 else "",
            "user_id": result.user_id,
            "space_id": str(account.get("space_id") or resolved_space_id),
            "space_view_id": str(
                account.get("space_view_id") or result.space_view_id or ""
            ),
            "register_method": result.register_method
            or ("api" if use_api else "browser"),
            "attempted_api": bool(result.attempted_api),
            "used_browser_fallback": bool(result.used_browser_fallback),
            "workspace_count": int(
                (account.get("workspace") or {}).get("workspace_count")
                or result.workspace_count
                or 0
            ),
            "pending_workspace_hydration": bool(
                ((account.get("status") or {}).get("workspace_hydration_pending"))
            ),
        }
    else:
        log_cb("error", f"Notion 注册失败: {result.error}")
        return {"success": False, "error": result.error}


def _save_account(account: Dict[str, Any]) -> None:
    saved = get_config_store().upsert_account(account)
    logger.info(
        "账号已保存到运行时配置",
        extra={
            "request_info": {
                "event": "account_saved_runtime_config",
                "account_id": saved.get("id"),
                "user_email": saved.get("user_email", ""),
                "space_id": saved.get("space_id", ""),
            }
        },
    )


def _apply_pending_hydration_metadata(account: Dict[str, Any]) -> Dict[str, Any]:
    updated = dict(account)
    workspace = (
        updated.get("workspace") if isinstance(updated.get("workspace"), dict) else {}
    )
    status = updated.get("status") if isinstance(updated.get("status"), dict) else {}
    now_ts = int(time.time())
    if str(updated.get("space_id") or "").startswith("pending-signup-"):
        existing_retry_after = int(status.get("workspace_hydration_retry_after") or 0)
        existing_backoff = int(status.get("workspace_hydration_backoff_seconds") or 0)
        workspace = {
            **workspace,
            "state": "workspace_creation_pending",
            "workspace_count": int(workspace.get("workspace_count") or 0),
            "workspaces": workspace.get("workspaces") or [],
        }
        status = {
            **status,
            "workspace_state": "workspace_creation_pending",
            "workspace_hydration_pending": True,
            "workspace_hydration_retry_after": (
                existing_retry_after if existing_retry_after > 0 else now_ts + 120
            ),
            "workspace_hydration_backoff_seconds": (
                existing_backoff if existing_backoff > 0 else 120
            ),
            "last_workspace_action": status.get("last_workspace_action")
            or "register_pending_hydration",
        }
        updated["notes"] = str(updated.get("notes") or "").strip() or (
            "Created by register flow; workspace metadata may still be hydrating."
        )
    updated["workspace"] = workspace
    updated["status"] = status
    return updated


def _migrate_pending_signup_accounts() -> int:
    store = get_config_store()
    accounts = store.get_accounts()
    changed = 0
    migrated_accounts = []
    for account in accounts:
        migrated = _apply_pending_hydration_metadata(account)
        if (
            str(migrated.get("space_id") or "").startswith("pending-signup-")
            and migrated.get("workspace") == account.get("workspace")
            and migrated.get("status") == account.get("status")
        ):
            workspace = (
                dict(migrated.get("workspace"))
                if isinstance(migrated.get("workspace"), dict)
                else {}
            )
            status = (
                dict(migrated.get("status"))
                if isinstance(migrated.get("status"), dict)
                else {}
            )
            workspace["state"] = "workspace_creation_pending"
            status["workspace_state"] = "workspace_creation_pending"
            migrated["workspace"] = workspace
            migrated["status"] = status
        if migrated != account:
            changed += 1
        migrated_accounts.append(migrated)
    if changed:
        store.set_accounts(migrated_accounts)
    return changed


_migrate_pending_signup_accounts()


def _rebuild_runtime_account_pool(request: Request) -> None:
    try:
        from app.api.admin import _rebuild_pool

        _rebuild_pool(request)
    except Exception:
        logger.warning(
            "注册成功后刷新运行时账号池失败",
            exc_info=True,
            extra={"request_info": {"event": "register_runtime_pool_rebuild_failed"}},
        )


def _post_register_follow_up(request: Request, account_user_id: str) -> None:
    if not str(account_user_id or "").strip():
        return
    try:
        from app.api.admin import (
            _append_action_history_log,
            _summarize_action_payload,
            _write_workspace_action_result_to_account,
        )

        _rebuild_runtime_account_pool(request)
        pool = request.app.state.account_pool
        target = next(
            (
                item
                for item in pool.clients
                if str(item.user_id or "").strip() == str(account_user_id or "").strip()
            ),
            None,
        )
        if not target:
            return
        result = pool.sync_workspace_by_id(target.account_id)
        _write_workspace_action_result_to_account(
            target.account_id, "sync_workspace", result
        )
        _append_action_history_log(
            "sync_workspace",
            {
                "account_id": target.account_id,
                "user_id": target.user_id,
                "user_email": target.user_email,
                "summary": _summarize_action_payload(result),
                "result": result,
            },
        )
    except Exception:
        logger.warning(
            "注册成功后自动补全 workspace 失败",
            exc_info=True,
            extra={
                "request_info": {
                    "event": "register_workspace_follow_up_failed",
                    "user_id": account_user_id,
                }
            },
        )


def retry_pending_register_hydration(
    request: Request, account_id: str
) -> Dict[str, Any]:
    from app.api.admin import (
        _append_action_history_log,
        _summarize_action_payload,
        _write_refresh_action_result_to_account,
        _write_workspace_action_result_to_account,
    )

    _rebuild_runtime_account_pool(request)
    pool = request.app.state.account_pool
    store = get_config_store()
    config = store.get_config() if hasattr(store, "get_config") else {}
    transient_attempts = max(
        1, min(3, int(config.get("register_hydration_retry_attempts") or 2))
    )
    transient_delay_seconds = max(
        0.0,
        min(5.0, float(config.get("register_hydration_retry_delay_seconds") or 1.5)),
    )
    refresh_recovery_attempted = False
    refresh_recovery_result: Dict[str, Any] | None = None

    def _classify_hydration_exception(exc: Exception) -> tuple[str, str]:
        if isinstance(exc, requests.exceptions.Timeout):
            return "timeout", "workspace hydration timed out"
        if isinstance(exc, requests.HTTPError):
            response = exc.response
            status_code = response.status_code if response is not None else None
            excerpt = (
                (response.text or "").strip().replace("\n", " ")[:300]
                if response is not None
                else str(exc)[:300]
            )
            if status_code == 429:
                return "rate_limited", excerpt or "429 upstream"
            if status_code in {401, 403}:
                return "unauthorized", excerpt or str(exc)
            if status_code == 404:
                return "not_found", excerpt or str(exc)
            if status_code and status_code >= 500:
                return "server_error", excerpt or str(exc)
            return "client_error", excerpt or str(exc)
        message = str(exc).strip()
        lowered = message.lower()
        if any(
            token in lowered for token in {"429", "rate limit", "too many requests"}
        ):
            return "rate_limited", message
        if any(
            token in lowered for token in {"401", "403", "unauthorized", "forbidden"}
        ):
            return "unauthorized", message
        if any(token in lowered for token in {"timeout", "timed out"}):
            return "timeout", message
        if any(
            token in lowered
            for token in {
                "ssl",
                "eof",
                "connection aborted",
                "connection reset",
                "connection refused",
                "max retries exceeded",
                "proxyerror",
                "sock",
            }
        ):
            return "network_error", message
        return "network_error", message

    def _should_retry_hydration_result(candidate: dict[str, Any]) -> bool:
        if bool(candidate.get("ok")):
            return False
        category = str(candidate.get("failure_category") or "").strip().lower()
        return category in {"network_error", "timeout", "server_error", "rate_limited"}

    result: Dict[str, Any] = {}
    for attempt_index in range(transient_attempts):
        try:
            result = pool.sync_workspace_by_id(account_id)
        except Exception as exc:
            category, reason = _classify_hydration_exception(exc)
            result = {
                "ok": False,
                "account_id": account_id,
                "reason": reason,
                "failure_category": category,
            }
        result["attempt"] = attempt_index + 1
        result["attempts"] = transient_attempts
        result["refresh_recovery_attempted"] = refresh_recovery_attempted
        if refresh_recovery_result is not None:
            result["refresh_recovery_ok"] = bool(
                refresh_recovery_result.get("ok", False)
            )
        failure_category = str(result.get("failure_category") or "").strip().lower()
        if (
            failure_category in {"unauthorized", "forbidden"}
            and not refresh_recovery_attempted
            and hasattr(pool, "refresh_account_by_id")
        ):
            refresh_recovery_attempted = True
            try:
                refresh_recovery_result = pool.refresh_account_by_id(account_id)
            except Exception as exc:
                refresh_category, refresh_reason = _classify_hydration_exception(exc)
                refresh_recovery_result = {
                    "ok": False,
                    "account_id": account_id,
                    "reason": refresh_reason,
                    "failure_category": refresh_category,
                    "action": "refresh_exchange_live_template",
                    "reauthorize_required": refresh_category
                    in {"unauthorized", "forbidden"},
                }
            _write_refresh_action_result_to_account(
                account_id,
                "refresh",
                refresh_recovery_result,
            )
            refresh_summary = _summarize_action_payload(refresh_recovery_result)
            _append_action_history_log(
                "refresh",
                {
                    "account_id": account_id,
                    "summary": refresh_summary,
                    "result": refresh_recovery_result,
                },
            )
            result["refresh_recovery_attempted"] = True
            result["refresh_recovery_ok"] = bool(
                refresh_recovery_result.get("ok", False)
            )
            if bool(refresh_recovery_result.get("ok", False)):
                if attempt_index + 1 >= transient_attempts:
                    break
                continue
            break
        if not _should_retry_hydration_result(result):
            break
        if attempt_index + 1 >= transient_attempts:
            break
        time.sleep(transient_delay_seconds)
    _write_workspace_action_result_to_account(account_id, "sync_workspace", result)
    summary = _summarize_action_payload(result)
    _append_action_history_log(
        "sync_workspace",
        {
            "account_id": account_id,
            "summary": summary,
            "result": result,
        },
    )

    accounts = store.get_accounts()
    now_ts = int(time.time())
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
        hydrated = (
            bool(result.get("ok")) and int(result.get("workspace_count") or 0) > 0
        )
        failure_category = str(result.get("failure_category") or "").strip().lower()
        retry_delay = 300
        reason_text = str(result.get("reason") or "").lower()
        is_upstream_429 = "429" in reason_text or failure_category == "rate_limited"
        if is_upstream_429:
            retry_delay = 900
        elif failure_category in {"network_error", "timeout", "server_error"}:
            retry_delay = 300
        elif failure_category in {"unauthorized", "forbidden"}:
            retry_delay = 1800
        elif failure_category in {"client_error", "not_found"}:
            retry_delay = 3600
        status["workspace_hydration_pending"] = not hydrated
        status["workspace_hydration_retry_after"] = (
            0 if hydrated else now_ts + retry_delay
        )
        status["last_workspace_action"] = "sync_workspace"
        status["last_workspace_error"] = (
            "" if hydrated else str(result.get("reason") or "")
        )
        status["last_workspace_failure_category"] = (
            "rate_limited" if is_upstream_429 and not hydrated else failure_category
        )
        status["workspace_hydration_backoff_seconds"] = 0 if hydrated else retry_delay
        status["workspace_hydration_retry_policy"] = (
            "upstream_rate_limit"
            if is_upstream_429 and not hydrated
            else (
                "upstream_transient_failure"
                if failure_category in {"network_error", "timeout", "server_error"}
                and not hydrated
                else (
                    "reauthorize_or_permission_review"
                    if failure_category in {"unauthorized", "forbidden"}
                    and not hydrated
                    else (
                        "config_or_resource_review"
                        if failure_category in {"client_error", "not_found"}
                        and not hydrated
                        else "none"
                    )
                )
            )
        )
        status["workspace_hydration_refresh_recovery_attempted"] = bool(
            result.get("refresh_recovery_attempted", False)
        )
        status["workspace_hydration_refresh_recovery_ok"] = bool(
            result.get("refresh_recovery_ok", False)
        )
        workspace["state"] = (
            "ready"
            if hydrated
            else (workspace.get("state") or "workspace_creation_pending")
        )
        account["status"] = status
        account["workspace"] = workspace
        store.upsert_account(account)
        break
    return result


def list_due_pending_hydration_account_ids(now_ts: int | None = None) -> list[str]:
    store = get_config_store()
    accounts = store.get_accounts()
    effective_now = int(now_ts or time.time())
    account_ids: list[str] = []
    for account in accounts:
        status = (
            account.get("status") if isinstance(account.get("status"), dict) else {}
        )
        workspace = (
            account.get("workspace")
            if isinstance(account.get("workspace"), dict)
            else {}
        )
        if not bool(status.get("workspace_hydration_pending", False)):
            continue
        workspace_state = str(
            workspace.get("state") or status.get("workspace_state") or ""
        ).strip()
        if workspace_state != "workspace_creation_pending":
            continue
        retry_after = int(status.get("workspace_hydration_retry_after") or 0)
        if retry_after and retry_after > effective_now:
            continue
        account_id = str(account.get("id") or "").strip()
        if account_id:
            account_ids.append(account_id)
    return account_ids


def _effective_proxy(explicit_proxy: Optional[str]) -> str:
    explicit = str(explicit_proxy or "").strip()
    if explicit:
        return explicit
    config = get_config_store().get_config()
    proxy_mode = str(config.get("upstream_proxy_mode") or "direct").strip().lower()
    if proxy_mode == "warp" and bool(config.get("upstream_warp_enabled", False)):
        return str(config.get("upstream_warp_proxy") or "").strip()
    if proxy_mode == "socks5":
        return str(config.get("upstream_socks5_proxy") or "").strip()
    return str(
        config.get("upstream_proxy")
        or config.get("upstream_http_proxy")
        or config.get("upstream_https_proxy")
        or ""
    ).strip()


def _run_register_task(
    request: Request,
    task_id: str,
    count: int,
    mail_provider: str,
    domain: Optional[str],
    mail_base_url: Optional[str],
    mail_api_key: Optional[str],
    use_api: bool,
    headless: bool,
    proxy: Optional[str],
) -> None:
    task = REGISTER_TASKS.get(task_id)
    if not task:
        return
    task["status"] = "running"
    for idx in range(count):
        if task.get("cancelled"):
            task["status"] = "cancelled"
            _append_log(task_id, "warning", "任务已取消")
            break
        _append_log(task_id, "info", f"进度: {idx + 1}/{count}")
        result = _register_one(
            task_id,
            mail_provider,
            domain,
            mail_base_url,
            mail_api_key,
            use_api,
            headless,
            proxy,
        )
        task["results"].append(result)
        task["progress"] = idx + 1
        if result.get("success"):
            task["success_count"] += 1
            _post_register_follow_up(request, str(result.get("user_id") or ""))
            _append_log(task_id, "info", f"注册成功: {result.get('email', '')}")
        else:
            task["fail_count"] += 1
            _append_log(task_id, "error", f"注册失败: {result.get('error', '')}")
        if idx < count - 1 and not task.get("cancelled"):
            _append_log(task_id, "info", "等待 10 秒...")
            time.sleep(10)
    if task["status"] == "running":
        task["status"] = "completed"
    task["finished_at"] = time.time()
    REGISTER_AUTOMATION_STATE["active"] = False
    REGISTER_AUTOMATION_STATE["last_finished_at"] = int(time.time())
    _append_log(
        task_id,
        "info",
        f"任务完成: 成功 {task['success_count']}, 失败 {task['fail_count']}",
    )


@router.post("/register/start")
async def start_register(
    request: Request,
    req: RegisterRequest,
    x_admin_session: str | None = Header(default=None, alias="X-Admin-Session"),
) -> Dict[str, Any]:
    _ensure_admin(request, x_admin_session)
    task_id = str(uuid.uuid4())
    REGISTER_TASKS[task_id] = {
        "task_id": task_id,
        "status": "queued",
        "progress": 0,
        "total": req.count,
        "success_count": 0,
        "fail_count": 0,
        "logs": [],
        "results": [],
        "created_at": time.time(),
        "finished_at": None,
        "cancelled": False,
        "auto": False,
        "config": {
            "mail_provider": req.mail_provider,
            "domain": req.domain,
            "use_api": req.use_api,
            "headless": req.headless,
        },
    }
    proxy = _effective_proxy(req.proxy)
    _start_register_thread(
        request,
        task_id,
        req.count,
        req.mail_provider,
        req.domain,
        req.mail_base_url,
        req.mail_api_key,
        req.use_api,
        req.headless,
        proxy,
    )
    return {"task_id": task_id, "status": "queued"}


@router.get("/register/status/{task_id}")
async def get_register_status(
    task_id: str,
    request: Request,
    x_admin_session: str | None = Header(default=None, alias="X-Admin-Session"),
) -> RegisterTaskStatus:
    _ensure_admin(request, x_admin_session)
    task = REGISTER_TASKS.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    return RegisterTaskStatus(
        task_id=task_id,
        status=task["status"],
        progress=task["progress"],
        total=task["total"],
        success_count=task["success_count"],
        fail_count=task["fail_count"],
        logs=task["logs"][-50:],
        results=task["results"],
    )


@router.post("/register/cancel/{task_id}")
async def cancel_register(
    task_id: str,
    request: Request,
    x_admin_session: str | None = Header(default=None, alias="X-Admin-Session"),
) -> Dict[str, Any]:
    _ensure_admin(request, x_admin_session)
    task = REGISTER_TASKS.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    if task["status"] not in ("queued", "running"):
        raise HTTPException(status_code=400, detail="任务已完成或已取消")
    task["cancelled"] = True
    task["status"] = "cancelled"
    return {"task_id": task_id, "status": "cancelled"}


@router.get("/register/tasks")
async def list_register_tasks(
    request: Request,
    x_admin_session: str | None = Header(default=None, alias="X-Admin-Session"),
) -> List[Dict[str, Any]]:
    _ensure_admin(request, x_admin_session)
    result = []
    for task_id, task in REGISTER_TASKS.items():
        result.append(
            {
                "task_id": task_id,
                "status": task["status"],
                "progress": task["progress"],
                "total": task["total"],
                "success_count": task["success_count"],
                "fail_count": task["fail_count"],
                "created_at": task["created_at"],
                "finished_at": task["finished_at"],
            }
        )
    return sorted(result, key=lambda x: x["created_at"], reverse=True)


@router.delete("/register/tasks/{task_id}")
async def delete_register_task(
    task_id: str,
    request: Request,
    x_admin_session: str | None = Header(default=None, alias="X-Admin-Session"),
) -> Dict[str, Any]:
    _ensure_admin(request, x_admin_session)
    if task_id not in REGISTER_TASKS:
        raise HTTPException(status_code=404, detail="任务不存在")
    task = REGISTER_TASKS[task_id]
    if task["status"] in ("queued", "running"):
        raise HTTPException(status_code=400, detail="任务进行中，请先取消")
    del REGISTER_TASKS[task_id]
    return {"task_id": task_id, "deleted": True}

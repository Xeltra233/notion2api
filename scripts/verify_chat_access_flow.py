import copy
import json
import os
import sys
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("API_KEY", "test-server-key")
os.environ.setdefault("ADMIN_PASSWORD", "test-admin-password")

from app.config import get_chat_auth, get_config_store, update_admin_credentials  # noqa: E402
from app.server import app  # noqa: E402
from scripts.admin_session_test_utils import build_admin_session_headers  # noqa: E402


store = get_config_store()
original_config = copy.deepcopy(store.get_config())


def _chat_request_body() -> dict:
    return {
        "model": "claude-3-5-sonnet-20241022",
        "messages": [{"role": "user", "content": "hello"}],
        "stream": False,
    }


def _access_unblocked(status_code: int) -> bool:
    return int(status_code) != 401


try:
    baseline = copy.deepcopy(original_config)
    baseline["chat_enabled"] = True
    baseline["chat_auth"] = {
        "password_hash": "",
        "password_salt": "",
        "enabled": False,
        "updated_at": 0,
    }
    store.save_config(baseline)
    update_admin_credentials(username="admin", password="test-admin-password")

    with TestClient(app) as client:
        auth_headers = {"Authorization": "Bearer test-server-key"}
        admin_headers = build_admin_session_headers(client)

        open_access = client.get("/v1/chat/access", headers=auth_headers)
        open_completion = client.post(
            "/v1/chat/completions",
            headers=auth_headers,
            json=_chat_request_body(),
        )

        protected_config = copy.deepcopy(store.get_config())
        protected_config["chat_enabled"] = True
        protected_config["refresh_execution_mode"] = "manual"
        protected_config["refresh_request_url"] = ""
        protected_config["workspace_execution_mode"] = "manual"
        protected_config["workspace_request_url"] = ""
        store.save_config(protected_config)
        update_password = client.put(
            "/v1/admin/config/settings",
            headers=admin_headers,
            json={
                "app_mode": protected_config.get("app_mode", "standard"),
                "allowed_origins": protected_config.get("allowed_origins", []),
                "upstream_proxy": protected_config.get("upstream_proxy", ""),
                "upstream_http_proxy": protected_config.get("upstream_http_proxy", ""),
                "upstream_https_proxy": protected_config.get("upstream_https_proxy", ""),
                "upstream_socks5_proxy": protected_config.get("upstream_socks5_proxy", ""),
                "upstream_proxy_mode": protected_config.get("upstream_proxy_mode", "direct"),
                "upstream_warp_enabled": protected_config.get("upstream_warp_enabled", False),
                "upstream_warp_proxy": protected_config.get("upstream_warp_proxy", ""),
                "auto_create_workspace": protected_config.get("auto_create_workspace", False),
                "auto_select_workspace": protected_config.get("auto_select_workspace", True),
                "workspace_create_dry_run": protected_config.get("workspace_create_dry_run", True),
                "workspace_creation_template_space_id": protected_config.get("workspace_creation_template_space_id", ""),
                "account_probe_interval_seconds": protected_config.get("account_probe_interval_seconds", 300),
                "refresh_execution_mode": protected_config.get("refresh_execution_mode", "manual"),
                "refresh_request_url": protected_config.get("refresh_request_url", ""),
                "refresh_client_id": protected_config.get("refresh_client_id", ""),
                "workspace_execution_mode": protected_config.get("workspace_execution_mode", "manual"),
                "workspace_request_url": protected_config.get("workspace_request_url", ""),
                "allow_real_probe_requests": protected_config.get("allow_real_probe_requests", False),
                "chat_enabled": True,
                "chat_password_enabled": True,
                "chat_password": "chat-pass-123",
                "auto_register_enabled": protected_config.get("auto_register_enabled", False),
                "auto_register_idle_only": protected_config.get("auto_register_idle_only", True),
                "auto_register_interval_seconds": protected_config.get("auto_register_interval_seconds", 1800),
                "auto_register_min_spacing_seconds": protected_config.get("auto_register_min_spacing_seconds", 900),
                "auto_register_busy_cooldown_seconds": protected_config.get("auto_register_busy_cooldown_seconds", 1200),
                "auto_register_batch_size": protected_config.get("auto_register_batch_size", 1),
                "auto_register_headless": protected_config.get("auto_register_headless", False),
                "auto_register_use_api": protected_config.get("auto_register_use_api", True),
                "auto_register_mail_provider": protected_config.get("auto_register_mail_provider", "freemail"),
                "auto_register_mail_base_url": protected_config.get("auto_register_mail_base_url", ""),
                "auto_register_domain": protected_config.get("auto_register_domain", ""),
            },
        )
        update_password.raise_for_status()

        protected_access = client.get("/v1/chat/access", headers=auth_headers)
        blocked_completion = client.post(
            "/v1/chat/completions",
            headers=auth_headers,
            json=_chat_request_body(),
        )
        wrong_login = client.post(
            "/v1/chat/login",
            headers=auth_headers,
            json={"password": "wrong-pass"},
        )
        good_login = client.post(
            "/v1/chat/login",
            headers=auth_headers,
            json={"password": "chat-pass-123"},
        )
        good_login.raise_for_status()
        chat_session = good_login.json().get("session_token", "")
        chat_headers = {**auth_headers, "X-Chat-Session": chat_session}
        session_completion = client.post(
            "/v1/chat/completions",
            headers=chat_headers,
            json=_chat_request_body(),
        )
        admin_bypass_completion = client.post(
            "/v1/chat/completions",
            headers=admin_headers,
            json=_chat_request_body(),
        )

    result = {
        "open_access_status": open_access.status_code,
        "open_password_enabled": open_access.json().get("password_enabled"),
        "open_completion_status": open_completion.status_code,
        "open_completion_access_unblocked": _access_unblocked(open_completion.status_code),
        "update_password_status": update_password.status_code,
        "protected_access_status": protected_access.status_code,
        "protected_password_enabled": protected_access.json().get("password_enabled"),
        "protected_configured": protected_access.json().get("configured"),
        "blocked_completion_status": blocked_completion.status_code,
        "blocked_completion_rejected_by_chat_access": blocked_completion.status_code == 401,
        "wrong_login_status": wrong_login.status_code,
        "good_login_status": good_login.status_code,
        "chat_session_completion_status": session_completion.status_code,
        "chat_session_access_unblocked": _access_unblocked(session_completion.status_code),
        "admin_bypass_completion_status": admin_bypass_completion.status_code,
        "admin_bypass_access_unblocked": _access_unblocked(admin_bypass_completion.status_code),
        "stored_chat_password_enabled": get_chat_auth().get("enabled"),
        "stored_chat_password_configured": bool(get_chat_auth().get("password_hash")),
    }
    print(json.dumps(result, ensure_ascii=False))
finally:
    store.save_config(original_config)

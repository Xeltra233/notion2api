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

from app.server import app  # noqa: E402
from scripts.admin_session_test_utils import build_admin_session_headers  # noqa: E402


with TestClient(app) as client:
    headers = build_admin_session_headers(client)

    current = client.get("/v1/admin/config", headers=headers)
    current.raise_for_status()
    settings = current.json()["settings"]

    invalid_payload = {
        "app_mode": settings.get("app_mode", "standard"),
        "allowed_origins": settings.get("allowed_origins", []),
        "upstream_proxy": settings.get("upstream_proxy", ""),
        "upstream_http_proxy": settings.get("upstream_http_proxy", ""),
        "upstream_https_proxy": settings.get("upstream_https_proxy", ""),
        "upstream_socks5_proxy": settings.get("upstream_socks5_proxy", ""),
        "upstream_proxy_mode": settings.get("upstream_proxy_mode", "direct"),
        "upstream_warp_enabled": settings.get("upstream_warp_enabled", False),
        "upstream_warp_proxy": settings.get("upstream_warp_proxy", ""),
        "auto_create_workspace": settings.get("auto_create_workspace", False),
        "auto_select_workspace": settings.get("auto_select_workspace", True),
        "workspace_create_dry_run": settings.get("workspace_create_dry_run", True),
        "workspace_creation_template_space_id": settings.get("workspace_creation_template_space_id", ""),
        "account_probe_interval_seconds": settings.get("account_probe_interval_seconds", 300),
        "auto_register_enabled": settings.get("auto_register_enabled", False),
        "auto_register_idle_only": settings.get("auto_register_idle_only", True),
        "auto_register_interval_seconds": settings.get("auto_register_interval_seconds", 1800),
        "auto_register_min_spacing_seconds": settings.get("auto_register_min_spacing_seconds", 900),
        "auto_register_busy_cooldown_seconds": settings.get("auto_register_busy_cooldown_seconds", 1200),
        "auto_register_batch_size": settings.get("auto_register_batch_size", 1),
        "auto_register_headless": settings.get("auto_register_headless", False),
        "auto_register_use_api": settings.get("auto_register_use_api", True),
        "auto_register_mail_provider": settings.get("auto_register_mail_provider", "freemail"),
        "auto_register_mail_base_url": settings.get("auto_register_mail_base_url", ""),
        "auto_register_domain": settings.get("auto_register_domain", ""),
        "refresh_execution_mode": settings.get("refresh_execution_mode", "manual"),
        "refresh_request_url": "http://127.0.0.1:9999/oauth/token",
        "refresh_client_id": settings.get("refresh_client_id", ""),
        "workspace_execution_mode": settings.get("workspace_execution_mode", "manual"),
        "workspace_request_url": "https://example.com/workspace",
        "allow_real_probe_requests": settings.get("allow_real_probe_requests", False),
    }

    invalid_resp = client.put("/v1/admin/config/settings", headers=headers, json=invalid_payload)

    valid_payload = dict(invalid_payload)
    valid_payload["refresh_request_url"] = "https://example.com/oauth/token"
    valid_resp = client.put("/v1/admin/config/settings", headers=headers, json=valid_payload)

    health = client.get("/health")

    print(
        json.dumps(
            {
                "invalid_status": invalid_resp.status_code,
                "invalid_detail": invalid_resp.json().get("detail"),
                "valid_status": valid_resp.status_code,
                "valid_refresh_request_url": valid_resp.json().get("settings", {}).get("refresh_request_url"),
                "health_status": health.status_code,
                "health_has_account_details": "account_details" in health.json(),
            },
            ensure_ascii=False,
        )
    )

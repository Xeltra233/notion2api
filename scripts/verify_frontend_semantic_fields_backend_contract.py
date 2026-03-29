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

    safe_accounts = client.get("/v1/admin/accounts/safe?page=1&page_size=1", headers=headers)
    alerts = client.get("/v1/admin/alerts", headers=headers)
    operations = client.get("/v1/admin/operations", headers=headers)
    proxy_health = client.get("/v1/admin/config/proxy-health", headers=headers)
    request_templates = client.get("/v1/admin/request-templates", headers=headers)

    safe_accounts.raise_for_status()
    alerts.raise_for_status()
    operations.raise_for_status()
    proxy_health.raise_for_status()
    request_templates.raise_for_status()

    print(
        json.dumps(
            {
                "accounts_view_mode": safe_accounts.json().get("view_mode"),
                "alerts_response_mode": alerts.json().get("response_mode"),
                "operations_response_mode": operations.json().get("response_mode"),
                "proxy_health_response_mode": proxy_health.json().get("response_mode"),
                "request_templates_response_mode": request_templates.json().get("response_mode"),
            },
            ensure_ascii=False,
        )
    )

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

    safe_list = client.get("/v1/admin/accounts/safe?page=1&page_size=1", headers=headers)
    safe_list.raise_for_status()
    account_id = ((safe_list.json().get("accounts") or [{}])[0]).get("id")

    proxy_health = client.get("/v1/admin/config/proxy-health", headers=headers)
    alerts = client.get("/v1/admin/alerts", headers=headers)
    operations = client.get("/v1/admin/operations", headers=headers)
    request_templates = client.get("/v1/admin/request-templates", headers=headers)
    account_templates = client.get(f"/v1/admin/accounts/{account_id}/request-templates", headers=headers)

    proxy_health.raise_for_status()
    alerts.raise_for_status()
    operations.raise_for_status()
    request_templates.raise_for_status()
    account_templates.raise_for_status()

    print(
        json.dumps(
            {
                "proxy_health_mode": proxy_health.json().get("response_mode"),
                "proxy_health_contains_secrets": proxy_health.json().get("contains_secrets"),
                "alerts_mode": alerts.json().get("response_mode"),
                "alerts_contains_secrets": alerts.json().get("contains_secrets"),
                "operations_mode": operations.json().get("response_mode"),
                "operations_contains_secrets": operations.json().get("contains_secrets"),
                "request_templates_mode": request_templates.json().get("response_mode"),
                "request_templates_contains_secrets": request_templates.json().get("contains_secrets"),
                "account_templates_mode": account_templates.json().get("response_mode"),
                "account_templates_contains_secrets": account_templates.json().get("contains_secrets"),
            },
            ensure_ascii=False,
        )
    )

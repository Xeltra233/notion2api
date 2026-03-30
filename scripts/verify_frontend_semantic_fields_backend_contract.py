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

from app.config import get_config_store  # noqa: E402
from app.server import app  # noqa: E402
from scripts.admin_session_test_utils import build_admin_session_headers  # noqa: E402


store = get_config_store()
original_config = copy.deepcopy(store.get_config())

try:
    with TestClient(app) as client:
        headers = build_admin_session_headers(client)

        safe_accounts = client.get("/v1/admin/accounts/safe?page=1&page_size=1", headers=headers)
        alerts = client.get("/v1/admin/alerts", headers=headers)
        operations = client.get("/v1/admin/operations", headers=headers)
        proxy_health = client.get("/v1/admin/config/proxy-health", headers=headers)
        request_templates = client.get("/v1/admin/request-templates", headers=headers)
        admin_config = client.get("/v1/admin/config", headers=headers)
        chat_access = client.get("/v1/chat/access", headers=headers)

        safe_accounts.raise_for_status()
        alerts.raise_for_status()
        operations.raise_for_status()
        proxy_health.raise_for_status()
        request_templates.raise_for_status()
        admin_config.raise_for_status()
        chat_access.raise_for_status()

        settings = admin_config.json().get("settings", {})

        print(
            json.dumps(
                {
                    "accounts_view_mode": safe_accounts.json().get("view_mode"),
                    "alerts_response_mode": alerts.json().get("response_mode"),
                    "operations_response_mode": operations.json().get("response_mode"),
                    "proxy_health_response_mode": proxy_health.json().get("response_mode"),
                    "request_templates_response_mode": request_templates.json().get("response_mode"),
                    "has_chat_enabled_field": "chat_enabled" in settings,
                    "has_chat_password_enabled_field": "chat_password_enabled" in settings,
                    "has_chat_password_field": "chat_password" in settings,
                    "has_has_chat_password_field": "has_chat_password" in settings,
                    "has_media_public_base_url_field": "media_public_base_url" in settings,
                    "has_media_storage_path_field": "media_storage_path" in settings,
                    "chat_access_ok": chat_access.json().get("ok"),
                    "chat_access_has_chat_enabled": "chat_enabled" in chat_access.json(),
                    "chat_access_has_password_enabled": "password_enabled" in chat_access.json(),
                    "chat_access_has_configured": "configured" in chat_access.json(),
                },
                ensure_ascii=False,
            )
        )
finally:
    store.save_config(original_config)

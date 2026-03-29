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

    response = client.get("/v1/admin/config", headers=headers)
    response.raise_for_status()
    payload = response.json()

    print(
        json.dumps(
            {
                "ok": payload.get("ok"),
                "redaction_mode": payload.get("redaction_mode"),
                "settings_view_mode": payload.get("settings_view_mode"),
                "accounts_view_mode": payload.get("accounts_view_mode"),
                "api_key": payload.get("settings", {}).get("api_key"),
                "has_api_key": payload.get("settings", {}).get("has_api_key"),
            },
            ensure_ascii=False,
        )
    )

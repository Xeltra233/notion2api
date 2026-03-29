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

    config_resp = client.get("/v1/admin/config", headers=headers)
    snapshot_resp = client.get("/v1/admin/snapshot", headers=headers)
    report_resp = client.get("/v1/admin/report", headers=headers)

    config_resp.raise_for_status()
    snapshot_resp.raise_for_status()
    report_resp.raise_for_status()

    config_payload = config_resp.json()
    snapshot_payload = snapshot_resp.json()
    report_payload = report_resp.json()

    print(
        json.dumps(
            {
                "config_ok": config_payload.get("ok"),
                "config_redaction_mode": config_payload.get("redaction_mode"),
                "config_settings_view_mode": config_payload.get("settings_view_mode"),
                "config_accounts_view_mode": config_payload.get("accounts_view_mode"),
                "config_api_key_masked": config_payload.get("settings", {}).get("api_key"),
                "snapshot_redaction_mode": snapshot_payload.get("redaction_mode"),
                "snapshot_settings_view_mode": snapshot_payload.get("settings_view_mode"),
                "snapshot_accounts_view_mode": snapshot_payload.get("accounts_view_mode"),
                "report_redaction_mode": report_payload.get("redaction_mode"),
                "report_settings_view_mode": report_payload.get("settings_view_mode"),
                "report_accounts_view_mode": report_payload.get("accounts_view_mode"),
                "report_api_key_masked": report_payload.get("settings", {}).get("api_key"),
                "report_first_account_token_v2": ((report_payload.get("accounts") or [{}])[0]).get("token_v2"),
            },
            ensure_ascii=False,
        )
    )

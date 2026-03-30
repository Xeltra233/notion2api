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

from app.config import update_admin_credentials  # noqa: E402
from app.server import app  # noqa: E402
from scripts.admin_session_test_utils import build_admin_session_headers  # noqa: E402


update_admin_credentials(username="admin", password="test-admin-password")

with TestClient(app) as client:
    headers = build_admin_session_headers(client)

    safe_list = client.get("/v1/admin/accounts/safe?page=1&page_size=1", headers=headers)
    raw_list = client.get("/v1/admin/accounts?page=1&page_size=1", headers=headers)
    safe_export = client.get("/v1/admin/accounts/export", headers=headers)
    raw_export = client.get("/v1/admin/accounts/export?raw=true", headers=headers)
    operations = client.get("/v1/admin/operations", headers=headers)

    safe_list.raise_for_status()
    raw_list.raise_for_status()
    safe_export.raise_for_status()
    raw_export.raise_for_status()
    operations.raise_for_status()

    safe_account = (safe_list.json().get("accounts") or [{}])[0]
    raw_account = (raw_list.json().get("accounts") or [{}])[0]
    operation_rows = operations.json().get("operations") or []
    export_actions = [row for row in operation_rows if row.get("action") == "accounts_export"]
    latest_export = export_actions[0] if export_actions else {}

    print(
        json.dumps(
            {
                "safe_list_status": safe_list.status_code,
                "raw_list_status": raw_list.status_code,
                "safe_token_v2": safe_account.get("token_v2"),
                "raw_has_token_v2": bool(str(raw_account.get("token_v2") or "").strip()),
                "safe_export_mode": safe_export.json().get("export_mode"),
                "raw_export_mode": raw_export.json().get("export_mode"),
                "safe_export_token_v2": ((safe_export.json().get("accounts") or [{}])[0]).get("token_v2"),
                "raw_export_has_token_v2": bool(str(((raw_export.json().get("accounts") or [{}])[0]).get("token_v2") or "").strip()),
                "export_log_count": len(export_actions),
                "latest_export_log": latest_export,
                "latest_export_mode": latest_export.get("export_mode"),
            },
            ensure_ascii=False,
        )
    )

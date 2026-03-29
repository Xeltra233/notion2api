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

    raw_list = client.get("/v1/admin/accounts?page=1&page_size=1&state=workspace_hydration_due", headers=headers)
    operations = client.get("/v1/admin/operations", headers=headers)

    raw_list.raise_for_status()
    operations.raise_for_status()

    operation_rows = operations.json().get("operations") or []
    raw_list_actions = [row for row in operation_rows if row.get("action") == "accounts_list_raw"]
    latest_raw_list = raw_list_actions[0] if raw_list_actions else {}

    print(
        json.dumps(
            {
                "raw_list_status": raw_list.status_code,
                "raw_view_mode": raw_list.json().get("view_mode"),
                "raw_list_count": len(raw_list.json().get("accounts") or []),
                "raw_list_audit_count": len(raw_list_actions),
                "latest_raw_list_audit": latest_raw_list,
                "latest_raw_list_state_filter": ((latest_raw_list.get("filters") or {}).get("state")),
            },
            ensure_ascii=False,
        )
    )

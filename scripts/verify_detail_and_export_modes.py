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

    detail = client.get(f"/v1/admin/accounts/{account_id}", headers=headers)
    safe_export = client.get("/v1/admin/accounts/export", headers=headers)
    raw_export = client.get("/v1/admin/accounts/export?raw=true", headers=headers)

    detail.raise_for_status()
    safe_export.raise_for_status()
    raw_export.raise_for_status()

    print(
        json.dumps(
            {
                "detail_view_mode": detail.json().get("view_mode"),
                "safe_export_mode": safe_export.json().get("export_mode"),
                "safe_export_view_mode": safe_export.json().get("view_mode"),
                "raw_export_mode": raw_export.json().get("export_mode"),
                "raw_export_view_mode": raw_export.json().get("view_mode"),
                "safe_export_token_v2": ((safe_export.json().get("accounts") or [{}])[0]).get("token_v2"),
                "raw_export_has_token_v2": bool(str(((raw_export.json().get("accounts") or [{}])[0]).get("token_v2") or "").strip()),
            },
            ensure_ascii=False,
        )
    )

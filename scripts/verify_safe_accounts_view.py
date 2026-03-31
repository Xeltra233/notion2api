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

    raw_resp = client.get("/v1/admin/accounts?page=1&page_size=1", headers=headers)
    safe_resp = client.get(
        "/v1/admin/accounts/safe?page=1&page_size=1", headers=headers
    )

    raw_resp.raise_for_status()
    safe_resp.raise_for_status()

    raw_account = (raw_resp.json().get("accounts") or [{}])[0]
    safe_account = (safe_resp.json().get("accounts") or [{}])[0]
    safe_session = safe_account.get("session") or {}

    print(
        json.dumps(
            {
                "raw_status": raw_resp.status_code,
                "safe_status": safe_resp.status_code,
                "safe_view_mode": safe_resp.json().get("view_mode"),
                "raw_has_token_v2": bool(
                    str(raw_account.get("token_v2") or "").strip()
                ),
                "safe_token_v2": safe_account.get("token_v2"),
                "safe_has_token_v2": safe_account.get("has_token_v2"),
                "safe_access_token": safe_session.get("access_token"),
                "safe_has_access_token": safe_session.get("has_access_token"),
                "summary_same_total": raw_resp.json().get("summary", {}).get("total")
                == safe_resp.json().get("summary", {}).get("total"),
            },
            ensure_ascii=False,
        )
    )

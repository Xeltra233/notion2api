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
    safe_account = (safe_list.json().get("accounts") or [{}])[0]
    account_id = safe_account.get("id")

    detail = client.get(f"/v1/admin/accounts/{account_id}", headers=headers)
    detail.raise_for_status()
    detail_account = detail.json().get("account") or {}

    print(
        json.dumps(
            {
                "account_id": account_id,
                "safe_view_mode": safe_list.json().get("view_mode"),
                "safe_token_v2": safe_account.get("token_v2"),
                "detail_has_token_v2": bool(str(detail_account.get("token_v2") or "").strip()),
                "detail_access_token_present": bool(
                    str(((detail_account.get("oauth") or {}).get("access_token") or "").strip())
                ),
                "detail_matches_safe_id": detail_account.get("id") == account_id,
            },
            ensure_ascii=False,
        )
    )

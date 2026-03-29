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

from app.server import app  # noqa: E402
from app.usage import UsageStore  # noqa: E402
from app.config import get_config_store  # noqa: E402


store = get_config_store()
original_config = copy.deepcopy(store.get_config())
usage_db_path = ROOT / "data" / "usage-verification.sqlite3"

try:
    store.update_config(
        {
            "admin_auth": {
                "username": "admin",
                "password_hash": "",
                "password_salt": "",
                "must_change_password": True,
                "initialized_from_default": True,
                "updated_at": 0,
            },
            "db_path": str(usage_db_path),
        }
    )

    usage_store = UsageStore(str(usage_db_path))
    usage_store.record_event(
        request_id="chatcmpl-demo-1",
        request_type="chat.completions",
        stream=False,
        model="claude-opus-4-6",
        account_id="acct-001",
        prompt_tokens=120,
        completion_tokens=45,
        total_tokens=165,
        conversation_id="conv-001",
        created_at=1710000000,
    )
    usage_store.record_event(
        request_id="resp-demo-1",
        request_type="responses",
        stream=False,
        model="claude-opus-4-6",
        account_id="acct-001",
        prompt_tokens=60,
        completion_tokens=20,
        total_tokens=80,
        conversation_id="",
        created_at=1710003600,
    )

    with TestClient(app) as client:
        auth_headers = {"Authorization": "Bearer test-server-key"}
        login = client.post(
            "/v1/admin/login",
            headers=auth_headers,
            json={"username": "admin", "password": "test-admin-password"},
        )
        login.raise_for_status()
        session_headers = {
            **auth_headers,
            "X-Admin-Session": login.json().get("session_token", ""),
        }
        rotate = client.post(
            "/v1/admin/change-password",
            headers=session_headers,
            json={
                "current_password": "test-admin-password",
                "new_password": "test-admin-password-rotated",
                "new_username": "ops-admin",
            },
        )
        rotate.raise_for_status()
        admin_headers = {
            **auth_headers,
            "X-Admin-Session": rotate.json().get("session_token", ""),
        }

        summary = client.get("/v1/admin/usage/summary", headers=admin_headers)
        filtered = client.get(
            "/v1/admin/usage/summary?request_type=responses",
            headers=admin_headers,
        )
        events = client.get(
            "/v1/admin/usage/events?limit=1&offset=0",
            headers=admin_headers,
        )

    payload = {
        "summary_status": summary.status_code,
        "summary_request_count": summary.json().get("summary", {}).get("request_count"),
        "summary_total_tokens": summary.json().get("summary", {}).get("total_tokens"),
        "summary_distinct_models": summary.json().get("summary", {}).get("distinct_models"),
        "filtered_status": filtered.status_code,
        "filtered_request_count": filtered.json().get("summary", {}).get("request_count"),
        "filtered_total_tokens": filtered.json().get("summary", {}).get("total_tokens"),
        "events_status": events.status_code,
        "events_total": events.json().get("total"),
        "events_first_request_type": ((events.json().get("events") or [{}])[0].get("request_type")),
        "events_first_total_tokens": ((events.json().get("events") or [{}])[0].get("total_tokens")),
    }
    print(json.dumps(payload, ensure_ascii=False))
finally:
    store.save_config(original_config)
    try:
        usage_db_path.unlink()
    except FileNotFoundError:
        pass

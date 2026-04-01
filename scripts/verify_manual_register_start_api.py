import json
import os
import sys
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("API_KEY", "")
os.environ.setdefault("ADMIN_PASSWORD", "test-admin-password")

from app.server import app  # noqa: E402


with TestClient(app) as client:
    login = client.post(
        "/v1/admin/login",
        json={"username": "admin", "password": "test-admin-password"},
        headers={"X-Client-Type": "Web"},
    )
    login.raise_for_status()
    token = login.json().get("session_token", "")
    start = client.post(
        "/v1/register/start",
        json={
            "count": 7,
            "mail_provider": "freemail",
            "mail_base_url": "https://mail.speacecc.xyz",
            "mail_api_key": "dummy-key",
            "domain": "zhatianbang66fasdgewfas.dpdns.org",
            "use_api": True,
            "headless": True,
        },
        headers={
            "X-Admin-Session": token,
            "X-Client-Type": "Web",
        },
    )
    payload = start.json()
    result = {
        "login_status": login.status_code,
        "start_status": start.status_code,
        "payload": payload,
    }

assert result["login_status"] == 200, result
assert result["start_status"] == 200, result
assert payload.get("status") == "queued", result
assert bool(payload.get("task_id")), result

print(json.dumps(result, ensure_ascii=True))

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

from app.config import get_admin_auth, get_config_store  # noqa: E402
from app.server import app  # noqa: E402


store = get_config_store()
original_config = copy.deepcopy(store.get_config())

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
            }
        }
    )

    with TestClient(app) as client:
        auth_headers = {"Authorization": "Bearer test-server-key"}

        bad_login = client.post(
            "/v1/admin/login",
            headers=auth_headers,
            json={"username": "admin", "password": "wrong-password"},
        )
        login = client.post(
            "/v1/admin/login",
            headers=auth_headers,
            json={"username": "admin", "password": "test-admin-password"},
        )
        login.raise_for_status()
        login_payload = login.json()
        session_token = login_payload.get("session_token", "")
        session_headers = {
            **auth_headers,
            "X-Admin-Session": session_token,
        }

        blocked_before_change = client.get("/v1/admin/config", headers=session_headers)
        change_password = client.post(
            "/v1/admin/change-password",
            headers=session_headers,
            json={
                "current_password": "test-admin-password",
                "new_password": "test-admin-password-rotated",
                "new_username": "ops-admin",
            },
        )
        change_password.raise_for_status()
        changed_payload = change_password.json()

        old_session_headers = {
            **auth_headers,
            "X-Admin-Session": session_token,
        }
        old_session_after_change = client.get("/v1/admin/config", headers=old_session_headers)

        old_login_after_change = client.post(
            "/v1/admin/login",
            headers=auth_headers,
            json={"username": "admin", "password": "test-admin-password"},
        )
        new_login = client.post(
            "/v1/admin/login",
            headers=auth_headers,
            json={"username": "ops-admin", "password": "test-admin-password-rotated"},
        )
        new_login.raise_for_status()
        new_session_headers = {
            **auth_headers,
            "X-Admin-Session": new_login.json().get("session_token", ""),
        }
        config_after_change = client.get("/v1/admin/config", headers=new_session_headers)
        register_after_change = client.get("/v1/register/tasks", headers=new_session_headers)

    result = {
        "bad_login_status": bad_login.status_code,
        "must_change_password": login_payload.get("must_change_password"),
        "initialized_from_default": login_payload.get("initialized_from_default"),
        "blocked_before_change_status": blocked_before_change.status_code,
        "blocked_before_change_detail": blocked_before_change.json().get("detail"),
        "change_password_status": change_password.status_code,
        "changed_username": changed_payload.get("username"),
        "old_session_after_change_status": old_session_after_change.status_code,
        "old_login_after_change_status": old_login_after_change.status_code,
        "new_login_status": new_login.status_code,
        "config_after_change_status": config_after_change.status_code,
        "register_after_change_status": register_after_change.status_code,
        "stored_admin_username": get_admin_auth().get("username"),
        "stored_must_change_password": get_admin_auth().get("must_change_password"),
    }
    print(json.dumps(result, ensure_ascii=False))
finally:
    store.save_config(original_config)

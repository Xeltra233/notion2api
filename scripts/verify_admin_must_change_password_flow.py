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
os.environ.setdefault("ADMIN_PASSWORD", "bootstrap-admin-password")

from app.config import (  # noqa: E402
    get_admin_auth,
    get_config_store,
    update_admin_credentials,
)
from app.server import app  # noqa: E402


store = get_config_store()
original_config = copy.deepcopy(store.get_config())

try:
    update_admin_credentials(
        username="admin",
        password="bootstrap-admin-password",
        must_change_password=True,
        initialized_from_default=True,
    )

    with TestClient(app) as client:
        auth_headers = {"Authorization": "Bearer test-server-key"}
        login = client.post(
            "/v1/admin/login",
            headers=auth_headers,
            json={"username": "admin", "password": "bootstrap-admin-password"},
        )
        login.raise_for_status()
        login_payload = login.json()
        session_headers = {
            **auth_headers,
            "X-Admin-Session": login_payload.get("session_token", ""),
        }

        blocked_config = client.get("/v1/admin/config", headers=session_headers)
        change_password = client.post(
            "/v1/admin/change-password",
            headers=session_headers,
            json={
                "current_password": "bootstrap-admin-password",
                "new_password": "bootstrap-admin-password-rotated",
                "new_username": "admin",
            },
        )
        change_password.raise_for_status()
        rotated_payload = change_password.json()
        rotated_headers = {
            **auth_headers,
            "X-Admin-Session": rotated_payload.get("session_token", ""),
        }
        unlocked_config = client.get("/v1/admin/config", headers=rotated_headers)

    result = {
        "login_status": login.status_code,
        "login_must_change_password": login_payload.get("must_change_password"),
        "blocked_config_status": blocked_config.status_code,
        "blocked_config_detail": blocked_config.json().get("detail"),
        "change_password_status": change_password.status_code,
        "rotated_must_change_password": rotated_payload.get("must_change_password"),
        "unlocked_config_status": unlocked_config.status_code,
        "stored_must_change_password": get_admin_auth().get("must_change_password"),
    }
    print(json.dumps(result, ensure_ascii=False))
finally:
    store.save_config(original_config)

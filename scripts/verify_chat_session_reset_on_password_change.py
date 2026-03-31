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

from app.config import get_config_store, update_admin_credentials  # noqa: E402
from app.api import admin as admin_api  # noqa: E402
from app.server import app  # noqa: E402
from scripts.admin_session_test_utils import build_admin_session_headers  # noqa: E402


store = get_config_store()
original_config = copy.deepcopy(store.get_config())
original_rebuild_pool = admin_api._rebuild_pool

try:
    update_admin_credentials(username="admin", password="test-admin-password")
    admin_api._rebuild_pool = lambda request: None
    with TestClient(app) as client:
        auth_headers = {"Authorization": "Bearer test-server-key"}
        admin_headers = build_admin_session_headers(client)

        save_runtime = client.put(
            "/v1/admin/config/settings",
            headers=admin_headers,
            json={
                "chat_enabled": True,
                "chat_password_enabled": True,
                "chat_password": "chat-secret-one",
            },
        )
        save_runtime.raise_for_status()

        chat_login = client.post(
            "/v1/chat/login",
            headers=auth_headers,
            json={"password": "chat-secret-one"},
        )
        chat_login.raise_for_status()
        chat_headers = {
            **auth_headers,
            "X-Chat-Session": chat_login.json().get("session_token", ""),
        }

        before_change = client.delete(
            "/v1/conversations/test-conversation",
            headers=chat_headers,
        )

        rotate_runtime = client.put(
            "/v1/admin/config/settings",
            headers=admin_headers,
            json={
                "chat_enabled": True,
                "chat_password_enabled": True,
                "chat_password": "chat-secret-two",
            },
        )
        rotate_runtime.raise_for_status()

        after_change = client.delete(
            "/v1/conversations/test-conversation",
            headers=chat_headers,
        )
        new_chat_login = client.post(
            "/v1/chat/login",
            headers=auth_headers,
            json={"password": "chat-secret-two"},
        )
        new_chat_login.raise_for_status()

    output = {
        "before_change_status": before_change.status_code,
        "before_change_payload": before_change.json(),
        "after_change_status": after_change.status_code,
        "after_change_payload": after_change.json(),
        "new_chat_login_status": new_chat_login.status_code,
    }

    assert before_change.status_code == 400, output
    assert after_change.status_code == 401, output
    assert after_change.json() == {"detail": "Invalid chat session"}, output
    assert new_chat_login.status_code == 200, output

    print(json.dumps(output, ensure_ascii=False))
finally:
    admin_api._rebuild_pool = original_rebuild_pool
    store.save_config(original_config)

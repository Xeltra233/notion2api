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

from app.config import get_config_store  # noqa: E402
from app.server import app  # noqa: E402
from scripts.admin_session_test_utils import build_admin_session_headers  # noqa: E402


store = get_config_store()
original_config = copy.deepcopy(store.get_config())

try:
    with TestClient(app) as client:
        auth_only_headers = {"Authorization": "Bearer test-server-key"}
        admin_headers = build_admin_session_headers(client)

        denied = client.get("/v1/register/tasks", headers=auth_only_headers)
        allowed = client.get("/v1/register/tasks", headers=admin_headers)
        health = client.get("/health")

        result = {
            "auth_only_status": denied.status_code,
            "auth_only_detail": denied.json().get("detail"),
            "admin_status": allowed.status_code,
            "admin_payload_type": type(allowed.json()).__name__,
            "health_status": health.status_code,
            "health_has_account_details": "account_details" in health.json(),
        }
        print(json.dumps(result, ensure_ascii=False))
finally:
    store.save_config(original_config)

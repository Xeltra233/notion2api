import copy
import json
import os
import sys
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("API_KEY", "model-api-key")
os.environ.setdefault("ADMIN_PASSWORD", "test-admin-password")

from app.config import get_config_store, update_admin_credentials  # noqa: E402
from app.server import app  # noqa: E402
from scripts.admin_session_test_utils import build_admin_session_headers  # noqa: E402


store = get_config_store()
original_config = copy.deepcopy(store.get_config())

try:
    cfg = copy.deepcopy(original_config)
    cfg["api_key"] = "model-api-key"
    store.save_config(cfg)
    update_admin_credentials(username="admin", password="test-admin-password")

    with TestClient(app) as client:
        admin_headers = build_admin_session_headers(client)
        response = client.get(
            "/v1/register/status/non-existent-task-id",
            headers=admin_headers,
        )

    output = {
        "status": response.status_code,
        "payload": response.json(),
    }

    assert response.status_code == 404, output
    assert response.json() == {"detail": "任务不存在"}, output

    print(json.dumps(output, ensure_ascii=False))
finally:
    store.save_config(original_config)

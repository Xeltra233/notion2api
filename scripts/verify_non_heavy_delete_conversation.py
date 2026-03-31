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


store = get_config_store()
original_config = copy.deepcopy(store.get_config())

try:
    config = copy.deepcopy(original_config)
    config["app_mode"] = "standard"
    config["chat_enabled"] = True
    config["chat_auth"] = {
        "password_hash": "",
        "password_salt": "",
        "enabled": False,
        "updated_at": 0,
    }
    store.save_config(config)

    with TestClient(app) as client:
        response = client.delete(
            "/v1/conversations/demo-conversation",
            headers={"Authorization": "Bearer test-server-key"},
        )

    output = {
        "status": response.status_code,
        "payload": response.json(),
    }

    assert response.status_code == 400, output
    assert response.json() == {
        "detail": "Conversation management is only available in heavy mode."
    }, output

    print(json.dumps(output, ensure_ascii=False))
finally:
    store.save_config(original_config)

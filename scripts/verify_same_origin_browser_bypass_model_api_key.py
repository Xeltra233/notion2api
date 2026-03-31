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

from app.config import get_config_store  # noqa: E402
from app.server import app  # noqa: E402


store = get_config_store()
original_config = copy.deepcopy(store.get_config())

try:
    cfg = copy.deepcopy(original_config)
    cfg["api_key"] = "model-api-key"
    cfg["browser_trust_same_origin"] = True
    store.save_config(cfg)

    with TestClient(app, base_url="http://testserver") as client:
        response = client.get(
            "/v1/models",
            headers={
                "X-Client-Type": "Web",
                "Origin": "http://testserver",
                "Referer": "http://testserver/",
            },
        )

    output = {
        "status": response.status_code,
        "payload": response.json(),
    }

    assert response.status_code == 200, output
    assert "data" in response.json(), output

    print(json.dumps(output, ensure_ascii=True))
finally:
    store.save_config(original_config)

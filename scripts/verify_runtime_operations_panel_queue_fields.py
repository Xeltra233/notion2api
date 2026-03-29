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
    response = client.get("/v1/admin/register/auto-status", headers=headers)
    response.raise_for_status()
    panel = response.json().get("runtime_operations_panel") or {}
    automation = response.json().get("automation") or {}
    print(
        json.dumps(
            {
                "panel_current_reason": panel.get("current_reason"),
                "panel_gate_reason_present": "gate_reason" in panel,
                "panel_latest_task_status_present": "latest_task_status" in panel,
                "automation_gate_reason_present": "gate_reason" in automation,
                "automation_latest_task_status_present": "latest_task_status" in automation,
            },
            ensure_ascii=False,
        )
    )

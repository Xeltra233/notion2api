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
    payload = response.json()
    automation = payload.get("automation") or {}
    guidance = payload.get("guidance") or {}
    panel = payload.get("runtime_operations_panel") or {}

    print(
        json.dumps(
            {
                "response_mode": payload.get("response_mode"),
                "automation_has_current_reason": "current_reason" in automation,
                "automation_has_gate_reason": "gate_reason" in automation,
                "automation_has_latest_task_status": "latest_task_status" in automation,
                "guidance_has_message": bool(guidance.get("message")),
                "guidance_has_next_step": bool(guidance.get("next_step")),
                "panel_has_current_reason": "current_reason" in panel,
                "panel_has_gate_reason": "gate_reason" in panel,
                "panel_has_latest_task_status": "latest_task_status" in panel,
                "panel_current_matches_automation": panel.get("current_reason") == automation.get("current_reason"),
                "panel_gate_matches_automation": panel.get("gate_reason") == automation.get("gate_reason"),
                "panel_task_matches_automation": panel.get("latest_task_status") == automation.get("latest_task_status"),
            },
            ensure_ascii=False,
        )
    )

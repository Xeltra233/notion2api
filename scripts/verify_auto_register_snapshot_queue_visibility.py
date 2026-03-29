import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("API_KEY", "test-server-key")
os.environ.setdefault("ADMIN_PASSWORD", "test-admin-password")

from app.api import register as register_api  # noqa: E402


original_evaluate = register_api._evaluate_auto_register_gate
try:
    task_id = "queue-visibility-test"
    register_api.REGISTER_AUTOMATION_STATE.update(
        {
            "last_started_at": 123,
            "last_finished_at": 0,
            "last_task_id": task_id,
            "active": True,
            "last_decision_reason": "queued",
        }
    )
    register_api.REGISTER_TASKS[task_id] = {
        "task_id": task_id,
        "status": "queued",
        "progress": 0,
        "total": 1,
        "success_count": 0,
        "fail_count": 0,
        "logs": [],
        "results": [],
        "created_at": 123.0,
        "cancelled": False,
        "auto": True,
    }

    def fake_evaluate(now_ts=None):
        return {
            "allowed": False,
            "reason": "register_task_active",
            "proxy_mode": "direct",
            "proxy_gate_reason": "",
            "register_task_active": True,
            "pending_hydration_total": 0,
            "pending_hydration_due": 0,
            "pending_hydration_blocking": False,
            "spacing_remaining_seconds": 0,
            "busy_cooldown_remaining_seconds": 0,
            "next_eligible_at": 0,
        }

    register_api._evaluate_auto_register_gate = fake_evaluate
    snapshot = register_api.get_register_automation_snapshot()
    print(
        json.dumps(
            {
                "current_reason": snapshot.get("current_reason"),
                "gate_reason": snapshot.get("gate_reason"),
                "latest_task_status": snapshot.get("latest_task_status"),
                "last_decision_reason": snapshot.get("last_decision_reason"),
                "register_task_active": snapshot.get("register_task_active"),
            },
            ensure_ascii=False,
        )
    )
finally:
    register_api._evaluate_auto_register_gate = original_evaluate
    register_api.REGISTER_TASKS.pop("queue-visibility-test", None)

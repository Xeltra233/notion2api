import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("API_KEY", "test-server-key")
os.environ.setdefault("ADMIN_PASSWORD", "test-admin-password")

from app.api import register as register_api  # noqa: E402
from app.server import app  # noqa: E402


original_can_start = register_api._can_start_auto_register
original_start_thread = register_api._start_register_thread


register_api._can_start_auto_register = lambda now_ts=None: (True, "ok")
register_api._start_register_thread = lambda *args, **kwargs: None
try:
    request = SimpleNamespace(app=app)
    result = register_api.maybe_start_auto_register(request)
    task_id = result.get("task_id", "")
    task = dict(register_api.REGISTER_TASKS.get(task_id) or {})
    print(
        json.dumps(
            {
                "ok": result.get("ok"),
                "status": result.get("status"),
                "reason": result.get("reason"),
                "task_status": task.get("status"),
                "last_decision_reason": register_api.REGISTER_AUTOMATION_STATE.get("last_decision_reason"),
            },
            ensure_ascii=False,
        )
    )
finally:
    register_api._can_start_auto_register = original_can_start
    register_api._start_register_thread = original_start_thread
    if task_id:
        register_api.REGISTER_TASKS.pop(task_id, None)

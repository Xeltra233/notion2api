import json
import os
import sys
import time
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


def fake_run_register_task(request, task_id, count, *args):
    task = register_api.REGISTER_TASKS[task_id]
    task["status"] = "running"
    register_api._append_log(task_id, "info", "fake auto register started")
    time.sleep(0.2)
    task["progress"] = count
    task["success_count"] = count
    task["status"] = "completed"
    task["finished_at"] = time.time()
    register_api.REGISTER_AUTOMATION_STATE["active"] = False
    register_api.REGISTER_AUTOMATION_STATE["last_finished_at"] = int(time.time())
    register_api._append_log(task_id, "info", "fake auto register finished")


config = register_api.get_config_store().get_config()
config.update(
    {
        "auto_register_enabled": True,
        "auto_register_idle_only": False,
        "auto_register_batch_size": 1,
        "upstream_proxy_mode": "direct",
    }
)
register_api.get_config_store().save_config(config)

original_run = register_api._run_register_task
register_api._run_register_task = fake_run_register_task
register_api._can_start_auto_register = lambda now_ts=None: (True, "ok")
try:
    request = SimpleNamespace(app=app)
    start = time.time()
    result = register_api.maybe_start_auto_register(request)
    elapsed_ms = int((time.time() - start) * 1000)
    task_id = result.get("task_id", "")
    immediate = dict(register_api.REGISTER_TASKS.get(task_id) or {})
    immediate_state = dict(register_api.get_register_automation_snapshot())
    time.sleep(0.35)
    final = dict(register_api.REGISTER_TASKS.get(task_id) or {})
    final_state = dict(register_api.get_register_automation_snapshot())
    print(
        json.dumps(
            {
                "result": result,
                "elapsed_ms": elapsed_ms,
                "immediate_status": immediate.get("status"),
                "immediate_active": immediate_state.get("active"),
                "final_status": final.get("status"),
                "final_progress": final.get("progress"),
                "final_success_count": final.get("success_count"),
                "final_active": final_state.get("active"),
            },
            ensure_ascii=False,
        )
    )
finally:
    register_api._run_register_task = original_run
    register_api._can_start_auto_register = original_can_start

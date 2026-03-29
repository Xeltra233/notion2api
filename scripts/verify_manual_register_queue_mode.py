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


original_run = register_api._run_register_task


def fake_run_register_task(request, task_id, count, *args):
    task = register_api.REGISTER_TASKS[task_id]
    time.sleep(0.1)
    task["status"] = "running"
    task["progress"] = count
    task["success_count"] = count
    task["status"] = "completed"
    task["finished_at"] = time.time()


register_api._run_register_task = fake_run_register_task
try:
    request = SimpleNamespace(app=app)
    task_id = "manual-queue-test"
    register_api.REGISTER_TASKS[task_id] = {
        "task_id": task_id,
        "status": "queued",
        "progress": 0,
        "total": 1,
        "success_count": 0,
        "fail_count": 0,
        "logs": [],
        "results": [],
        "created_at": time.time(),
        "finished_at": None,
        "cancelled": False,
        "auto": False,
        "config": {},
    }
    register_api._start_register_thread(
        request,
        task_id,
        1,
        "freemail",
        None,
        None,
        None,
        True,
        False,
        None,
    )
    immediate = dict(register_api.REGISTER_TASKS.get(task_id) or {})
    time.sleep(0.25)
    final = dict(register_api.REGISTER_TASKS.get(task_id) or {})
    print(
        json.dumps(
            {
                "immediate_status": immediate.get("status"),
                "final_status": final.get("status"),
                "final_progress": final.get("progress"),
                "final_success_count": final.get("success_count"),
                "has_task_id": bool(final.get("task_id")),
                "auto_flag": final.get("auto"),
            },
            ensure_ascii=False,
        )
    )
finally:
    register_api._run_register_task = original_run
    register_api.REGISTER_TASKS.pop("manual-queue-test", None)

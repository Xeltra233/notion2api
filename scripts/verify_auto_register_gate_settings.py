import copy
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("ADMIN_PASSWORD", "test-admin-password")

from app.api import register as register_api  # noqa: E402
from app.config import get_config_store  # noqa: E402


store = get_config_store()
original_config = copy.deepcopy(store.get_config())
original_state = copy.deepcopy(register_api.REGISTER_AUTOMATION_STATE)
original_tasks = copy.deepcopy(register_api.REGISTER_TASKS)

try:
    store.update_config(
        {
            "auto_register_enabled": True,
            "auto_register_idle_only": True,
            "auto_register_interval_seconds": 3600,
            "auto_register_min_spacing_seconds": 900,
            "auto_register_busy_cooldown_seconds": 1200,
            "upstream_proxy_mode": "direct",
            "accounts": [],
            "action_history": [
                {"timestamp": 4900, "action": "refresh", "payload": {}},
            ],
        }
    )
    register_api.REGISTER_AUTOMATION_STATE.clear()
    register_api.REGISTER_AUTOMATION_STATE.update(
        {
            "active": False,
            "last_started_at": 2000,
            "last_decision_reason": "",
        }
    )
    register_api.REGISTER_TASKS.clear()

    spacing_eval = register_api._evaluate_auto_register_gate(5000)

    store.update_config(
        {
            "auto_register_enabled": True,
            "auto_register_idle_only": False,
            "auto_register_interval_seconds": 300,
            "auto_register_min_spacing_seconds": 300,
            "auto_register_busy_cooldown_seconds": 1200,
            "upstream_proxy_mode": "direct",
            "accounts": [],
            "action_history": [
                {"timestamp": 4900, "action": "refresh", "payload": {}},
            ],
        }
    )
    register_api.REGISTER_AUTOMATION_STATE["last_started_at"] = 0
    idle_disabled_eval = register_api._evaluate_auto_register_gate(5000)

    output = {
        "spacing_eval": spacing_eval,
        "idle_disabled_eval": idle_disabled_eval,
    }

    assert spacing_eval["allowed"] is False, spacing_eval
    assert spacing_eval["reason"] == "auto_register_spacing", spacing_eval
    assert spacing_eval["effective_spacing_seconds"] == 3600, spacing_eval
    assert spacing_eval["spacing_remaining_seconds"] == 600, spacing_eval

    assert idle_disabled_eval["allowed"] is True, idle_disabled_eval
    assert idle_disabled_eval["reason"] == "ok", idle_disabled_eval
    assert idle_disabled_eval["idle_only"] is False, idle_disabled_eval
    assert idle_disabled_eval["busy_cooldown_remaining_seconds"] == 1100, (
        idle_disabled_eval
    )

    print(json.dumps(output, ensure_ascii=False))
finally:
    store.save_config(original_config)
    register_api.REGISTER_AUTOMATION_STATE.clear()
    register_api.REGISTER_AUTOMATION_STATE.update(original_state)
    register_api.REGISTER_TASKS.clear()
    register_api.REGISTER_TASKS.update(original_tasks)

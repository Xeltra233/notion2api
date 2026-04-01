import copy
import json
import os
import sys
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("API_KEY", "")
os.environ.setdefault("ADMIN_PASSWORD", "test-admin-password")

from app.api import register as register_api  # noqa: E402
from app.config import get_config_store  # noqa: E402
from app.server import app  # noqa: E402


store = get_config_store()
original_config = copy.deepcopy(store.get_config())
original_start_thread = register_api._start_register_thread
captured = {}


def fake_start_thread(
    request,
    task_id,
    count,
    mail_provider,
    domain,
    mail_base_url,
    mail_api_key,
    use_api,
    headless,
    proxy,
):
    captured.update(
        {
            "task_id": task_id,
            "count": count,
            "mail_provider": mail_provider,
            "domain": domain,
            "mail_base_url": mail_base_url,
            "mail_api_key": mail_api_key,
            "use_api": use_api,
            "headless": headless,
            "proxy": proxy,
        }
    )


try:
    cfg = copy.deepcopy(original_config)
    cfg["auto_register_mail_provider"] = "freemail"
    cfg["auto_register_mail_base_url"] = "https://mail.speacecc.xyz"
    cfg["auto_register_mail_api_key"] = "runtime-mail-token"
    cfg["auto_register_domain"] = "zhatianbang66fasdgewfas.dpdns.org"
    store.save_config(cfg)
    register_api._start_register_thread = fake_start_thread

    with TestClient(app) as client:
        login = client.post(
            "/v1/admin/login",
            json={"username": "admin", "password": "test-admin-password"},
            headers={"X-Client-Type": "Web"},
        )
        login.raise_for_status()
        token = login.json().get("session_token", "")
        start = client.post(
            "/v1/register/start",
            json={
                "count": 5,
                "mail_provider": "freemail",
                "use_api": True,
                "headless": True,
            },
            headers={
                "X-Admin-Session": token,
                "X-Client-Type": "Web",
            },
        )

    output = {
        "status": start.status_code,
        "payload": start.json(),
        "captured": captured,
    }

    assert start.status_code == 200, output
    assert captured.get("mail_base_url") == "https://mail.speacecc.xyz", output
    assert captured.get("mail_api_key") == "runtime-mail-token", output
    assert captured.get("domain") == "zhatianbang66fasdgewfas.dpdns.org", output

    print(json.dumps(output, ensure_ascii=True))
finally:
    store.save_config(original_config)
    register_api._start_register_thread = original_start_thread

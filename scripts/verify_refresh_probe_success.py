import copy
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("ADMIN_PASSWORD", "test-admin-password")

from app.config import get_config_store, update_admin_credentials
from app.server import app
from scripts.admin_session_test_utils import build_admin_session_headers


ACCOUNT_ID = "0d8e0424-013f-4933-9324-70e7ad8bf32a"


class RefreshSuccessHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        content_length = int(self.headers.get("Content-Length") or 0)
        if content_length:
            self.rfile.read(content_length)
        if self.path == "/oauth/token":
            body = {
                "access_token": "cli-live-access",
                "refresh_token": "cli-live-refresh",
                "expires_in": 1800,
                "token_type": "Bearer",
                "scope": "workspace.read",
                "request_id": "cli-refresh-success",
            }
            encoded = json.dumps(body).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)
            return
        self.send_response(404)
        self.end_headers()

    def log_message(self, format, *args):
        return


def main() -> None:
    update_admin_credentials(username="admin", password="test-admin-password")
    store = get_config_store()
    original_config = copy.deepcopy(store.get_config())
    original_accounts = copy.deepcopy(store.get_accounts())

    server = ThreadingHTTPServer(("127.0.0.1", 8012), RefreshSuccessHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        config = store.get_config()
        config.update(
            {
                "refresh_execution_mode": "live_template",
                "refresh_request_url": "http://127.0.0.1:8012/oauth/token",
                "refresh_client_id": "client-id-demo",
                "refresh_client_secret": "client-secret-demo",
                "allow_real_probe_requests": True,
            }
        )
        store.save_config(config)

        accounts = store.get_accounts()
        target = next(item for item in accounts if item["id"] == ACCOUNT_ID)
        target.setdefault("status", {})
        target.setdefault("session", {})
        target.setdefault("session", {})
        target["status"].update(
            {
                "session_expired": True,
                "needs_refresh": True,
                "needs_reauth": True,
                "reauthorize_required": True,
                "last_refresh_error": "stale",
            }
        )
        target["session"].update(
            {
                "access_token": "old-access",
                "refresh_token": "old-refresh",
                "expired": True,
                "needs_refresh": True,
                "expires_at": 1,
            }
        )
        store.set_accounts(accounts)

        with TestClient(app) as client:
            headers = build_admin_session_headers(client)
            refresh_response = client.post(
                f"/v1/admin/accounts/{ACCOUNT_ID}/refresh-probe",
                headers=headers,
            )
            accounts_response = client.get("/v1/admin/accounts", headers=headers)
            snapshot_response = client.get("/v1/admin/snapshot", headers=headers)

        account = next(
            item
            for item in accounts_response.json()["accounts"]
            if item["id"] == ACCOUNT_ID
        )
        output = {
            "refresh_probe": refresh_response.json(),
            "account_status": account["status"],
            "session_expired": account["status"].get("session_expired"),
            "account_session": account.get("session") or {},
            "recent_probe": snapshot_response.json()["recent_probes"][-1],
        }
        print(json.dumps(output, ensure_ascii=False))
    finally:
        server.shutdown()
        thread.join(timeout=5)
        restore_config = copy.deepcopy(original_config)
        restore_config.pop("accounts", None)
        store.save_config(restore_config)
        store.set_accounts(original_accounts)


if __name__ == "__main__":
    main()

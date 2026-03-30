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


class WorkspaceSuccessHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        content_length = int(self.headers.get("Content-Length") or 0)
        if content_length:
            self.rfile.read(content_length)
        if self.path == "/saveTransactions":
            body = {
                "workspace_id": "workspace-cli-001",
                "workspace_ids": ["workspace-cli-001", "workspace-cli-002"],
                "transaction_id": "txn-cli-001",
                "workspace_name": "CLI Workspace",
                "workspace_slug": "cli-workspace",
                "subscription_tier": "business",
                "space_view_id": "cli-space-view-001",
                "request_id": "cli-workspace-success",
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

    server = ThreadingHTTPServer(("127.0.0.1", 8013), WorkspaceSuccessHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        config = store.get_config()
        config.update(
            {
                "workspace_execution_mode": "live_template",
                "workspace_request_url": "http://127.0.0.1:8013/saveTransactions",
                "allow_real_probe_requests": True,
            }
        )
        store.save_config(config)

        accounts = store.get_accounts()
        target = next(item for item in accounts if item["id"] == ACCOUNT_ID)
        target.setdefault("status", {})
        target.setdefault("workspace", {})
        target["space_id"] = "legacy-space-cli"
        target["space_view_id"] = "legacy-space-view-cli"
        target["status"].update(
            {
                "workspace_state": "workspace_creation_pending",
                "last_workspace_error": "stale workspace error",
            }
        )
        target["workspace"].update(
            {
                "state": "missing",
                "workspace_count": 0,
                "subscription_tier": "",
                "workspaces": [
                    {
                        "id": "legacy-space-cli",
                        "name": "Legacy CLI Workspace",
                        "space_view_id": "legacy-space-view-cli",
                        "subscription_tier": "free",
                    }
                ],
            }
        )
        store.set_accounts(accounts)

        with TestClient(app) as client:
            headers = build_admin_session_headers(client)
            workspace_response = client.post(
                f"/v1/admin/accounts/{ACCOUNT_ID}/workspace-probe",
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
            "workspace_probe": workspace_response.json(),
            "account_space_id": account["space_id"],
            "account_space_view_id": account["space_view_id"],
            "account_status": account["status"],
            "account_workspace": account["workspace"],
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

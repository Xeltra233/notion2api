import copy
import json
import os
import sys
from pathlib import Path

from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("ADMIN_PASSWORD", "test-admin-password")

from app.account_pool import AccountPool
from app.config import get_config_store, update_admin_credentials
from app.server import app
from scripts.admin_session_test_utils import build_admin_session_headers


ACCOUNT_ID = "0d8e0424-013f-4933-9324-70e7ad8bf32a"


def main() -> None:
    update_admin_credentials(username="admin", password="test-admin-password")
    store = get_config_store()
    original_config = copy.deepcopy(store.get_config())
    original_accounts = copy.deepcopy(store.get_accounts())
    original_method = AccountPool.refresh_account_by_id

    def fake_refresh_account_by_id(self, account_id: str):
        return {
            "ok": True,
            "account_id": account_id,
            "action": "refresh_exchange_live_template",
            "reason": "Refresh exchange succeeded.",
            "recognized_fields": {
                "access_token": "formal-cli-access-001",
                "refresh_token": "formal-cli-refresh-001",
                "expires_in": 3600,
                "token_type": "Bearer",
                "scope": "workspace.read workspace.write",
            },
        }

    AccountPool.refresh_account_by_id = fake_refresh_account_by_id

    try:
        accounts = store.get_accounts()
        target = next(item for item in accounts if item["id"] == ACCOUNT_ID)
        target.setdefault("status", {})
        target.setdefault("oauth", {})
        target["status"].update(
            {
                "oauth_expired": True,
                "needs_refresh": True,
                "needs_reauth": True,
                "reauthorize_required": True,
                "last_refresh_error": "expired before action",
            }
        )
        target["oauth"].update(
            {
                "access_token": "old-formal-cli-access",
                "refresh_token": "old-formal-cli-refresh",
                "expired": True,
                "needs_refresh": True,
                "expires_at": 1,
            }
        )
        store.set_accounts(accounts)

        with TestClient(app) as client:
            headers = build_admin_session_headers(client)
            refresh_response = client.post(
                f"/v1/admin/accounts/{ACCOUNT_ID}/refresh",
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
            "refresh_action": refresh_response.json(),
            "account_status": account["status"],
            "account_oauth": account["oauth"],
            "recent_actions": snapshot_response.json().get("recent_actions", []),
        }
        print(json.dumps(output, ensure_ascii=False))
    finally:
        AccountPool.refresh_account_by_id = original_method
        restore_config = copy.deepcopy(original_config)
        restore_config.pop("accounts", None)
        store.save_config(restore_config)
        store.set_accounts(original_accounts)


if __name__ == "__main__":
    main()

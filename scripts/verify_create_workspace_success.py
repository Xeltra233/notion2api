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
    original_method = AccountPool.create_workspace_by_id

    def fake_create_workspace_by_id(self, account_id: str):
        return {
            "ok": True,
            "account_id": account_id,
            "space_id": "created-cli-space-901",
            "action": "create_workspace_live_template",
            "reason": "Workspace was created from verified template.",
            "recognized_fields": {
                "workspace_id": "created-cli-space-901",
                "workspace_ids": ["created-cli-space-901", "created-cli-space-902"],
                "workspace_name": "CLI Created Workspace",
                "workspace_slug": "cli-created-workspace",
                "subscription_tier": "business",
                "space_view_id": "created-cli-view-901",
                "transaction_id": "created-cli-txn-901",
            },
            "workspaces": [
                {
                    "id": "legacy-cli-space-before-create",
                    "name": "Legacy CLI Before Create",
                    "space_view_id": "legacy-cli-view-before-create",
                    "subscription_tier": "free",
                },
                {
                    "id": "created-cli-space-901",
                    "name": "CLI Created Workspace",
                    "slug": "cli-created-workspace",
                    "space_view_id": "created-cli-view-901",
                    "subscription_tier": "business",
                },
            ],
        }

    AccountPool.create_workspace_by_id = fake_create_workspace_by_id

    try:
        accounts = store.get_accounts()
        target = next(item for item in accounts if item["id"] == ACCOUNT_ID)
        target["space_id"] = "legacy-cli-space-before-create"
        target["space_view_id"] = "legacy-cli-view-before-create"
        target.setdefault("workspace", {})
        target.setdefault("status", {})
        target["workspace"].update(
            {
                "state": "missing",
                "workspace_count": 1,
                "workspaces": [
                    {
                        "id": "legacy-cli-space-before-create",
                        "name": "Legacy CLI Before Create",
                        "space_view_id": "legacy-cli-view-before-create",
                        "subscription_tier": "free",
                    }
                ],
            }
        )
        target["status"].update(
            {
                "workspace_state": "workspace_creation_pending",
                "last_workspace_error": "pending create",
            }
        )
        store.set_accounts(accounts)

        with TestClient(app) as client:
            headers = build_admin_session_headers(client)
            create_response = client.post(
                f"/v1/admin/accounts/{ACCOUNT_ID}/workspaces/create",
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
            "create_workspace": create_response.json(),
            "account_space_id": account["space_id"],
            "account_space_view_id": account["space_view_id"],
            "account_status": account["status"],
            "account_workspace": account["workspace"],
            "recent_actions": snapshot_response.json().get("recent_actions", []),
        }
        print(json.dumps(output, ensure_ascii=False))
    finally:
        AccountPool.create_workspace_by_id = original_method
        restore_config = copy.deepcopy(original_config)
        restore_config.pop("accounts", None)
        store.save_config(restore_config)
        store.set_accounts(original_accounts)


if __name__ == "__main__":
    main()

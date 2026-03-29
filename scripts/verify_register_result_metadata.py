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
from app.register.notion_register import NotionRegisterResult  # noqa: E402


class FakeMailClient:
    email = "api-register-check@example.com"

    def register_account(self, domain=None):
        return True


class FakeRegisterService:
    def __init__(self, *args, **kwargs):
        pass

    def register(self, mail_client, use_api=True):
        if use_api:
            return NotionRegisterResult(
                success=True,
                email=mail_client.email,
                token_v2="token-api-1234567890",
                user_id="user-api-001",
                space_id="space-api-001",
                space_view_id="space-view-api-001",
                register_method="api",
                attempted_api=True,
                used_browser_fallback=False,
                workspace_count=2,
            )
        return NotionRegisterResult(
            success=True,
            email=mail_client.email,
            token_v2="token-browser-1234567890",
            user_id="user-browser-001",
            space_id="",
            space_view_id="",
            register_method="browser",
            attempted_api=False,
            used_browser_fallback=False,
            workspace_count=0,
        )

    def finalize_account_record(self, account):
        finalized = dict(account)
        finalized["workspace"] = {
            "workspace_count": 2,
            "workspaces": [
                {"id": "space-api-001", "name": "Primary"},
                {"id": "space-api-002", "name": "Secondary"},
            ],
            "state": "ready",
        }
        finalized["status"] = {
            "workspace_hydration_pending": False,
            "workspace_state": "ready",
        }
        return finalized


original_create_temp_mail_client = register_api.create_temp_mail_client
original_register_service = register_api.NotionRegisterService
original_save_account = register_api._save_account

saved_accounts = []


def fake_save_account(account):
    saved_accounts.append(dict(account))


register_api.create_temp_mail_client = lambda **kwargs: FakeMailClient()
register_api.NotionRegisterService = FakeRegisterService
register_api._save_account = fake_save_account

try:
    api_result = register_api._register_one(
        task_id="task-api-001",
        mail_provider="freemail",
        domain=None,
        mail_base_url=None,
        mail_api_key=None,
        use_api=True,
        headless=True,
        proxy=None,
    )

    browser_result = register_api._register_one(
        task_id="task-browser-001",
        mail_provider="freemail",
        domain=None,
        mail_base_url=None,
        mail_api_key=None,
        use_api=False,
        headless=True,
        proxy=None,
    )

    print(
        json.dumps(
            {
                "api_result": api_result,
                "browser_result": browser_result,
                "saved_account_count": len(saved_accounts),
                "saved_account_workspace_state": ((saved_accounts[0].get("workspace") or {}).get("state") if saved_accounts else ""),
            },
            ensure_ascii=False,
        )
    )
finally:
    register_api.create_temp_mail_client = original_create_temp_mail_client
    register_api.NotionRegisterService = original_register_service
    register_api._save_account = original_save_account

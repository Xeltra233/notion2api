import json
import os
import sys
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("API_KEY", "test-server-key")
os.environ.setdefault("ADMIN_PASSWORD", "test-admin-password")

from app.server import app  # noqa: E402
from scripts.admin_session_test_utils import build_admin_session_headers  # noqa: E402


with TestClient(app) as client:
    headers = build_admin_session_headers(client)

    auto_status = client.get("/v1/admin/register/auto-status", headers=headers)
    refresh_status = client.get("/v1/admin/oauth/refresh-status", headers=headers)
    workspace_status = client.get("/v1/admin/workspaces/create-status", headers=headers)
    workspace_rows = client.get("/v1/admin/accounts/workspaces/status", headers=headers)
    refresh_diagnostics = client.get("/v1/admin/oauth/refresh-diagnostics", headers=headers)
    workspace_diagnostics = client.get("/v1/admin/workspaces/diagnostics", headers=headers)

    auto_status.raise_for_status()
    refresh_status.raise_for_status()
    workspace_status.raise_for_status()
    workspace_rows.raise_for_status()
    refresh_diagnostics.raise_for_status()
    workspace_diagnostics.raise_for_status()

    print(
        json.dumps(
            {
                "auto_status_mode": auto_status.json().get("response_mode"),
                "auto_status_contains_secrets": auto_status.json().get("contains_secrets"),
                "refresh_status_mode": refresh_status.json().get("response_mode"),
                "refresh_status_contains_secrets": refresh_status.json().get("contains_secrets"),
                "workspace_status_mode": workspace_status.json().get("response_mode"),
                "workspace_status_contains_secrets": workspace_status.json().get("contains_secrets"),
                "workspace_rows_mode": workspace_rows.json().get("response_mode"),
                "workspace_rows_contains_secrets": workspace_rows.json().get("contains_secrets"),
                "refresh_diagnostics_mode": refresh_diagnostics.json().get("response_mode"),
                "refresh_diagnostics_contains_secrets": refresh_diagnostics.json().get("contains_secrets"),
                "workspace_diagnostics_mode": workspace_diagnostics.json().get("response_mode"),
                "workspace_diagnostics_contains_secrets": workspace_diagnostics.json().get("contains_secrets"),
            },
            ensure_ascii=False,
        )
    )

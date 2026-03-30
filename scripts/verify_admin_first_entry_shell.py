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


REQUIRED_STRINGS = {
    "has_admin_first_kicker": "后台优先",
    "has_admin_login_module": 'data-module="access"',
    "has_overview_module": 'data-module="overview"',
    "has_usage_module": 'data-module="usage"',
    "has_accounts_module": 'data-module="accounts"',
    "has_runtime_module": 'data-module="runtime"',
    "has_diagnostics_module": 'data-module="diagnostics"',
    "has_chat_module": 'data-module="chat"',
    "has_admin_workspace_title": "后台工作区",
    "has_login_copy": "先进入后台",
    "has_module_shell_copy": "聊天只是其中一个模块，不再抢首页位置",
    "has_admin_header_title": "管理后台",
    "has_new_chat_label": "新建聊天",
}


def main() -> None:
    with TestClient(app) as client:
        response = client.get("/")
        response.raise_for_status()
        html = response.text

    result = {
        "status": response.status_code,
        "content_type": response.headers.get("content-type", ""),
        **{key: needle in html for key, needle in REQUIRED_STRINGS.items()},
    }
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()

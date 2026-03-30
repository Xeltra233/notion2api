import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("API_KEY", "test-server-key")
os.environ.setdefault("ADMIN_PASSWORD", "test-admin-password")

from app.config import get_config_store  # noqa: E402


FRONTEND_APP = ROOT / "frontend" / "js" / "core" / "app.js"
FRONTEND_STATE = ROOT / "frontend" / "js" / "core" / "state.js"
INDEX_HTML = ROOT / "frontend" / "index.html"


def main() -> None:
    app_js = FRONTEND_APP.read_text(encoding="utf-8")
    state_js = FRONTEND_STATE.read_text(encoding="utf-8")
    index_html = INDEX_HTML.read_text(encoding="utf-8")
    runtime_config = get_config_store().get_config()

    result = {
        "default_module_signed_out_is_access": "return window.NotionAI.Core.State.get('adminSessionToken') ? 'overview' : 'access';" in app_js,
        "signed_out_non_chat_requests_force_access_module": "if (!hasAdminSession && requested !== 'chat') {\n            return 'access';\n        }" in app_js,
        "signed_out_chat_requests_are_handled_separately": "if (requested === 'chat' && !canAccessChatModule) {" in app_js,
        "init_uses_default_module_resolution": "const initialModule = window.NotionAI.Core.State.get('activeModule') || window.NotionAI.Core.App.getDefaultModule();" in app_js,
        "state_persists_active_module": "claude_active_module" in state_js,
        "access_module_button_present": 'data-module="access"' in index_html,
        "chat_module_button_present": 'data-module="chat"' in index_html,
        "runtime_default_chat_enabled_present": "chat_enabled" in runtime_config,
    }
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()

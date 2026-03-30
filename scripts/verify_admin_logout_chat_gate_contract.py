import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SETTINGS_JS = ROOT / "frontend" / "js" / "api" / "settings.js"


def main() -> None:
    source = SETTINGS_JS.read_text(encoding="utf-8")
    result = {
        "signout_is_async": "async signOutAdminSession()" in source,
        "signout_refreshes_chat_access_state": "await this.refreshChatAccessState(true);" in source,
        "signout_resyncs_shell": "window.NotionAI.Core.App.syncShellFromState();" in source,
        "signout_still_logs_out_admin": "window.NotionAI.API.Admin.logout();" in source,
    }
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()

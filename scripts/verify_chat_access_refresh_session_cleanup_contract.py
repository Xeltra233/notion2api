import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SETTINGS_JS = ROOT / "frontend" / "js" / "api" / "settings.js"


def main() -> None:
    source = SETTINGS_JS.read_text(encoding="utf-8")
    result = {
        "refresh_reads_chat_access": "const data = await window.NotionAI.API.Admin.getChatAccess();" in source,
        "refresh_updates_chat_enabled_state": "window.NotionAI.Core.State.set('chatEnabled', chatEnabled);" in source,
        "refresh_updates_chat_password_state": "window.NotionAI.Core.State.set('chatPasswordEnabled', passwordEnabled);" in source,
        "refresh_clears_stale_chat_session_when_open_or_disabled": "if (!chatEnabled || !passwordEnabled) {" in source,
        "refresh_clears_chat_session": "window.NotionAI.Core.State.clearChatSession();" in source,
        "refresh_resyncs_shell": "window.NotionAI.Core.App.syncShellFromState();" in source,
    }
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()

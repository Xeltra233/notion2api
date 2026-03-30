import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SETTINGS_JS = ROOT / "frontend" / "js" / "api" / "settings.js"


def main() -> None:
    source = SETTINGS_JS.read_text(encoding="utf-8")
    result = {
        "runtime_save_updates_chat_enabled_state": "window.NotionAI.Core.State.set('chatEnabled', Boolean(payload.chat_enabled));" in source,
        "runtime_save_updates_chat_password_state": "window.NotionAI.Core.State.set('chatPasswordEnabled', Boolean(payload.chat_password_enabled));" in source,
        "runtime_save_clears_chat_session_when_chat_disabled": "if (!payload.chat_enabled || !payload.chat_password_enabled) {" in source,
        "runtime_save_clears_chat_session": "window.NotionAI.Core.State.clearChatSession();" in source,
        "runtime_save_refreshes_chat_access": "await this.refreshChatAccessState(true);" in source,
    }
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()

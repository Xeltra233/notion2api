import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_JS = ROOT / "frontend" / "js" / "core" / "app.js"


def main() -> None:
    source = APP_JS.read_text(encoding="utf-8")
    result = {
        "resolve_allows_signed_out_chat_requests": "if (!hasAdminSession && requested !== 'chat') {" in source,
        "resolve_uses_chat_password_state": "const chatPasswordEnabled = Boolean(window.NotionAI.Core.State.get('chatPasswordEnabled'));" in source,
        "resolve_uses_chat_session_state": "const hasChatSession = Boolean(window.NotionAI.Core.State.get('chatSessionToken'));" in source,
        "resolve_computes_chat_module_access": "const canAccessChatModule = chatEnabled && (hasAdminSession || !chatPasswordEnabled || hasChatSession);" in source,
        "nav_keeps_chat_visible_without_admin": "const hideForSignedOut = !hasAdminSession && moduleName !== 'access' && moduleName !== 'chat';" in source,
        "chat_view_uses_chat_module_access": "chatView.classList.toggle('hidden', !canAccessChatModule || resolvedModule !== 'chat');" in source,
        "chat_sidebar_uses_chat_module_access": "chatSidebarPanel.classList.toggle('hidden', !canAccessChatModule || resolvedModule !== 'chat');" in source,
    }
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()

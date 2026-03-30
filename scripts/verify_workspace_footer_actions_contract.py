import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_JS = ROOT / "frontend" / "js" / "core" / "app.js"
SETTINGS_JS = ROOT / "frontend" / "js" / "api" / "settings.js"
INDEX_HTML = ROOT / "frontend" / "index.html"


def main() -> None:
    app_js = APP_JS.read_text(encoding="utf-8")
    settings_js = SETTINGS_JS.read_text(encoding="utf-8")
    index_html = INDEX_HTML.read_text(encoding="utf-8")
    result = {
        "footer_comment_renamed_from_settings": "// Workspace footer actions" in app_js,
        "cancel_button_uses_settings_close": "window.NotionAI.API.Settings.close();" in app_js,
        "settings_close_routes_to_default_module": "window.NotionAI.Core.App.setActiveModule(window.NotionAI.Core.App.getDefaultModule());" in settings_js,
        "footer_reset_label_present": "重置视图" in index_html,
        "footer_save_label_present": "保存更改" in index_html,
    }
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()

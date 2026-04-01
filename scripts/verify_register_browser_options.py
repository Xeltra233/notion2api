import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.register.notion_register import NotionRegisterService  # noqa: E402


service = NotionRegisterService(headless=True)
page = None

try:
    captured = {}

    class DummyOptions:
        def __init__(self):
            self.browser_path = ""
            self.user_data_path = ""
            self.arguments = []

        def set_browser_path(self, path):
            self.browser_path = path

        def set_user_data_path(self, path):
            self.user_data_path = path

        def set_argument(self, arg):
            self.arguments.append(arg)

        def set_user_agent(self, value):
            captured["user_agent"] = value

        def auto_port(self):
            captured["auto_port"] = True

    class DummyPage:
        def __init__(self, options):
            captured["browser_path"] = options.browser_path
            captured["user_data_path"] = options.user_data_path
            captured["arguments"] = list(options.arguments)
            self.set = type(
                "Setter", (), {"timeouts": lambda *_args, **_kwargs: None}
            )()

    import app.register.notion_register as nr

    original_options = nr.ChromiumOptions
    original_page = nr.ChromiumPage
    original_platform = nr.platform.system
    nr.ChromiumOptions = DummyOptions
    nr.ChromiumPage = DummyPage
    nr.platform.system = lambda: "Linux"

    page = service._create_browser_page()

    output = {
        "browser_path": captured.get("browser_path", ""),
        "user_data_path": captured.get("user_data_path", ""),
        "arguments": captured.get("arguments", []),
        "auto_port": captured.get("auto_port", False),
    }

    assert output["user_data_path"], output
    assert os.path.basename(output["user_data_path"]).startswith("session_"), output
    assert any(arg == "--no-sandbox" for arg in output["arguments"]), output
    assert any(arg == "--disable-dev-shm-usage" for arg in output["arguments"]), output
    assert any(arg == "--headless=new" for arg in output["arguments"]), output
    assert output["auto_port"] is True, output

    print(json.dumps(output, ensure_ascii=True))
finally:
    import app.register.notion_register as nr

    if "original_options" in locals():
        nr.ChromiumOptions = original_options
    if "original_page" in locals():
        nr.ChromiumPage = original_page
    if "original_platform" in locals():
        nr.platform.system = original_platform

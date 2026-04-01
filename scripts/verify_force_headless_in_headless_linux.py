import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import app.register.notion_register as nr  # noqa: E402


original_platform = nr.platform.system
original_display = os.environ.pop("DISPLAY", None)
original_wayland = os.environ.pop("WAYLAND_DISPLAY", None)

try:
    nr.platform.system = lambda: "Linux"
    output = {
        "should_force_headless": nr.should_force_headless(),
        "service_headless": nr.NotionRegisterService(headless=False).headless,
    }
    assert output["should_force_headless"] is True, output
    assert output["service_headless"] is True, output
    print(json.dumps(output, ensure_ascii=True))
finally:
    nr.platform.system = original_platform
    if original_display is not None:
        os.environ["DISPLAY"] = original_display
    if original_wayland is not None:
        os.environ["WAYLAND_DISPLAY"] = original_wayland

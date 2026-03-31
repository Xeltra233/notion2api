import copy
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("ADMIN_PASSWORD", "test-admin-password")

from app.api import admin as admin_api  # noqa: E402
from app.config import get_config_store  # noqa: E402


store = get_config_store()
original_config = copy.deepcopy(store.get_config())

try:
    warp_config = copy.deepcopy(original_config)
    warp_config.update(
        {
            "upstream_proxy_mode": "warp",
            "upstream_warp_enabled": True,
            "upstream_warp_proxy": "socks5://127.0.0.1:9090",
            "upstream_proxy": "http://127.0.0.1:8080",
            "upstream_http_proxy": "http://127.0.0.1:8081",
            "upstream_https_proxy": "http://127.0.0.1:8082",
        }
    )
    store.save_config(warp_config)
    warp_service = admin_api._build_email_login_register_service()

    socks5_config = copy.deepcopy(warp_config)
    socks5_config.update(
        {
            "upstream_proxy_mode": "socks5",
            "upstream_socks5_proxy": "socks5://127.0.0.1:10080",
            "upstream_warp_enabled": False,
        }
    )
    store.save_config(socks5_config)
    socks5_service = admin_api._build_email_login_register_service()

    http_config = copy.deepcopy(socks5_config)
    http_config.update(
        {
            "upstream_proxy_mode": "http",
            "upstream_proxy": "http://127.0.0.1:18080",
            "upstream_http_proxy": "http://127.0.0.1:18081",
            "upstream_https_proxy": "http://127.0.0.1:18082",
        }
    )
    store.save_config(http_config)
    http_service = admin_api._build_email_login_register_service()

    output = {
        "warp_proxy": warp_service.proxy,
        "socks5_proxy": socks5_service.proxy,
        "http_proxy": http_service.proxy,
    }

    assert warp_service.proxy == "socks5://127.0.0.1:9090", output
    assert socks5_service.proxy == "socks5://127.0.0.1:10080", output
    assert http_service.proxy == "http://127.0.0.1:18080", output

    print(json.dumps(output, ensure_ascii=False))
finally:
    store.save_config(original_config)

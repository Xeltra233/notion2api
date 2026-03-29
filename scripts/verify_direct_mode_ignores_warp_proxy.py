import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import get_config_store  # noqa: E402
from app.register.mail_client import build_runtime_proxy_dict, is_runtime_proxy_active  # noqa: E402

store = get_config_store()
original = store.get_config()
try:
    store.update_config(
        {
            "upstream_proxy_mode": "direct",
            "upstream_warp_enabled": True,
            "upstream_warp_proxy": "socks5://127.0.0.1:40000",
            "upstream_proxy": "",
            "upstream_http_proxy": "",
            "upstream_https_proxy": "",
            "upstream_socks5_proxy": "",
        }
    )
    print(
        json.dumps(
            {
                "proxy_dict": build_runtime_proxy_dict(),
                "proxy_active": is_runtime_proxy_active(),
            },
            ensure_ascii=False,
        )
    )
finally:
    store.save_config(original)

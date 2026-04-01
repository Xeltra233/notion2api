import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.register.mail_client import FreemailClient  # noqa: E402


client = FreemailClient(
    base_url="https://mail.speacecc.xyz",
    api_key="sample-admin-token",
    domain="zhatianbang66fasdgewfas.dpdns.org",
)

output = {
    "auth_params": client._auth_params(),
    "headers": client._headers(),
}

assert output["auth_params"] == {"admin_token": "sample-admin-token"}, output
assert output["headers"].get("Authorization") == "Bearer sample-admin-token", output

print(json.dumps(output, ensure_ascii=True))

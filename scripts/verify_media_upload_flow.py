import copy
import json
import os
import sys
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("API_KEY", "test-server-key")
os.environ.setdefault("ADMIN_PASSWORD", "test-admin-password")

from app.config import get_config_store, update_admin_credentials  # noqa: E402
from app.server import app  # noqa: E402
from scripts.admin_session_test_utils import build_admin_session_headers  # noqa: E402

PNG_DATA_URL = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9WlH0XwAAAAASUVORK5CYII="
)


def main() -> None:
    store = get_config_store()
    original_config = copy.deepcopy(store.get_config())
    media_dir = ROOT / "data" / "verify-media-upload"
    created_files: list[Path] = []

    try:
        config = copy.deepcopy(original_config)
        config["chat_enabled"] = True
        config["chat_auth"] = {
            "password_hash": "",
            "password_salt": "",
            "enabled": False,
            "updated_at": 0,
        }
        config["media_storage_path"] = str(media_dir)
        config["media_public_base_url"] = ""
        config["refresh_execution_mode"] = "manual"
        config["refresh_request_url"] = ""
        config["workspace_execution_mode"] = "manual"
        config["workspace_request_url"] = ""
        store.save_config(config)
        update_admin_credentials(username="admin", password="test-admin-password")

        with TestClient(app) as client:
            auth_headers = {"Authorization": "Bearer test-server-key"}
            admin_headers = build_admin_session_headers(client)

            upload_response = client.post(
                "/v1/media/upload",
                headers=auth_headers,
                json={"data_url": PNG_DATA_URL, "file_name": "tiny pixel.png"},
            )
            upload_response.raise_for_status()
            upload_payload = upload_response.json()
            created_files.append(media_dir / upload_payload["media_id"])

            fetch_response = client.get(upload_payload["url"])

            custom_base = "https://cdn.example.com/media-assets"
            update_response = client.put(
                "/v1/admin/config/settings",
                headers=admin_headers,
                json={
                    "app_mode": config.get("app_mode", "standard"),
                    "allowed_origins": config.get("allowed_origins", []),
                    "upstream_proxy": config.get("upstream_proxy", ""),
                    "upstream_http_proxy": config.get("upstream_http_proxy", ""),
                    "upstream_https_proxy": config.get("upstream_https_proxy", ""),
                    "upstream_socks5_proxy": config.get("upstream_socks5_proxy", ""),
                    "upstream_proxy_mode": config.get("upstream_proxy_mode", "direct"),
                    "upstream_warp_enabled": config.get("upstream_warp_enabled", False),
                    "upstream_warp_proxy": config.get("upstream_warp_proxy", ""),
                    "auto_create_workspace": config.get("auto_create_workspace", False),
                    "auto_select_workspace": config.get("auto_select_workspace", True),
                    "workspace_create_dry_run": config.get("workspace_create_dry_run", True),
                    "workspace_creation_template_space_id": config.get("workspace_creation_template_space_id", ""),
                    "account_probe_interval_seconds": config.get("account_probe_interval_seconds", 300),
                    "refresh_execution_mode": config.get("refresh_execution_mode", "manual"),
                    "refresh_request_url": config.get("refresh_request_url", ""),
                    "refresh_client_id": config.get("refresh_client_id", ""),
                    "workspace_execution_mode": config.get("workspace_execution_mode", "manual"),
                    "workspace_request_url": config.get("workspace_request_url", ""),
                    "allow_real_probe_requests": config.get("allow_real_probe_requests", False),
                    "chat_enabled": True,
                    "chat_password_enabled": False,
                    "chat_password": "",
                    "auto_register_enabled": config.get("auto_register_enabled", False),
                    "auto_register_idle_only": config.get("auto_register_idle_only", True),
                    "auto_register_interval_seconds": config.get("auto_register_interval_seconds", 1800),
                    "auto_register_min_spacing_seconds": config.get("auto_register_min_spacing_seconds", 900),
                    "auto_register_busy_cooldown_seconds": config.get("auto_register_busy_cooldown_seconds", 1200),
                    "auto_register_batch_size": config.get("auto_register_batch_size", 1),
                    "auto_register_headless": config.get("auto_register_headless", False),
                    "auto_register_use_api": config.get("auto_register_use_api", True),
                    "auto_register_mail_provider": config.get("auto_register_mail_provider", "freemail"),
                    "auto_register_mail_base_url": config.get("auto_register_mail_base_url", ""),
                    "auto_register_domain": config.get("auto_register_domain", ""),
                    "media_public_base_url": custom_base,
                    "media_storage_path": str(media_dir),
                },
            )
            update_response.raise_for_status()

            upload_public_response = client.post(
                "/v1/media/upload",
                headers=auth_headers,
                json={"data_url": PNG_DATA_URL, "file_name": "with-custom-base.png"},
            )
            upload_public_response.raise_for_status()
            upload_public_payload = upload_public_response.json()
            created_files.append(media_dir / upload_public_payload["media_id"])

            missing_response = client.get("/v1/media/does-not-exist.png")
            traversal_response = client.get("/v1/media/../runtime_config.json")
            encoded_traversal_response = client.get("/v1/media/%2e%2e%2Fruntime_config.json")

        output = {
            "upload_status": upload_response.status_code,
            "upload_payload": upload_payload,
            "stored_file_exists": created_files[0].exists(),
            "fetch_status": fetch_response.status_code,
            "fetch_content_type": fetch_response.headers.get("content-type", ""),
            "fetch_size": len(fetch_response.content),
            "update_status": update_response.status_code,
            "public_upload_status": upload_public_response.status_code,
            "public_upload_url": upload_public_payload.get("url"),
            "public_upload_uses_custom_base": str(upload_public_payload.get("url") or "").startswith(custom_base + "/"),
            "missing_status": missing_response.status_code,
            "traversal_status": traversal_response.status_code,
            "encoded_traversal_status": encoded_traversal_response.status_code,
        }
        print(json.dumps(output, ensure_ascii=False))
    finally:
        restore_config = copy.deepcopy(original_config)
        restore_config.pop("accounts", None)
        store.save_config(restore_config)
        for path in created_files:
            try:
                if path.exists():
                    path.unlink()
            except OSError:
                pass
        try:
            if media_dir.exists() and not any(media_dir.iterdir()):
                media_dir.rmdir()
        except OSError:
            pass


if __name__ == "__main__":
    main()

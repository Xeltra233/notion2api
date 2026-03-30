import hashlib
import hmac
import ipaddress
import json
import os
import secrets
import threading
import time
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

CONFIG_PATH = DATA_DIR / "runtime_config.json"
ACCOUNTS_PATH = DATA_DIR / "accounts.json"
DEFAULT_DB_PATH = str(DATA_DIR / "conversations.db")
DEFAULT_ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")

REQUIRED_ACCOUNT_FIELDS = {"token_v2", "space_id", "user_id"}
DEFAULT_ALLOWED_ORIGINS: list[str] = []
_PASSWORD_HASH_NAME = "sha256"
_PASSWORD_HASH_ITERATIONS = 200000
_ADMIN_SESSION_TTL_SECONDS = 12 * 60 * 60
_CHAT_SESSION_TTL_SECONDS = 12 * 60 * 60
DEFAULT_ALLOWED_ORIGINS: list[str] = []


def _normalize_accounts(accounts: Any) -> list[dict[str, Any]]:
    if accounts is None:
        return []
    if not isinstance(accounts, list):
        raise ValueError("accounts must be a list")

    normalized_accounts: list[dict[str, Any]] = []
    for idx, account in enumerate(accounts):
        if not isinstance(account, dict):
            raise ValueError(f"accounts[{idx}] must be an object")

        normalized = deepcopy(account)
        missing = sorted(
            field
            for field in REQUIRED_ACCOUNT_FIELDS
            if not str(normalized.get(field, "")).strip()
        )
        if missing:
            raise ValueError(
                f"accounts[{idx}] missing required fields: {', '.join(missing)}"
            )

        normalized["id"] = str(normalized.get("id") or uuid.uuid4())
        normalized["token_v2"] = str(normalized.get("token_v2") or "").strip()
        normalized["space_id"] = str(normalized.get("space_id") or "").strip()
        normalized["user_id"] = str(normalized.get("user_id") or "").strip()
        normalized["space_view_id"] = str(normalized.get("space_view_id") or "").strip()
        normalized["user_name"] = (
            str(normalized.get("user_name") or "user").strip() or "user"
        )
        normalized["user_email"] = str(normalized.get("user_email") or "").strip()
        normalized["plan_type"] = (
            str(normalized.get("plan_type") or "unknown").strip() or "unknown"
        )
        normalized["enabled"] = bool(normalized.get("enabled", True))
        normalized["source"] = (
            str(normalized.get("source") or "manual").strip() or "manual"
        )
        normalized["notes"] = str(normalized.get("notes") or "")
        tags = normalized.get("tags")
        normalized["tags"] = (
            [str(item).strip() for item in tags if str(item).strip()]
            if isinstance(tags, list)
            else []
        )
        created_at = normalized.get("created_at")
        updated_at = normalized.get("updated_at")
        try:
            normalized["created_at"] = (
                int(created_at) if created_at is not None else int(time.time())
            )
        except (TypeError, ValueError):
            normalized["created_at"] = int(time.time())
        try:
            normalized["updated_at"] = (
                int(updated_at) if updated_at is not None else int(time.time())
            )
        except (TypeError, ValueError):
            normalized["updated_at"] = int(time.time())
        normalized["oauth"] = (
            normalized.get("oauth") if isinstance(normalized.get("oauth"), dict) else {}
        )
        normalized["workspace"] = (
            normalized.get("workspace")
            if isinstance(normalized.get("workspace"), dict)
            else {}
        )
        normalized["status"] = (
            normalized.get("status")
            if isinstance(normalized.get("status"), dict)
            else {}
        )
        normalized_accounts.append(normalized)

    return normalized_accounts


def _normalize_origins(origins: Any) -> list[str]:
    if origins is None:
        return DEFAULT_ALLOWED_ORIGINS.copy()
    if isinstance(origins, str):
        values = [item.strip() for item in origins.split(",") if item.strip()]
    elif isinstance(origins, list):
        values = [str(item).strip() for item in origins if str(item).strip()]
    else:
        return DEFAULT_ALLOWED_ORIGINS.copy()

    normalized: list[str] = []
    for item in values:
        parsed = urlparse(item)
        scheme = str(parsed.scheme or "").lower()
        hostname = str(parsed.hostname or "").strip()
        if item == "*" or not hostname or scheme not in {"http", "https"}:
            continue
        normalized.append(f"{scheme}://{parsed.netloc}")
    return normalized or DEFAULT_ALLOWED_ORIGINS.copy()


def validate_runtime_request_url(value: Any, field_name: str) -> str:
    url = str(value or "").strip()
    if not url:
        return ""
    parsed = urlparse(url)
    scheme = str(parsed.scheme or "").lower()
    host = str(parsed.hostname or "").strip().lower()
    if scheme not in {"http", "https"}:
        raise ValueError(f"{field_name} must use http or https")
    if not host:
        raise ValueError(f"{field_name} must include a hostname")
    if host in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError(f"{field_name} cannot target localhost")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return url
    if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
        raise ValueError(f"{field_name} cannot target private or reserved IP ranges")
    return url


def _hash_password(password: str, salt: str) -> str:
    derived = hashlib.pbkdf2_hmac(
        _PASSWORD_HASH_NAME,
        str(password or "").encode("utf-8"),
        str(salt or "").encode("utf-8"),
        _PASSWORD_HASH_ITERATIONS,
    )
    return derived.hex()


def _hash_admin_password(password: str, salt: str) -> str:
    return _hash_password(password, salt)


def _build_admin_auth_payload(
    *,
    username: str,
    password: str,
    must_change_password: bool,
    initialized_from_default: bool,
    updated_at: int | None = None,
) -> dict[str, Any]:
    normalized_username = str(username or DEFAULT_ADMIN_USERNAME).strip() or DEFAULT_ADMIN_USERNAME
    salt = secrets.token_hex(16)
    now_ts = int(updated_at or time.time())
    return {
        "username": normalized_username,
        "password_salt": salt,
        "password_hash": _hash_admin_password(password, salt),
        "must_change_password": bool(must_change_password),
        "initialized_from_default": bool(initialized_from_default),
        "updated_at": now_ts,
    }


def _normalize_admin_auth(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        username = str(raw.get("username") or DEFAULT_ADMIN_USERNAME).strip() or DEFAULT_ADMIN_USERNAME
        password_hash = str(raw.get("password_hash") or "").strip()
        password_salt = str(raw.get("password_salt") or "").strip()
        if username and password_hash and password_salt:
            updated_at = raw.get("updated_at")
            try:
                normalized_updated_at = int(updated_at) if updated_at is not None else int(time.time())
            except (TypeError, ValueError):
                normalized_updated_at = int(time.time())
            return {
                "username": username,
                "password_hash": password_hash,
                "password_salt": password_salt,
                "must_change_password": bool(raw.get("must_change_password", False)),
                "initialized_from_default": bool(raw.get("initialized_from_default", False)),
                "updated_at": normalized_updated_at,
            }
    default_password = str(ADMIN_PASSWORD or "").strip()
    if default_password:
        return _build_admin_auth_payload(
            username=DEFAULT_ADMIN_USERNAME,
            password=default_password,
            must_change_password=False,
            initialized_from_default=True,
        )
    return {
        "username": str(DEFAULT_ADMIN_USERNAME or "admin").strip() or "admin",
        "password_hash": "",
        "password_salt": "",
        "must_change_password": False,
        "initialized_from_default": True,
        "updated_at": int(time.time()),
    }


def _admin_password_matches(admin_auth: dict[str, Any], password: str) -> bool:
    password_hash = str(admin_auth.get("password_hash") or "").strip()
    password_salt = str(admin_auth.get("password_salt") or "").strip()
    if not password_hash or not password_salt:
        return False
    candidate = _hash_admin_password(password, password_salt)
    return hmac.compare_digest(password_hash, candidate)


def _build_chat_auth_payload(
    *,
    password: str,
    enabled: bool,
    updated_at: int | None = None,
) -> dict[str, Any]:
    normalized_password = str(password or "")
    now_ts = int(updated_at or time.time())
    if not normalized_password:
        return {
            "password_salt": "",
            "password_hash": "",
            "enabled": False,
            "updated_at": now_ts,
        }
    salt = secrets.token_hex(16)
    return {
        "password_salt": salt,
        "password_hash": _hash_password(normalized_password, salt),
        "enabled": bool(enabled),
        "updated_at": now_ts,
    }


def _normalize_chat_auth(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        password_hash = str(raw.get("password_hash") or "").strip()
        password_salt = str(raw.get("password_salt") or "").strip()
        updated_at = raw.get("updated_at")
        try:
            normalized_updated_at = int(updated_at) if updated_at is not None else int(time.time())
        except (TypeError, ValueError):
            normalized_updated_at = int(time.time())
        if password_hash and password_salt:
            return {
                "password_hash": password_hash,
                "password_salt": password_salt,
                "enabled": bool(raw.get("enabled", False)),
                "updated_at": normalized_updated_at,
            }
    return {
        "password_hash": "",
        "password_salt": "",
        "enabled": False,
        "updated_at": int(time.time()),
    }


def _normalize_action_history(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    normalized_logs: list[dict[str, Any]] = []
    for item in raw[-100:]:
        if not isinstance(item, dict):
            continue
        normalized_item = deepcopy(item)
        action = str(normalized_item.get("action") or "").strip()
        payload = (
            normalized_item.get("payload")
            if isinstance(normalized_item.get("payload"), dict)
            else {}
        )
        summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else None
        if summary is not None:
            if not str(summary.get("action") or "").strip() and action:
                summary["action"] = action
            if not str(summary.get("account_id") or "").strip() and payload.get("account_id"):
                summary["account_id"] = payload.get("account_id")
            if not str(summary.get("user_id") or "").strip() and payload.get("user_id"):
                summary["user_id"] = payload.get("user_id")
            if not str(summary.get("user_email") or "").strip() and payload.get("user_email"):
                summary["user_email"] = payload.get("user_email")
        normalized_logs.append(normalized_item)
    return normalized_logs


def _chat_password_matches(chat_auth: dict[str, Any], password: str) -> bool:
    password_hash = str(chat_auth.get("password_hash") or "").strip()
    password_salt = str(chat_auth.get("password_salt") or "").strip()
    if not password_hash or not password_salt:
        return False
    candidate = _hash_password(password, password_salt)
    return hmac.compare_digest(password_hash, candidate)


def _default_config() -> dict[str, Any]:
    return {
        "app_mode": "standard",
        "api_key": "",
        "allowed_origins": DEFAULT_ALLOWED_ORIGINS.copy(),
        "db_path": DEFAULT_DB_PATH,
        "admin_auth": _normalize_admin_auth(None),
        "chat_auth": _normalize_chat_auth(None),
        "siliconflow_api_key": "",
        "upstream_proxy": "",
        "upstream_http_proxy": "",
        "upstream_https_proxy": "",
        "upstream_socks5_proxy": "",
        "upstream_proxy_mode": "direct",
        "upstream_warp_enabled": False,
        "upstream_warp_proxy": "",
        "auto_create_workspace": False,
        "auto_select_workspace": True,
        "workspace_create_dry_run": True,
        "workspace_creation_template_space_id": "",
        "account_probe_interval_seconds": 300,
        "operation_logs": [],
        "probe_logs": [],
        "action_history": [],
        "refresh_execution_mode": "manual",
        "refresh_request_url": "",
        "refresh_client_id": "",
        "refresh_client_secret": "",
        "workspace_execution_mode": "manual",
        "workspace_request_url": "",
        "allow_real_probe_requests": False,
        "chat_enabled": False,
        "media_public_base_url": "",
        "media_storage_path": str(DATA_DIR / "media"),
        "auto_register_enabled": False,
        "auto_register_idle_only": True,
        "auto_register_interval_seconds": 1800,
        "auto_register_min_spacing_seconds": 900,
        "auto_register_busy_cooldown_seconds": 1200,
        "auto_register_batch_size": 1,
        "auto_register_headless": False,
        "auto_register_use_api": True,
        "auto_register_mail_provider": "freemail",
        "auto_register_mail_base_url": "",
        "auto_register_mail_api_key": "",
        "auto_register_domain": "",
    }


class RuntimeConfigStore:
    def __init__(self, config_path: Path, accounts_path: Path):
        self.path = config_path
        self.accounts_path = accounts_path
        self._lock = threading.RLock()
        self._config = self._load_or_create_config()
        self._ensure_accounts_file()
        self._migrate_embedded_accounts_if_needed()

    def _normalize_config(self, raw: Any) -> dict[str, Any]:
        if not isinstance(raw, dict):
            raise ValueError("Runtime config must be an object")

        config = _default_config()
        config["app_mode"] = (
            str(raw.get("app_mode") or config["app_mode"]).lower().strip() or "standard"
        )
        if config["app_mode"] not in {"lite", "standard", "heavy"}:
            config["app_mode"] = "standard"

        config["api_key"] = str(raw.get("api_key") or "")
        config["allowed_origins"] = _normalize_origins(raw.get("allowed_origins"))
        config["db_path"] = (
            str(raw.get("db_path") or DEFAULT_DB_PATH).strip() or DEFAULT_DB_PATH
        )
        config["admin_auth"] = _normalize_admin_auth(raw.get("admin_auth"))
        config["chat_auth"] = _normalize_chat_auth(raw.get("chat_auth"))
        config["siliconflow_api_key"] = str(raw.get("siliconflow_api_key") or "")
        config["upstream_proxy"] = str(raw.get("upstream_proxy") or "")
        config["upstream_http_proxy"] = str(raw.get("upstream_http_proxy") or "")
        config["upstream_https_proxy"] = str(raw.get("upstream_https_proxy") or "")
        config["upstream_socks5_proxy"] = str(raw.get("upstream_socks5_proxy") or "")
        proxy_mode = str(raw.get("upstream_proxy_mode") or "direct").strip().lower()
        config["upstream_proxy_mode"] = (
            proxy_mode
            if proxy_mode in {"direct", "http", "https", "socks5", "warp", "mixed"}
            else "direct"
        )
        config["upstream_warp_enabled"] = bool(raw.get("upstream_warp_enabled", False))
        config["upstream_warp_proxy"] = str(raw.get("upstream_warp_proxy") or "")
        config["auto_create_workspace"] = bool(raw.get("auto_create_workspace", False))
        config["auto_select_workspace"] = bool(raw.get("auto_select_workspace", True))
        config["workspace_create_dry_run"] = bool(
            raw.get("workspace_create_dry_run", True)
        )
        config["workspace_creation_template_space_id"] = str(
            raw.get("workspace_creation_template_space_id") or ""
        )
        interval = raw.get("account_probe_interval_seconds", 300)
        try:
            config["account_probe_interval_seconds"] = max(30, int(interval))
        except (TypeError, ValueError):
            config["account_probe_interval_seconds"] = 300
        operation_logs = raw.get("operation_logs")
        if isinstance(operation_logs, list):
            config["operation_logs"] = operation_logs[-50:]
        else:
            config["operation_logs"] = []
        probe_logs = raw.get("probe_logs")
        if isinstance(probe_logs, list):
            config["probe_logs"] = probe_logs[-100:]
        else:
            config["probe_logs"] = []
        config["action_history"] = _normalize_action_history(raw.get("action_history"))
        refresh_execution_mode = (
            str(raw.get("refresh_execution_mode") or "manual").strip().lower()
        )
        config["refresh_execution_mode"] = (
            refresh_execution_mode
            if refresh_execution_mode in {"manual", "dry_run", "live_template"}
            else "manual"
        )
        config["refresh_request_url"] = str(raw.get("refresh_request_url") or "")
        config["refresh_client_id"] = str(raw.get("refresh_client_id") or "")
        config["refresh_client_secret"] = str(raw.get("refresh_client_secret") or "")
        workspace_execution_mode = (
            str(raw.get("workspace_execution_mode") or "manual").strip().lower()
        )
        config["workspace_execution_mode"] = (
            workspace_execution_mode
            if workspace_execution_mode in {"manual", "dry_run", "live_template"}
            else "manual"
        )
        config["workspace_request_url"] = str(raw.get("workspace_request_url") or "")
        config["allow_real_probe_requests"] = bool(
            raw.get("allow_real_probe_requests", False)
        )
        config["media_public_base_url"] = str(raw.get("media_public_base_url") or "").strip()
        config["media_storage_path"] = (
            str(raw.get("media_storage_path") or str(DATA_DIR / "media")).strip()
            or str(DATA_DIR / "media")
        )
        config["auto_register_enabled"] = bool(raw.get("auto_register_enabled", False))
        config["auto_register_idle_only"] = bool(
            raw.get("auto_register_idle_only", True)
        )
        try:
            config["auto_register_interval_seconds"] = max(
                300, int(raw.get("auto_register_interval_seconds", 1800))
            )
        except (TypeError, ValueError):
            config["auto_register_interval_seconds"] = 1800
        try:
            config["auto_register_min_spacing_seconds"] = max(
                300, int(raw.get("auto_register_min_spacing_seconds", 900))
            )
        except (TypeError, ValueError):
            config["auto_register_min_spacing_seconds"] = 900
        try:
            config["auto_register_busy_cooldown_seconds"] = max(
                300, int(raw.get("auto_register_busy_cooldown_seconds", 1200))
            )
        except (TypeError, ValueError):
            config["auto_register_busy_cooldown_seconds"] = 1200
        try:
            config["auto_register_batch_size"] = max(
                1, min(3, int(raw.get("auto_register_batch_size", 1)))
            )
        except (TypeError, ValueError):
            config["auto_register_batch_size"] = 1
        config["auto_register_headless"] = bool(
            raw.get("auto_register_headless", False)
        )
        config["auto_register_use_api"] = bool(raw.get("auto_register_use_api", True))
        config["auto_register_mail_provider"] = (
            str(raw.get("auto_register_mail_provider") or "freemail").strip()
            or "freemail"
        )
        config["auto_register_mail_base_url"] = str(
            raw.get("auto_register_mail_base_url") or ""
        )
        config["auto_register_mail_api_key"] = str(
            raw.get("auto_register_mail_api_key") or ""
        )
        config["auto_register_domain"] = str(raw.get("auto_register_domain") or "")
        return config

    def _write_json(self, path: Path, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def _load_json(self, path: Path, default: Any) -> Any:
        if not path.exists():
            self._write_json(path, default)
            return deepcopy(default)
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON in {path.name}: {exc}") from exc

    def _load_or_create_config(self) -> dict[str, Any]:
        raw = self._load_json(self.path, _default_config())
        normalized = self._normalize_config(raw)
        self._write_json(self.path, normalized)
        return normalized

    def _ensure_accounts_file(self) -> None:
        raw_accounts = self._load_json(self.accounts_path, [])
        normalized = _normalize_accounts(raw_accounts)
        self._write_json(self.accounts_path, normalized)

    def _migrate_embedded_accounts_if_needed(self) -> None:
        raw = self._load_json(self.path, _default_config())
        embedded_accounts = raw.get("accounts")
        if not embedded_accounts:
            return
        normalized_accounts = _normalize_accounts(embedded_accounts)
        self._write_json(self.accounts_path, normalized_accounts)
        raw.pop("accounts", None)
        normalized_config = self._normalize_config(raw)
        self._write_json(self.path, normalized_config)
        self._config = normalized_config

    def get_config(self) -> dict[str, Any]:
        with self._lock:
            config = deepcopy(self._config)
            config["accounts"] = self.get_accounts()
            return config

    def save_config(self, config: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            incoming = deepcopy(config)
            accounts = incoming.pop("accounts", None)
            normalized = self._normalize_config(incoming)
            self._write_json(self.path, normalized)
            self._config = normalized
            if accounts is not None:
                self.set_accounts(accounts)
            return self.get_config()

    def update_config(self, updates: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            current = self.get_config()
            current.update(deepcopy(updates))
            return self.save_config(current)

    def get_accounts(self) -> list[dict[str, Any]]:
        with self._lock:
            raw_accounts = self._load_json(self.accounts_path, [])
            normalized = _normalize_accounts(raw_accounts)
            if normalized != raw_accounts:
                self._write_json(self.accounts_path, normalized)
            return deepcopy(normalized)

    def set_accounts(self, accounts: list[dict[str, Any]]) -> dict[str, Any]:
        with self._lock:
            normalized = _normalize_accounts(accounts)
            self._write_json(self.accounts_path, normalized)
            config = deepcopy(self._config)
            config["accounts"] = deepcopy(normalized)
            return config

    def upsert_account(self, account: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            accounts = self.get_accounts()
            account_id = str(account.get("id") or "").strip()
            account_user_id = str(account.get("user_id") or "").strip()
            updated = False
            for idx, existing in enumerate(accounts):
                existing_id = str(existing.get("id") or "").strip()
                existing_user_id = str(existing.get("user_id") or "").strip()
                if (account_id and existing_id == account_id) or (
                    not account_id
                    and account_user_id
                    and existing_user_id == account_user_id
                ):
                    merged = deepcopy(existing)
                    merged.update(deepcopy(account))
                    merged["updated_at"] = int(time.time())
                    accounts[idx] = merged
                    account_id = str(merged.get("id") or existing_id or account_id)
                    updated = True
                    break
            if not updated:
                fresh = deepcopy(account)
                now = int(time.time())
                fresh.setdefault("created_at", now)
                fresh["updated_at"] = now
                accounts.append(fresh)
            config = self.set_accounts(accounts)
            target_id = account_id or str(account.get("id") or "")
            for item in config["accounts"]:
                if (
                    not target_id
                    or item.get("id") == target_id
                    or item.get("user_id") == account.get("user_id")
                ):
                    return item
            return config["accounts"][-1]

    def delete_account(self, account_id: str) -> bool:
        with self._lock:
            accounts = self.get_accounts()
            remaining = [
                account for account in accounts if account.get("id") != account_id
            ]
            if len(remaining) == len(accounts):
                return False
            self.set_accounts(remaining)
            return True


_STORE = RuntimeConfigStore(CONFIG_PATH, ACCOUNTS_PATH)


def get_config_store() -> RuntimeConfigStore:
    return _STORE


def get_runtime_config() -> dict[str, Any]:
    return _STORE.get_config()


def get_accounts() -> list[dict[str, Any]]:
    return _STORE.get_accounts()


def get_admin_auth() -> dict[str, Any]:
    admin_auth = get_runtime_config().get("admin_auth")
    return _normalize_admin_auth(admin_auth)


def verify_admin_credentials(username: str, password: str) -> bool:
    admin_auth = get_admin_auth()
    normalized_username = str(username or "").strip()
    if normalized_username != str(admin_auth.get("username") or ""):
        return False
    return _admin_password_matches(admin_auth, password)


def update_admin_credentials(
    *,
    username: str,
    password: str,
    must_change_password: bool = False,
    initialized_from_default: bool = False,
) -> dict[str, Any]:
    store = get_config_store()
    config = store.get_config()
    config["admin_auth"] = _build_admin_auth_payload(
        username=username,
        password=password,
        must_change_password=must_change_password,
        initialized_from_default=initialized_from_default,
    )
    saved = store.save_config(config)
    return _normalize_admin_auth(saved.get("admin_auth"))


def get_admin_session_ttl_seconds() -> int:
    return _ADMIN_SESSION_TTL_SECONDS


def get_chat_session_ttl_seconds() -> int:
    return _CHAT_SESSION_TTL_SECONDS


def get_chat_auth() -> dict[str, Any]:
    chat_auth = get_runtime_config().get("chat_auth")
    return _normalize_chat_auth(chat_auth)


def is_chat_password_enabled() -> bool:
    return bool(get_chat_auth().get("enabled", False))


def verify_chat_password(password: str) -> bool:
    return _chat_password_matches(get_chat_auth(), password)


def update_chat_password(*, password: str, enabled: bool) -> dict[str, Any]:
    store = get_config_store()
    config = store.get_config()
    config["chat_auth"] = _build_chat_auth_payload(password=password, enabled=enabled)
    saved = store.save_config(config)
    return _normalize_chat_auth(saved.get("chat_auth"))


def get_api_key() -> str:
    return str(get_runtime_config().get("api_key") or "")


def get_media_public_base_url() -> str:
    return str(get_runtime_config().get("media_public_base_url") or "").strip()


def get_media_storage_path() -> Path:
    raw_path = str(get_runtime_config().get("media_storage_path") or "").strip()
    path = Path(raw_path or str(DATA_DIR / "media")).expanduser()
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_allowed_origins() -> list[str]:
    return _normalize_origins(get_runtime_config().get("allowed_origins"))


def get_db_path() -> str:
    return str(get_runtime_config().get("db_path") or DEFAULT_DB_PATH)


def get_siliconflow_api_key() -> str:
    return str(get_runtime_config().get("siliconflow_api_key") or "")


def get_upstream_proxy() -> str:
    return str(get_runtime_config().get("upstream_proxy") or "")


def get_upstream_http_proxy() -> str:
    return str(get_runtime_config().get("upstream_http_proxy") or "")


def get_upstream_https_proxy() -> str:
    return str(get_runtime_config().get("upstream_https_proxy") or "")


def get_upstream_socks5_proxy() -> str:
    return str(get_runtime_config().get("upstream_socks5_proxy") or "")


def get_upstream_proxy_mode() -> str:
    return str(get_runtime_config().get("upstream_proxy_mode") or "direct")


def get_upstream_warp_enabled() -> bool:
    return bool(get_runtime_config().get("upstream_warp_enabled", False))


def get_upstream_warp_proxy() -> str:
    return str(get_runtime_config().get("upstream_warp_proxy") or "")


def should_auto_create_workspace() -> bool:
    return bool(get_runtime_config().get("auto_create_workspace", False))


def should_auto_select_workspace() -> bool:
    return bool(get_runtime_config().get("auto_select_workspace", True))


def should_workspace_create_dry_run() -> bool:
    return bool(get_runtime_config().get("workspace_create_dry_run", True))


def get_workspace_creation_template_space_id() -> str:
    return str(get_runtime_config().get("workspace_creation_template_space_id") or "")


def get_account_probe_interval_seconds() -> int:
    try:
        return max(
            30, int(get_runtime_config().get("account_probe_interval_seconds", 300))
        )
    except (TypeError, ValueError):
        return 300


def get_app_mode() -> str:
    mode = str(get_runtime_config().get("app_mode") or "standard").lower().strip()
    return mode if mode in {"lite", "standard", "heavy"} else "standard"


def is_lite_mode() -> bool:
    return get_app_mode() == "lite"


def is_standard_mode() -> bool:
    return get_app_mode() == "standard"


def get_default_account() -> dict[str, Any]:
    accounts = get_accounts()
    if not accounts:
        raise ValueError("No accounts configured.")
    return accounts[0]

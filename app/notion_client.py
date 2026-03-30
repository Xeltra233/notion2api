import copy
import json
import threading
import time
import uuid
from collections.abc import Mapping, Sequence
from typing import Any, Generator, Optional

import cloudscraper
import requests

from app.config import (
    get_runtime_config,
    get_upstream_proxy_mode,
    get_workspace_creation_template_space_id,
    should_auto_create_workspace,
    should_auto_select_workspace,
    should_workspace_create_dry_run,
)
from app.model_registry import is_search_model
from app.logger import logger
from app.model_registry import get_notion_model
from app.register.mail_client import build_runtime_proxy_dict
from app.stream_parser import parse_stream

def _build_live_template_client_context(api: "NotionOpusAPI") -> dict[str, Any]:
    return {
        "platform": "web",
        "provider": "notion-web",
        "account_id": api.account_id,
        "account_key": api.account_key,
        "user_id": api.user_id,
        "space_id": api.space_id,
        "space_view_id": api.space_view_id or None,
        "user_email": api.user_email or None,
        "user_name": api.user_name or None,
    }


def _build_live_template_headers(
    api: "NotionOpusAPI", execution_kind: str
) -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (compatible; notion-opus-admin/1.0)",
        "X-Requested-With": "XMLHttpRequest",
        "X-Notion-Active-User-Header": api.user_id,
        "X-Notion-Space-Id": api.space_id,
    }
    if api.space_view_id:
        headers["X-Notion-Space-View-Id"] = api.space_view_id
    if execution_kind == "refresh":
        headers["X-Notion-Refresh-Execution"] = "formal_refresh"
    if execution_kind == "workspace":
        headers["X-Notion-Workspace-Execution"] = "formal_create_workspace"
    return headers


def _classify_probe_failure_category(status_code: Optional[int]) -> str:
    status = int(status_code or 0)
    if status == 401:
        return "unauthorized"
    if status == 403:
        return "forbidden"
    if status == 404:
        return "not_found"
    if status == 408:
        return "timeout"
    if status == 429:
        return "rate_limited"
    if status >= 500:
        return "server_error"
    if 200 <= status < 300:
        return "success"
    return "client_error"


def _limit_probe_value(value: Any, depth: int = 0) -> Any:
    if depth >= 4:
        return "<truncated>"
    if isinstance(value, str):
        return value[:500]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if isinstance(value, Mapping):
        items = list(value.items())[:20]
        return {
            str(key)[:120]: _limit_probe_value(item, depth + 1) for key, item in items
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_limit_probe_value(item, depth + 1) for item in list(value)[:20]]
    return str(value)[:500]


def _collect_probe_values(
    payload: Any, target_keys: set[str], found: dict[str, Any]
) -> None:
    if len(found) >= 20:
        return
    if isinstance(payload, Mapping):
        for key, value in payload.items():
            normalized_key = str(key or "").strip().lower()
            if normalized_key in target_keys and normalized_key not in found:
                found[normalized_key] = _limit_probe_value(value)
            _collect_probe_values(value, target_keys, found)
    elif isinstance(payload, Sequence) and not isinstance(
        payload, (str, bytes, bytearray)
    ):
        for item in list(payload)[:20]:
            _collect_probe_values(item, target_keys, found)


def _parse_probe_response(
    response: requests.Response, probe_kind: str
) -> dict[str, Any]:
    content_type_header = str(response.headers.get("Content-Type") or "").strip()
    content_type = content_type_header.split(";", 1)[0].strip().lower()
    response_text = response.text or ""
    excerpt = response_text[:300]
    parsed: dict[str, Any] = {
        "content_type": content_type,
        "response_excerpt": excerpt,
        "response_length": len(response_text),
        "response_format": "text" if response_text else "empty",
        "response_parse_error": "",
    }

    json_payload: Any = None
    try_json = bool(response_text) and (
        "json" in content_type
        or response_text.lstrip().startswith("{")
        or response_text.lstrip().startswith("[")
    )
    if try_json:
        try:
            json_payload = response.json()
            parsed["response_format"] = "json"
            parsed["response_json"] = _limit_probe_value(json_payload)
        except ValueError as exc:
            parsed["response_format"] = "text"
            parsed["response_parse_error"] = str(exc)[:300]

    if isinstance(json_payload, Mapping):
        token_keys = {
            "access_token",
            "refresh_token",
            "expires_in",
            "token_type",
            "scope",
            "error",
            "error_description",
            "message",
            "request_id",
            "trace_id",
        }
        workspace_keys = {
            "workspace_id",
            "workspace_ids",
            "space_id",
            "space_ids",
            "created_space_id",
            "new_space_id",
            "transaction_id",
            "transaction_ids",
            "workspace_name",
            "workspace_slug",
            "subscription_tier",
            "space_view_id",
            "error",
            "error_description",
            "message",
            "request_id",
            "trace_id",
        }
        recognized_keys = token_keys if probe_kind == "refresh" else workspace_keys
        fields: dict[str, Any] = {}
        _collect_probe_values(json_payload, recognized_keys, fields)
        if fields:
            parsed["recognized_fields"] = fields

    return parsed


def _build_workspace_entries_from_recognized_fields(
    recognized_fields: dict[str, Any],
) -> list[dict[str, Any]]:
    candidate_ids: list[str] = []
    for key in ("workspace_ids", "space_ids"):
        value = recognized_fields.get(key)
        if isinstance(value, list):
            candidate_ids.extend(
                str(item).strip() for item in value if str(item).strip()
            )
    for key in ("workspace_id", "space_id", "created_space_id", "new_space_id"):
        value = str(recognized_fields.get(key) or "").strip()
        if value:
            candidate_ids.append(value)

    workspace_name = str(recognized_fields.get("workspace_name") or "").strip()
    workspace_slug = str(recognized_fields.get("workspace_slug") or "").strip()
    subscription_tier = str(recognized_fields.get("subscription_tier") or "").strip()
    space_view_id = str(recognized_fields.get("space_view_id") or "").strip()

    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, workspace_id in enumerate(candidate_ids):
        if not workspace_id or workspace_id in seen:
            continue
        seen.add(workspace_id)
        item: dict[str, Any] = {"id": workspace_id}
        if workspace_name:
            item["name"] = workspace_name
        if workspace_slug:
            item["slug"] = workspace_slug
        if subscription_tier:
            item["subscription_tier"] = subscription_tier
        if index == 0 and space_view_id:
            item["space_view_id"] = space_view_id
        entries.append(item)
    return entries


class NotionUpstreamError(RuntimeError):
    """Notion 上游请求失败或返回异常内容。"""

    def __init__(
        self,
        message: str,
        *,
        status_code: Optional[int] = None,
        retriable: bool = True,
        response_excerpt: str = "",
    ):
        super().__init__(message)
        self.status_code = status_code
        self.retriable = retriable
        self.response_excerpt = response_excerpt


class NotionOpusAPI:
    def __init__(self, account_config: dict):
        """
        从单组账号配置初始化 Notion 客户端。
        account_config 需要包含 token_v2, space_id, user_id, space_view_id, user_name, user_email
        """
        self.token_v2 = account_config.get("token_v2", "")
        self.space_id = account_config.get("space_id", "")
        self.user_id = account_config.get("user_id", "")
        self.space_view_id = account_config.get("space_view_id", "")
        self.user_name = account_config.get("user_name", "user")
        self.user_email = account_config.get("user_email", "")
        self.url = "https://www.notion.so/api/v3/runInferenceTranscript"
        self.delete_url = "https://www.notion.so/api/v3/saveTransactions"
        self.get_spaces_url = "https://www.notion.so/api/v3/getSpaces"
        self.get_upload_url = "https://www.notion.so/api/v3/getUploadFileUrl"
        self.load_user_content_url = "https://www.notion.so/api/v3/loadUserContent"
        self.account_key = self.user_email or self.user_id or "unknown-account"
        self.account_id = str(account_config.get("id") or "").strip()
        self.plan_type = (
            str(account_config.get("plan_type") or "unknown").strip() or "unknown"
        )
        self.oauth = (
            account_config.get("oauth")
            if isinstance(account_config.get("oauth"), dict)
            else {}
        )
        self.workspace = (
            account_config.get("workspace")
            if isinstance(account_config.get("workspace"), dict)
            else {}
        )
        self.status = (
            account_config.get("status")
            if isinstance(account_config.get("status"), dict)
            else {}
        )

    def _build_proxy_config(self) -> Optional[dict[str, str]]:
        return build_runtime_proxy_dict()

    def _should_allow_direct_workspace_fallback(self) -> bool:
        workspace_state = str(
            self.status.get("workspace_state") or self.workspace.get("state") or ""
        ).strip()
        return bool(self.status.get("workspace_hydration_pending", False)) and (
            workspace_state == "workspace_creation_pending"
            or str(self.space_id or "").startswith("pending-signup-")
        )

    def _is_proxy_transport_error(self, exc: Exception) -> bool:
        if isinstance(
            exc,
            (
                requests.exceptions.ProxyError,
                requests.exceptions.SSLError,
                requests.exceptions.ConnectionError,
                requests.exceptions.Timeout,
            ),
        ):
            return True
        lowered = str(exc).lower()
        return any(
            token in lowered
            for token in {
                "proxyerror",
                "socks",
                "connection refused",
                "connection reset",
                "ssl",
                "eof",
                "max retries exceeded",
                "timed out",
            }
        )

    def _post_with_optional_direct_fallback(
        self,
        url: str,
        *,
        json_payload: dict[str, Any],
        headers: dict[str, str],
        timeout: int,
        allow_direct_fallback: bool = False,
    ) -> requests.Response:
        proxies = self._build_proxy_config()
        try:
            return requests.post(
                url,
                json=json_payload,
                headers=headers,
                timeout=timeout,
                proxies=proxies,
            )
        except Exception as exc:
            if (
                not allow_direct_fallback
                or get_upstream_proxy_mode().strip().lower() == "direct"
                or not proxies
                or not self._is_proxy_transport_error(exc)
            ):
                raise
            logger.warning(
                "Workspace request retrying without proxy",
                extra={
                    "request_info": {
                        "event": "workspace_direct_fallback",
                        "account_id": self.account_id,
                        "space_id": self.space_id,
                        "url": url,
                    }
                },
            )
            return requests.post(
                url,
                json=json_payload,
                headers=headers,
                timeout=timeout,
                proxies=None,
            )

    def classify_status(self, status_code: Optional[int]) -> dict[str, Any]:
        status = int(status_code or 0)
        if status == 401:
            return {"retriable": False, "cooldown_seconds": 600, "state": "invalid"}
        if status == 403:
            return {"retriable": False, "cooldown_seconds": 300, "state": "invalid"}
        if status == 429:
            return {"retriable": False, "cooldown_seconds": 90, "state": "cooling"}
        if status >= 500:
            return {"retriable": True, "cooldown_seconds": 15, "state": "cooling"}
        return {"retriable": True, "cooldown_seconds": 10, "state": "cooling"}

    def list_spaces(self, allow_direct_fallback: bool = False) -> list[dict[str, Any]]:
        headers = self._build_thread_headers()
        resp = self._post_with_optional_direct_fallback(
            self.get_spaces_url,
            json_payload={},
            headers=headers,
            timeout=15,
            allow_direct_fallback=allow_direct_fallback,
        )
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, dict) or not data:
            return []

        root = data.get(self.user_id)
        if not isinstance(root, dict):
            first_value = next(iter(data.values()), None)
            root = first_value if isinstance(first_value, dict) else {}

        spaces = root.get("space", {}) if isinstance(root, dict) else {}
        if not isinstance(spaces, dict):
            return []

        items: list[dict[str, Any]] = []
        for space_id, space_obj in spaces.items():
            if not isinstance(space_obj, dict):
                continue
            value = space_obj.get("value")
            if not isinstance(value, dict):
                continue
            items.append(
                {
                    "id": str(space_id or value.get("id", "") or "").strip(),
                    "name": str(value.get("name", "") or "").strip(),
                    "plan_type": str(value.get("plan_type", "") or "").strip(),
                    "subscription_tier": str(
                        value.get("subscription_tier", "") or ""
                    ).strip(),
                    "role": str(space_obj.get("role", "") or "").strip(),
                }
            )
        return [item for item in items if item.get("id")]

    def try_refresh_session_probe(self) -> dict[str, Any]:
        refresh_template = self.try_refresh_session()
        runtime_config = get_runtime_config()
        if bool(runtime_config.get("allow_real_probe_requests", False)):
            refresh_mode = (
                str(runtime_config.get("refresh_execution_mode") or "manual")
                .strip()
                .lower()
            )
            refresh_url = str(runtime_config.get("refresh_request_url") or "").strip()
            client_id = str(runtime_config.get("refresh_client_id") or "").strip()
            client_secret = str(
                runtime_config.get("refresh_client_secret") or ""
            ).strip()
            refresh_token = str(self.oauth.get("refresh_token") or "").strip()
            if (
                refresh_mode == "live_template"
                and refresh_url
                and client_id
                and client_secret
                and refresh_token
            ):
                request_body = {
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                    "client_id": client_id,
                    "client_secret": client_secret,
                }
                try:
                    response = requests.post(
                        refresh_url,
                        json=request_body,
                        headers={"Content-Type": "application/json"},
                        timeout=10,
                    )
                    parsed_response = _parse_probe_response(response, "refresh")
                    category = _classify_probe_failure_category(response.status_code)
                    recognized = (
                        parsed_response.get("recognized_fields")
                        if isinstance(parsed_response.get("recognized_fields"), dict)
                        else {}
                    )
                    reason = "Real upstream refresh probe request was sent."
                    if recognized.get("error"):
                        reason = (
                            f"Real upstream refresh probe returned error: "
                            f"{str(recognized.get('error'))[:120]}"
                        )
                    elif response.ok and recognized.get("access_token"):
                        reason = "Real upstream refresh probe returned token fields."
                    return {
                        "ok": response.ok,
                        "probed": True,
                        "reason": reason,
                        "status_code": response.status_code,
                        "failure_category": category,
                        **parsed_response,
                        "request_template": {
                            "method": "POST",
                            "url": refresh_url,
                            "headers": {
                                "Content-Type": "application/json",
                            },
                            "body": {
                                "grant_type": "refresh_token",
                                "refresh_token": "***redacted***",
                                "client_id": client_id,
                                "client_secret": "***redacted***",
                            },
                            "mode": "live_template",
                        },
                        "action": "real_refresh_probe_sent",
                    }
                except requests.RequestException as exc:
                    return {
                        "ok": False,
                        "probed": True,
                        "reason": f"Real upstream refresh probe failed: {str(exc)[:300]}",
                        "failure_category": "network_error",
                        "request_template": {
                            "method": "POST",
                            "url": refresh_url,
                            "headers": {
                                "Content-Type": "application/json",
                            },
                            "body": {
                                "grant_type": "refresh_token",
                                "refresh_token": "***redacted***",
                                "client_id": client_id,
                                "client_secret": "***redacted***",
                            },
                            "mode": "live_template",
                        },
                        "action": "real_refresh_probe_failed",
                    }
            return {
                "ok": False,
                "probed": False,
                "reason": "Real upstream refresh probe execution is blocked. The global flag exists, but actual network sending is intentionally disabled until the upstream refresh exchange is verified.",
                "request_template": refresh_template.get("request_template"),
                "action": "real_refresh_probe_blocked",
            }
        if refresh_template.get("action") != "dry_run_refresh_request":
            refresh_mode = (
                str(runtime_config.get("refresh_execution_mode") or "manual")
                .strip()
                .lower()
            )
            refresh_url = str(runtime_config.get("refresh_request_url") or "").strip()
            client_id = str(runtime_config.get("refresh_client_id") or "").strip()
            client_secret = str(
                runtime_config.get("refresh_client_secret") or ""
            ).strip()
            if (
                refresh_mode == "live_template"
                and refresh_url
                and client_id
                and client_secret
            ):
                return {
                    "ok": False,
                    "probed": True,
                    "reason": "Refresh probe executed in live-template mode. The request skeleton is fully populated, but no upstream request was sent.",
                    "request_template": {
                        "method": "POST",
                        "url": refresh_url,
                        "headers": {
                            "Content-Type": "application/json",
                        },
                        "body": {
                            "grant_type": "refresh_token",
                            "refresh_token": "***redacted***",
                            "client_id": client_id,
                            "client_secret": "***redacted***",
                        },
                        "mode": "live_template",
                    },
                    "action": "refresh_probe_live_template",
                }
            return {
                "ok": False,
                "probed": False,
                "reason": "Refresh probe only supports dry_run_refresh_request mode right now.",
                "request_template": refresh_template.get("request_template"),
            }

        return {
            "ok": False,
            "probed": True,
            "reason": "Refresh probe executed in dry-run mode. No upstream request was sent.",
            "request_template": refresh_template.get("request_template"),
            "action": "refresh_probe_dry_run",
        }

    def try_workspace_create_probe(self) -> dict[str, Any]:
        runtime_config = get_runtime_config()
        if bool(runtime_config.get("allow_real_probe_requests", False)):
            workspace_mode = (
                str(runtime_config.get("workspace_execution_mode") or "manual")
                .strip()
                .lower()
            )
            workspace_url = str(
                runtime_config.get("workspace_request_url") or ""
            ).strip()
            if workspace_mode == "live_template" and workspace_url:
                request_body = {
                    "operation": "create_workspace",
                    "template_space_id": get_workspace_creation_template_space_id().strip()
                    or None,
                    "source_space_id": self.space_id,
                    "user_id": self.user_id,
                    "space_view_id": self.space_view_id or None,
                    "transactions": [
                        {
                            "id": "workspace-probe-transaction",
                            "space_id": self.space_id,
                            "debug": "replace with real Notion transaction payload",
                        }
                    ],
                }
                try:
                    response = requests.post(
                        workspace_url,
                        json=request_body,
                        headers={
                            "Content-Type": "application/json",
                            "x-notion-active-user-header": self.user_id,
                        },
                        timeout=10,
                    )
                    parsed_response = _parse_probe_response(response, "workspace")
                    category = _classify_probe_failure_category(response.status_code)
                    recognized = (
                        parsed_response.get("recognized_fields")
                        if isinstance(parsed_response.get("recognized_fields"), dict)
                        else {}
                    )
                    reason = "Real upstream workspace probe request was sent."
                    if recognized.get("error"):
                        reason = (
                            f"Real upstream workspace probe returned error: "
                            f"{str(recognized.get('error'))[:120]}"
                        )
                    elif response.ok and (
                        recognized.get("workspace_id")
                        or recognized.get("created_space_id")
                        or recognized.get("new_space_id")
                        or recognized.get("space_id")
                    ):
                        reason = "Real upstream workspace probe returned workspace identifiers."
                    return {
                        "ok": response.ok,
                        "probed": True,
                        "reason": reason,
                        "status_code": response.status_code,
                        "failure_category": category,
                        **parsed_response,
                        "request_template": {
                            "method": "POST",
                            "url": workspace_url,
                            "headers": {
                                "Content-Type": "application/json",
                                "x-notion-active-user-header": self.user_id,
                            },
                            "body": {
                                "operation": "create_workspace",
                                "template_space_id": get_workspace_creation_template_space_id().strip()
                                or None,
                                "source_space_id": self.space_id,
                                "user_id": self.user_id,
                                "space_view_id": self.space_view_id or None,
                                "transactions": [
                                    {
                                        "id": "***workspace-creation-transaction-id***",
                                        "space_id": self.space_id,
                                        "debug": "replace with real Notion transaction payload",
                                    }
                                ],
                            },
                            "mode": "live_template",
                        },
                        "action": "real_workspace_probe_sent",
                    }
                except requests.RequestException as exc:
                    return {
                        "ok": False,
                        "probed": True,
                        "reason": f"Real upstream workspace probe failed: {str(exc)[:300]}",
                        "failure_category": "network_error",
                        "request_template": {
                            "method": "POST",
                            "url": workspace_url,
                            "headers": {
                                "Content-Type": "application/json",
                                "x-notion-active-user-header": self.user_id,
                            },
                            "body": {
                                "operation": "create_workspace",
                                "template_space_id": get_workspace_creation_template_space_id().strip()
                                or None,
                                "source_space_id": self.space_id,
                                "user_id": self.user_id,
                                "space_view_id": self.space_view_id or None,
                                "transactions": [
                                    {
                                        "id": "***workspace-creation-transaction-id***",
                                        "space_id": self.space_id,
                                        "debug": "replace with real Notion transaction payload",
                                    }
                                ],
                            },
                            "mode": "live_template",
                        },
                        "action": "real_workspace_probe_failed",
                    }
            return {
                "ok": False,
                "probed": False,
                "reason": "Real upstream workspace probe execution is blocked. The global flag exists, but actual transaction sending is intentionally disabled until the upstream Notion transaction is verified.",
                "request_template": self.maybe_create_workspace().get(
                    "request_template"
                ),
                "action": "real_workspace_probe_blocked",
            }
        workspace_mode = (
            str(runtime_config.get("workspace_execution_mode") or "manual")
            .strip()
            .lower()
        )
        workspace_url = str(runtime_config.get("workspace_request_url") or "").strip()
        if workspace_mode == "live_template" and workspace_url:
            return {
                "ok": False,
                "probed": True,
                "reason": "Workspace probe executed in live-template mode. The request skeleton is fully populated, but no upstream transaction was sent.",
                "request_template": {
                    "method": "POST",
                    "url": workspace_url,
                    "headers": {
                        "Content-Type": "application/json",
                        "x-notion-active-user-header": self.user_id,
                    },
                    "body": {
                        "operation": "create_workspace",
                        "template_space_id": get_workspace_creation_template_space_id().strip()
                        or None,
                        "source_space_id": self.space_id,
                        "user_id": self.user_id,
                        "space_view_id": self.space_view_id or None,
                        "transactions": [
                            {
                                "id": "***workspace-creation-transaction-id***",
                                "space_id": self.space_id,
                                "debug": "replace with real Notion transaction payload",
                            }
                        ],
                    },
                    "mode": "live_template",
                },
                "action": "workspace_create_probe_live_template",
            }

        workspace_template = self.maybe_create_workspace()
        if workspace_template.get("action") != "dry_run_workspace_create_request":
            return {
                "ok": False,
                "probed": False,
                "reason": "Workspace probe only supports dry_run_workspace_create_request mode right now.",
                "request_template": workspace_template.get("request_template"),
            }

        return {
            "ok": False,
            "probed": True,
            "reason": "Workspace create probe executed in dry-run mode. No upstream transaction was sent.",
            "request_template": workspace_template.get("request_template"),
            "action": "workspace_create_probe_dry_run",
        }

    def sync_workspace_context(self, spaces: list[dict[str, Any]]) -> None:
        if not spaces:
            return
        selected = None
        pending_signup_space = str(self.space_id or "").startswith("pending-signup-")
        if self.space_id:
            selected = next(
                (space for space in spaces if space.get("id") == self.space_id), None
            )
        if selected is None and pending_signup_space:
            selected = spaces[0]
        if selected is None and should_auto_select_workspace():
            selected = spaces[0]

        if selected is not None:
            self.space_id = str(selected.get("id", "") or self.space_id)
            selected_view_id = str(selected.get("space_view_id", "") or "").strip()
            if selected_view_id:
                self.space_view_id = selected_view_id
                return
            matching_view_id = self._fetch_space_view_id(
                self.space_id,
                allow_direct_fallback=pending_signup_space,
            )
            if matching_view_id:
                self.space_view_id = matching_view_id

    def _fetch_space_view_id(
        self, target_space_id: str, allow_direct_fallback: bool = False
    ) -> str:
        headers = self._build_thread_headers()
        resp = self._post_with_optional_direct_fallback(
            self.load_user_content_url,
            json_payload={},
            headers=headers,
            timeout=15,
            allow_direct_fallback=allow_direct_fallback,
        )
        resp.raise_for_status()
        data = resp.json()
        record_map = data.get("recordMap", {}) if isinstance(data, dict) else {}
        space_views = (
            record_map.get("space_view", {}) if isinstance(record_map, dict) else {}
        )
        for view_id, view_obj in space_views.items():
            if not isinstance(view_obj, dict):
                continue
            value = view_obj.get("value")
            if (
                isinstance(value, dict)
                and str(value.get("space_id", "") or "") == target_space_id
            ):
                return str(view_id)
        return self.space_view_id

    def maybe_create_workspace(self) -> dict[str, Any]:
        runtime_config = get_runtime_config()
        template_space_id = get_workspace_creation_template_space_id().strip()
        workspace_mode = (
            str(runtime_config.get("workspace_execution_mode") or "manual")
            .strip()
            .lower()
        )
        workspace_url = str(runtime_config.get("workspace_request_url") or "").strip()
        live_headers = _build_live_template_headers(self, "workspace")
        request_id = str(uuid.uuid4())
        transaction_id = str(uuid.uuid4())
        workspace_name = self.user_name or self.user_email or "Workspace"
        created_time_ms = int(time.time() * 1000)
        permission_record_id = str(uuid.uuid4())
        base_transaction = {
            "id": transaction_id,
            "space_id": self.space_id,
            "table": "space",
            "command": "create",
            "version": 1,
            "actor_id": self.user_id,
            "request_id": request_id,
            "path": [],
            "pointer": {
                "table": "space",
                "id": transaction_id,
                "spaceId": self.space_id,
                "workspaceId": transaction_id,
            },
            "args": {
                "type": "team",
                "name": workspace_name,
                "source_space_id": self.space_id,
                "template_space_id": template_space_id or None,
                "icon": "🚀",
                "locale": "en-US",
                "timezone": "UTC",
                "is_setup_flow": True,
                "owner_user_id": self.user_id,
                "permission": "editor",
                "created_time": created_time_ms,
                "created_by_id": self.user_id,
                "created_by_table": "notion_user",
                "plan_type": self.plan_type or "unknown",
                "space_permission": "editor",
                "invited_user_count": 0,
                "default_page_icon": "🚀",
                "root_pages": [],
                "bootstrap_flags": {
                    "is_ai_first_workspace": False,
                    "is_importing_template": bool(template_space_id),
                    "should_seed_getting_started": True,
                    "should_seed_teamspace": True,
                },
            },
            "operations": [
                {
                    "pointer": {
                        "table": "space",
                        "id": transaction_id,
                        "spaceId": self.space_id,
                        "workspaceId": transaction_id,
                    },
                    "path": [],
                    "command": "set",
                    "args": {
                        "id": transaction_id,
                        "version": 1,
                        "name": workspace_name,
                        "type": "team",
                        "space_id": transaction_id,
                        "source_space_id": self.space_id,
                        "template_space_id": template_space_id or None,
                        "created_time": created_time_ms,
                        "created_by_id": self.user_id,
                        "created_by_table": "notion_user",
                        "owner_user_id": self.user_id,
                        "icon": "🚀",
                        "locale": "en-US",
                        "timezone": "UTC",
                        "permission": "editor",
                        "is_setup_flow": True,
                        "plan_type": self.plan_type or "unknown",
                        "is_trial": False,
                        "settings": {
                            "allowed_guests": True,
                            "sso_required": False,
                        },
                    },
                },
                {
                    "pointer": {
                        "table": "space_permission",
                        "id": permission_record_id,
                        "spaceId": transaction_id,
                    },
                    "path": [],
                    "command": "set",
                    "args": {
                        "id": permission_record_id,
                        "version": 1,
                        "space_id": transaction_id,
                        "user_id": self.user_id,
                        "role": "workspace_owner",
                        "permission": "editor",
                        "created_time": created_time_ms,
                        "created_by_id": self.user_id,
                        "created_by_table": "notion_user",
                    },
                },
            ],
        }
        request_template = {
            "method": "POST",
            "url": workspace_url or "https://www.notion.so/api/v3/saveTransactions",
            "headers": live_headers,
            "body": {
                "operation": "create_workspace",
                "request_mode": "live_template",
                "request_source": "admin_workspaces_create",
                "template_space_id": template_space_id or None,
                "source_space_id": self.space_id,
                "user_id": self.user_id,
                "space_view_id": self.space_view_id or None,
                "request_id": request_id,
                "event_name": "workspace_create_live_template",
                "transaction_type": "create-space",
                "transaction_count": 1,
                "record_context": {
                    "space_id": self.space_id,
                    "space_view_id": self.space_view_id or None,
                    "template_space_id": template_space_id or None,
                    "transaction_id": transaction_id,
                    "workspace_name": workspace_name,
                    "permission_record_id": permission_record_id,
                },
                "client_context": _build_live_template_client_context(self),
                "transactions": [
                    {
                        **base_transaction,
                        "id": "***workspace-creation-transaction-id***",
                        "request_id": "***workspace-create-request-id***",
                        "pointer": {
                            "table": "space",
                            "id": "***workspace-creation-transaction-id***",
                            "spaceId": self.space_id,
                            "workspaceId": "***workspace-creation-transaction-id***",
                        },
                        "debug": "replace with real Notion transaction payload",
                    }
                ],
            },
            "operation": "create_workspace",
            "provider": "notion-web",
            "template_space_id": template_space_id or None,
            "space_id": self.space_id,
            "user_id": self.user_id,
            "redactions": {
                "headers": [],
                "body": [],
            },
            "field_hints": {
                "body.source_space_id": "Current source workspace used to derive creation context. Review before sharing outside the tenant.",
                "body.space_view_id": "Optional workspace view context that helps match the browser session state.",
                "body.request_id": "Per-request correlation id. Safe to regenerate.",
                "body.record_context": "Replay-safe metadata describing source/template linkage.",
                "body.client_context": "Replay-safe browser context metadata.",
                "body.transactions[0].request_id": "Transaction correlation id. Safe to rotate per replay.",
                "body.transactions[0].pointer": "Pointer block mirrors the Notion saveTransactions target entity and is safe to regenerate for local replay.",
                "body.transactions[0].operations": "Nested operations more closely mirror Notion saveTransactions semantics for create/set payloads.",
                "body.transactions[0].operations[0].pointer": "Operation-level pointer describes the record that the set command materializes.",
                "body.transactions[0].operations[0].args.created_by_id": "Tenant-specific creator identifier; mask in external examples.",
                "body.transactions[0].operations[0].args.settings": "Workspace settings scaffold for replay; values are placeholders until upstream schema is fully verified.",
                "body.transactions[0].operations[1]": "Secondary permission record models the initial owner membership grant for the new workspace.",
                "body.transactions[0].operations[1].args.user_id": "Tenant-specific owner identifier; mask in external examples.",
                "body.transactions[0].args.owner_user_id": "Workspace owner identifier. Treat as tenant-specific and mask in external examples.",
                "body.transactions[0].args.template_space_id": "Optional template workspace reference. Mask if the template itself is sensitive.",
            },
            "notes": [
                "No body fields are automatically redacted here, but source_space_id, user_id, and template identifiers may still be tenant-sensitive.",
                "Use field_hints to decide which identifiers to mask before sharing payloads outside the admin team.",
                "transactions[0].operations is intentionally closer to observed saveTransactions create/set structure than the previous flat placeholder payload.",
                "A secondary space_permission operation is included to better reflect ownership bootstrap during workspace creation.",
            ],
        }
        if workspace_mode == "live_template" and workspace_url:
            request_body = {
                "operation": "create_workspace",
                "request_mode": "live_template",
                "request_source": "admin_workspaces_create",
                "template_space_id": template_space_id or None,
                "source_space_id": self.space_id,
                "user_id": self.user_id,
                "space_view_id": self.space_view_id or None,
                "request_id": request_id,
                "event_name": "workspace_create_live_template",
                "transaction_type": "create-space",
                "transaction_count": 1,
                "record_context": {
                    "space_id": self.space_id,
                    "space_view_id": self.space_view_id or None,
                    "template_space_id": template_space_id or None,
                    "transaction_id": transaction_id,
                    "workspace_name": workspace_name,
                    "permission_record_id": permission_record_id,
                },
                "client_context": _build_live_template_client_context(self),
                "transactions": [base_transaction],
            }
            try:
                response = requests.post(
                    workspace_url,
                    json=request_body,
                    headers=live_headers,
                    timeout=10,
                )
                parsed_response = _parse_probe_response(response, "workspace")
                recognized = (
                    parsed_response.get("recognized_fields")
                    if isinstance(parsed_response.get("recognized_fields"), dict)
                    else {}
                )
                reason = "Real workspace creation request was sent."
                created = False
                if recognized.get("error"):
                    reason = (
                        f"Real workspace creation returned error: "
                        f"{str(recognized.get('error'))[:120]}"
                    )
                elif response.ok and (
                    recognized.get("workspace_id")
                    or recognized.get("created_space_id")
                    or recognized.get("new_space_id")
                    or recognized.get("space_id")
                ):
                    reason = "Real workspace creation returned workspace identifiers."
                    created = True
                workspaces = _build_workspace_entries_from_recognized_fields(recognized)
                state = (
                    "ready" if response.ok and created else "workspace_creation_pending"
                )
                if recognized.get("error") or recognized.get("error_description"):
                    state = "workspace_creation_failed"
                return {
                    "ok": response.ok,
                    "created": created,
                    "reason": reason,
                    "reversed": False,
                    "state": state,
                    "status_code": response.status_code,
                    "failure_category": _classify_probe_failure_category(
                        response.status_code
                    ),
                    "workspaces": workspaces,
                    "workspace_count": len(workspaces),
                    "space_id": str(
                        recognized.get("workspace_id")
                        or recognized.get("space_id")
                        or recognized.get("created_space_id")
                        or recognized.get("new_space_id")
                        or self.space_id
                        or ""
                    ).strip(),
                    "template_space_id": template_space_id,
                    "request_template": {**request_template, "mode": "live_template"},
                    **parsed_response,
                    "action": "create_workspace_live_template",
                }
            except requests.RequestException as exc:
                return {
                    "ok": False,
                    "created": False,
                    "reason": f"Real workspace creation failed: {str(exc)[:300]}",
                    "reversed": False,
                    "state": "workspace_creation_failed",
                    "failure_category": "network_error",
                    "template_space_id": template_space_id,
                    "request_template": {**request_template, "mode": "live_template"},
                    "action": "create_workspace_live_template_failed",
                }
        if should_workspace_create_dry_run():
            return {
                "ok": True,
                "created": False,
                "reason": "workspace_create_dry_run is enabled",
                "reversed": False,
                "dry_run": True,
                "state": "workspace_creation_pending",
                "action": "dry_run_workspace_create_request",
                "template_space_id": template_space_id,
                "request_template": {
                    **request_template,
                    "notes": [
                        "This is a dry-run template only.",
                        "The real Notion workspace creation transaction still needs verification.",
                        "Replace placeholder transaction blocks after reverse engineering.",
                    ],
                },
            }

        if template_space_id:
            return {
                "ok": False,
                "created": False,
                "reason": "Workspace creation template is configured but the creation transaction is not implemented yet.",
                "reversed": False,
                "state": "workspace_creation_unimplemented",
                "action": "implement_workspace_create_transaction",
                "template_space_id": template_space_id,
                "request_template": {
                    **request_template,
                    "notes": [
                        "Template workspace is configured.",
                        "A real saveTransactions payload still needs implementation.",
                    ],
                },
            }
        return {
            "ok": False,
            "created": False,
            "reason": "Workspace auto-creation is not implemented yet because the exact Notion creation transaction is still unverified.",
            "reversed": False,
            "state": "workspace_creation_unverified",
            "action": "reverse_engineer_workspace_create",
            "request_template": {
                **request_template,
                "notes": [
                    "No template workspace configured.",
                    "Reverse engineer the upstream transaction before enabling real creation.",
                ],
            },
        }

    def get_account_profile(self) -> dict[str, Any]:
        spaces: list[dict[str, Any]] = []
        try:
            spaces = self.list_spaces()
        except Exception:
            spaces = []

        current_space = next(
            (space for space in spaces if space.get("id") == self.space_id), None
        )
        detected_plan = (
            str(
                (current_space or {}).get("plan_type") or self.plan_type or "unknown"
            ).strip()
            or "unknown"
        )
        subscription_tier = str(
            (current_space or {}).get("subscription_tier") or ""
        ).strip()

        return {
            "account_id": self.account_id,
            "account_key": self.account_key,
            "user_id": self.user_id,
            "user_email": self.user_email,
            "user_name": self.user_name,
            "space_id": self.space_id,
            "space_view_id": self.space_view_id,
            "plan_type": detected_plan,
            "subscription_tier": subscription_tier,
            "workspace_count": len(spaces),
            "workspaces": spaces,
            "oauth": copy.deepcopy(self.oauth),
            "workspace": copy.deepcopy(self.workspace),
            "status": copy.deepcopy(self.status),
        }

    def get_oauth_status(self) -> dict[str, Any]:
        expires_at_raw = self.oauth.get("expires_at")
        try:
            expires_at = int(expires_at_raw) if expires_at_raw is not None else 0
        except (TypeError, ValueError):
            expires_at = 0

        now = int(time.time())
        expired = bool(expires_at and expires_at <= now)
        expires_in = max(0, expires_at - now) if expires_at else None
        needs_refresh = bool(expires_at and expires_at - now <= 600)
        has_credentials = bool(self.token_v2.strip()) or bool(
            str(self.oauth.get("access_token") or "").strip()
        )
        return {
            "provider": str(self.oauth.get("provider") or ""),
            "has_access_token": bool(str(self.oauth.get("access_token") or "").strip()),
            "has_refresh_token": bool(
                str(self.oauth.get("refresh_token") or "").strip()
            ),
            "expires_at": expires_at or None,
            "expires_in": expires_in,
            "expired": expired,
            "needs_refresh": needs_refresh,
            "has_credentials": has_credentials,
            "scopes": self.oauth.get("scopes")
            if isinstance(self.oauth.get("scopes"), list)
            else [],
        }

    def try_refresh_session(self) -> dict[str, Any]:
        refresh_token = str(self.oauth.get("refresh_token") or "").strip()
        access_token = str(self.oauth.get("access_token") or "").strip()
        oauth_status = self.get_oauth_status()
        expires_in = oauth_status.get("expires_in")
        runtime_config = get_runtime_config()
        refresh_mode = (
            str(runtime_config.get("refresh_execution_mode") or "manual")
            .strip()
            .lower()
        )
        if refresh_token:
            if refresh_mode == "dry_run":
                return {
                    "ok": False,
                    "refreshed": False,
                    "reason": "Refresh token exists and dry-run refresh mode is enabled. A real refresh request template is prepared, but no upstream token exchange is executed.",
                    "has_refresh_token": True,
                    "has_access_token": bool(access_token),
                    "reauthorize_required": False,
                    "action": "dry_run_refresh_request",
                    "expires_in": expires_in,
                    "request_template": {
                        "method": "POST",
                        "url": "https://www.notion.so/api/v3/oauth/token",
                        "headers": {
                            "Content-Type": "application/json",
                            "Authorization": "Bearer ***access-token-if-required***",
                        },
                        "body": {
                            "grant_type": "refresh_token",
                            "refresh_token": "***redacted***",
                            "client_id": "***client-id-if-required***",
                            "client_secret": "***client-secret-if-required***",
                        },
                        "notes": [
                            "This is a dry-run template only.",
                            "Exact Notion web refresh endpoint and auth requirements still need verification.",
                            "Replace placeholders after upstream reverse engineering.",
                        ],
                        "grant_type": "refresh_token",
                        "refresh_token": "***redacted***",
                        "provider": str(self.oauth.get("provider") or "notion-web"),
                    },
                    "oauth_status": oauth_status,
                }
            refresh_url = str(runtime_config.get("refresh_request_url") or "").strip()
            client_id = str(runtime_config.get("refresh_client_id") or "").strip()
            client_secret = str(
                runtime_config.get("refresh_client_secret") or ""
            ).strip()
            if (
                refresh_mode == "live_template"
                and refresh_url
                and client_id
                and client_secret
            ):
                live_headers = _build_live_template_headers(self, "refresh")
                request_id = str(uuid.uuid4())
                request_body = {
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "request_id": request_id,
                    "event_name": "refresh_exchange_live_template",
                    "refresh_reason": "session_renewal",
                    "token_metadata": {
                        "provider": str(self.oauth.get("provider") or "notion-web"),
                        "has_access_token": bool(access_token),
                        "has_refresh_token": True,
                    },
                    "client_context": _build_live_template_client_context(self),
                }
                request_template = {
                    "method": "POST",
                    "url": refresh_url,
                    "headers": {
                        **live_headers,
                        "client_secret": "***redacted-header***",
                    },
                    "body": {
                        "grant_type": "refresh_token",
                        "refresh_token": "***redacted***",
                        "client_id": client_id,
                        "client_secret": "***redacted***",
                        "request_id": request_id,
                        "event_name": "refresh_exchange_live_template",
                        "refresh_reason": "session_renewal",
                        "token_metadata": {
                            "provider": str(self.oauth.get("provider") or "notion-web"),
                            "has_access_token": bool(access_token),
                            "has_refresh_token": True,
                        },
                        "client_context": _build_live_template_client_context(self),
                    },
                    "redactions": {
                        "headers": ["client_secret"],
                        "body": ["refresh_token", "client_secret"],
                    },
                    "field_hints": {
                        "headers.client_secret": "Secret mirror of runtime config. Keep redacted in copied templates and screenshots.",
                        "body.refresh_token": "Account secret. Never expose outside the runtime store or short-lived local debugging sessions.",
                        "body.client_id": "Usually safe to inspect internally, but still configuration data rather than public documentation.",
                        "body.client_secret": "Secret. Must stay masked in all shared payloads.",
                        "body.request_id": "Per-request correlation id. Safe to regenerate for replay.",
                        "body.client_context": "Safe diagnostic metadata describing the browser-like request context.",
                        "body.token_metadata": "Safe capability hints only; does not include raw credentials.",
                    },
                    "notes": [
                        "Sensitive refresh credentials are redacted in this template.",
                        "client_context and token_metadata are safe diagnostic scaffolding for replay/debugging.",
                        "Use field_hints to distinguish secret values from replay-safe metadata before exporting a request sample.",
                    ],
                    "mode": "live_template",
                }
                try:
                    response = requests.post(
                        refresh_url,
                        json=request_body,
                        headers=live_headers,
                        timeout=10,
                    )
                    parsed_response = _parse_probe_response(response, "refresh")
                    recognized = (
                        parsed_response.get("recognized_fields")
                        if isinstance(parsed_response.get("recognized_fields"), dict)
                        else {}
                    )
                    reason = "Real refresh exchange request was sent."
                    if recognized.get("error"):
                        reason = (
                            f"Real refresh exchange returned error: "
                            f"{str(recognized.get('error'))[:120]}"
                        )
                    elif response.ok and recognized.get("access_token"):
                        reason = "Real refresh exchange returned token fields."
                    return {
                        "ok": response.ok,
                        "refreshed": response.ok
                        and bool(recognized.get("access_token")),
                        "reason": reason,
                        "has_refresh_token": True,
                        "has_access_token": bool(
                            recognized.get("access_token") or access_token
                        ),
                        "reauthorize_required": str(recognized.get("error") or "")
                        .strip()
                        .lower()
                        in {
                            "invalid_grant",
                            "invalid_refresh_token",
                            "unauthorized_client",
                            "invalid_client",
                        },
                        "expires_in": recognized.get("expires_in", expires_in),
                        "oauth_status": oauth_status,
                        "status_code": response.status_code,
                        "failure_category": _classify_probe_failure_category(
                            response.status_code
                        ),
                        "request_template": request_template,
                        **parsed_response,
                        "action": "refresh_exchange_live_template",
                    }
                except requests.RequestException as exc:
                    return {
                        "ok": False,
                        "refreshed": False,
                        "reason": f"Real refresh exchange failed: {str(exc)[:300]}",
                        "has_refresh_token": True,
                        "has_access_token": bool(access_token),
                        "reauthorize_required": False,
                        "expires_in": expires_in,
                        "oauth_status": oauth_status,
                        "failure_category": "network_error",
                        "request_template": request_template,
                        "action": "refresh_exchange_failed",
                    }
            return {
                "ok": False,
                "refreshed": False,
                "reason": "Refresh token exists, but Notion OAuth refresh is not implemented yet. Reauthorize or wire the real refresh exchange.",
                "has_refresh_token": True,
                "has_access_token": bool(access_token),
                "reauthorize_required": False,
                "action": "implement_refresh_exchange",
                "expires_in": expires_in,
                "oauth_status": oauth_status,
            }
        return {
            "ok": False,
            "refreshed": False,
            "reason": "No refresh token available; manual re-authorization required.",
            "has_refresh_token": False,
            "has_access_token": bool(access_token),
            "reauthorize_required": True,
            "action": "manual_reauthorize",
            "expires_in": expires_in,
            "oauth_status": oauth_status,
        }

    def probe_account(self) -> dict[str, Any]:
        try:
            spaces = self.list_spaces()
            if not spaces and should_auto_create_workspace():
                creation_result = self.maybe_create_workspace()
                if creation_result.get("created"):
                    spaces = self.list_spaces()

            self.sync_workspace_context(spaces)
            current_space = next(
                (space for space in spaces if space.get("id") == self.space_id), None
            )
            detected_plan = (
                str(
                    (current_space or {}).get("plan_type")
                    or self.plan_type
                    or "unknown"
                ).strip()
                or "unknown"
            )
            subscription_tier = str(
                (current_space or {}).get("subscription_tier") or ""
            ).strip()
            workspace_ids = [space.get("id", "") for space in spaces if space.get("id")]
            excerpt = json_excerpt = str(workspace_ids[:5])[:300]
            if self.space_id and workspace_ids and self.space_id not in workspace_ids:
                return {
                    "ok": False,
                    "status_code": 409,
                    "workspace_count": len(spaces),
                    "workspaces": spaces,
                    "state": "invalid",
                    "cooldown_seconds": 300,
                    "retriable": False,
                    "plan_type": detected_plan,
                    "subscription_tier": subscription_tier,
                    "response_excerpt": f"Configured space_id {self.space_id} not found in accessible spaces {json_excerpt}",
                }

            return {
                "ok": True,
                "status_code": 200,
                "workspace_count": len(spaces),
                "workspaces": spaces,
                "state": "active",
                "cooldown_seconds": 0,
                "retriable": False,
                "plan_type": detected_plan,
                "subscription_tier": subscription_tier,
                "response_excerpt": json_excerpt,
            }
        except requests.HTTPError as exc:
            response = exc.response
            status_code = response.status_code if response is not None else None
            excerpt = (
                (response.text or "").strip().replace("\n", " ")[:300]
                if response is not None
                else str(exc)[:300]
            )
            classification = self.classify_status(status_code)
            return {
                "ok": False,
                "status_code": status_code,
                "workspace_count": 0,
                "workspaces": [],
                "state": classification["state"],
                "cooldown_seconds": classification["cooldown_seconds"],
                "retriable": classification["retriable"],
                "response_excerpt": excerpt,
            }
        except requests.exceptions.Timeout:
            return {
                "ok": False,
                "status_code": None,
                "workspace_count": 0,
                "workspaces": [],
                "state": "cooling",
                "cooldown_seconds": 15,
                "retriable": True,
                "response_excerpt": "timeout",
            }
        except requests.exceptions.RequestException as exc:
            return {
                "ok": False,
                "status_code": None,
                "workspace_count": 0,
                "workspaces": [],
                "state": "cooling",
                "cooldown_seconds": 15,
                "retriable": True,
                "response_excerpt": str(exc)[:300],
            }

    def _to_notion_transcript(
        self, transcript: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        converted: list[dict[str, Any]] = []
        for block in transcript:
            if block.get("type") != "config":
                converted.append(block)
                continue

            value = block.get("value")
            if not isinstance(value, dict):
                converted.append(block)
                continue

            notion_block = dict(block)
            notion_value = dict(value)
            notion_value["model"] = get_notion_model(str(value.get("model", "") or ""))
            notion_block["value"] = notion_value
            converted.append(notion_block)
        return converted

    def _resolve_thread_type(self, notion_transcript: list[dict[str, Any]]) -> str:
        for block in notion_transcript:
            if block.get("type") != "config":
                continue
            value = block.get("value")
            if isinstance(value, dict):
                thread_type = str(value.get("type", "") or "").strip()
                if thread_type:
                    return thread_type
        return "workflow"

    def _resolve_requested_model(self, notion_transcript: list[dict[str, Any]]) -> str:
        for block in notion_transcript:
            if block.get("type") != "config":
                continue
            value = block.get("value")
            if isinstance(value, dict):
                model_name = str(value.get("model", "") or "").strip()
                if model_name:
                    return model_name
        return ""

    def _resolve_request_profile(self, thread_type: str) -> dict[str, Any]:
        is_markdown_chat = thread_type == "markdown-chat"
        return {
            "thread_type": thread_type,
            "create_thread": not is_markdown_chat,
            "is_partial_transcript": is_markdown_chat,
            "precreate_thread": is_markdown_chat,
            "include_debug_overrides": True,
        }

    def _build_thread_headers(self) -> dict[str, str]:
        return {
            "content-type": "application/json",
            "cookie": f"token_v2={self.token_v2}",
            "x-notion-active-user-header": self.user_id,
            "x-notion-space-id": self.space_id,
        }

    def _create_thread(self, thread_id: str, thread_type: str) -> bool:
        payload = {
            "requestId": str(uuid.uuid4()),
            "transactions": [
                {
                    "id": str(uuid.uuid4()),
                    "spaceId": self.space_id,
                    "operations": [
                        {
                            "pointer": {
                                "table": "thread",
                                "id": thread_id,
                                "spaceId": self.space_id,
                            },
                            "path": [],
                            "command": "set",
                            "args": {
                                "id": thread_id,
                                "version": 1,
                                "parent_id": self.space_id,
                                "parent_table": "space",
                                "space_id": self.space_id,
                                "created_time": int(time.time() * 1000),
                                "created_by_id": self.user_id,
                                "created_by_table": "notion_user",
                                "messages": [],
                                "data": {},
                                "alive": True,
                                "type": thread_type,
                            },
                        }
                    ],
                }
            ],
        }
        try:
            resp = requests.post(
                self.delete_url,
                json=payload,
                headers=self._build_thread_headers(),
                timeout=20,
                proxies=self._build_proxy_config(),
            )
            if resp.status_code == 200:
                return True
            logger.warning(
                "Pre-create thread failed",
                extra={
                    "request_info": {
                        "event": "thread_precreate_failed",
                        "thread_id": thread_id,
                        "thread_type": thread_type,
                        "status": resp.status_code,
                    }
                },
            )
        except Exception:
            logger.warning(
                "Pre-create thread raised exception",
                exc_info=True,
                extra={
                    "request_info": {
                        "event": "thread_precreate_error",
                        "thread_id": thread_id,
                        "thread_type": thread_type,
                    }
                },
            )
        return False

    def delete_thread(self, thread_id: str) -> None:
        """
        通过 saveTransactions 接口将指定 thread 的 alive 状态设为 False，
        从而清理 Notion 主页面上的对话记录。
        此方法设计为在后台线程中调用，不影响主流输出。
        """
        headers = self._build_thread_headers()
        payload = {
            "requestId": str(uuid.uuid4()),
            "transactions": [
                {
                    "id": str(uuid.uuid4()),
                    "spaceId": self.space_id,
                    "operations": [
                        {
                            "pointer": {
                                "table": "thread",
                                "id": thread_id,
                                "spaceId": self.space_id,
                            },
                            "command": "update",
                            "path": [],
                            "args": {"alive": False},
                        }
                    ],
                }
            ],
        }
        try:
            resp = requests.post(
                self.delete_url,
                json=payload,
                headers=headers,
                timeout=15,
                proxies=self._build_proxy_config(),
            )
            if resp.status_code == 200:
                logger.info(
                    "Thread auto-deleted from Notion home",
                    extra={
                        "request_info": {
                            "event": "thread_deleted",
                            "thread_id": thread_id,
                        }
                    },
                )
            else:
                logger.warning(
                    f"Thread deletion failed: HTTP {resp.status_code}",
                    extra={
                        "request_info": {
                            "event": "thread_delete_failed",
                            "thread_id": thread_id,
                            "status": resp.status_code,
                        }
                    },
                )
        except Exception as exc:
            logger.warning(
                f"Thread deletion raised an exception: {exc}",
                extra={
                    "request_info": {
                        "event": "thread_delete_error",
                        "thread_id": thread_id,
                    }
                },
            )

    def stream_response(
        self, transcript: list, thread_id: Optional[str] = None
    ) -> Generator[dict[str, Any], None, None]:
        """
        发起 Notion API 请求并返回结构化流生成器。
        接收完整的 transcript 列表作为参数。

        Args:
            transcript: 对话历史记录列表
            thread_id: 可选的已有 thread_id。如果提供，将重用该线程以保持上下文
        """
        if not isinstance(transcript, list) or not transcript:
            raise ValueError(
                "Invalid transcript payload: transcript must be a non-empty list."
            )

        notion_transcript = self._to_notion_transcript(transcript)
        thread_type = self._resolve_thread_type(notion_transcript)
        requested_model = self._resolve_requested_model(transcript)
        request_profile = self._resolve_request_profile(thread_type)

        # 如果没有提供 thread_id，创建新的；否则重用已有的
        should_create_thread = thread_id is None
        thread_id = thread_id or str(uuid.uuid4())
        trace_id = str(uuid.uuid4())
        response = None

        # 保存 thread_id 以便外部访问
        self.current_thread_id = thread_id

        if request_profile["precreate_thread"] and should_create_thread:
            if not self._create_thread(thread_id, thread_type):
                should_create_thread = True
                request_profile["create_thread"] = True
                request_profile["is_partial_transcript"] = False
        elif not should_create_thread:
            # 如果重用已有线程，不要创建新线程
            request_profile["create_thread"] = False
            # 关键修复：设置 is_partial_transcript=True，让 Notion 接受客户端的历史消息
            request_profile["is_partial_transcript"] = True

        cookies = {
            "token_v2": self.token_v2,
            "notion_user_id": self.user_id,
        }

        headers = {
            "Content-Type": "application/json",
            "Accept": "application/x-ndjson",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36",
            "x-notion-space-id": self.space_id,
            "x-notion-active-user-header": self.user_id,
            "notion-audit-log-platform": "web",
            "notion-client-version": "23.13.20260228.0625",
            "origin": "https://www.notion.so",
            "referer": "https://www.notion.so/ai",
        }

        payload = {
            "traceId": trace_id,
            "spaceId": self.space_id,
            "threadId": thread_id,
            "threadType": thread_type,
            "createThread": request_profile["create_thread"],
            "generateTitle": True,
            "saveAllThreadOperations": True,
            "setUnreadState": True,
            "isPartialTranscript": request_profile["is_partial_transcript"],
            "asPatchResponse": True,
            "isUserInAnySalesAssistedSpace": False,
            "isSpaceSalesAssisted": False,
            "threadParentPointer": {
                "table": "space",
                "id": self.space_id,
                "spaceId": self.space_id,
            },
            "transcript": notion_transcript,
        }
        if request_profile["include_debug_overrides"]:
            payload["debugOverrides"] = {
                "emitAgentSearchExtractedResults": True,
                "cachedInferences": {},
                "annotationInferences": {},
                "emitInferences": False,
            }

        logger.info(
            "Dispatching request to Notion upstream",
            extra={
                "request_info": {
                    "event": "notion_upstream_request",
                    "trace_id": trace_id,
                    "thread_id": thread_id,
                    "thread_type": thread_type,
                    "create_thread": bool(request_profile["create_thread"]),
                    "is_partial_transcript": bool(
                        request_profile["is_partial_transcript"]
                    ),
                    "account": self.account_key,
                    "space_id": self.space_id,
                }
            },
        )

        try:
            scraper = cloudscraper.create_scraper()
            read_timeout = 180 if is_search_model(requested_model) else 120
            response = scraper.post(
                self.url,
                cookies=cookies,
                headers=headers,
                json=payload,
                stream=True,
                timeout=(15, read_timeout),
                proxies=self._build_proxy_config(),
            )
            if response.status_code != 200:
                excerpt = (response.text or "").strip().replace("\n", " ")[:300]
                retriable = response.status_code >= 500  # 429 不再重试，避免账号被冷却
                raise NotionUpstreamError(
                    f"Notion upstream returned HTTP {response.status_code}.",
                    status_code=response.status_code,
                    retriable=retriable,
                    response_excerpt=excerpt,
                )

            emitted = False
            for chunk in parse_stream(response):
                emitted = True
                yield chunk

            if not emitted:
                raise NotionUpstreamError(
                    "Notion upstream returned an empty stream.",
                    status_code=502,
                    retriable=True,
                )

            # 流结束后，不再自动删除 thread
            # 原因：Notion API 的 workflow 模式依赖于服务器端保存的对话历史
            # 删除 thread 会导致后续请求无法获取历史消息（AI 失忆）
            # 保持 thread 存活可以维持对话上下文
            logger.info(
                "Thread completed and preserved for conversation context",
                extra={
                    "request_info": {
                        "event": "thread_completed_preserved",
                        "thread_id": thread_id,
                        "was_created_new": should_create_thread,
                    }
                },
            )
        except requests.exceptions.Timeout as exc:
            logger.error(f"Request timeout: {exc}", exc_info=True)
            raise NotionUpstreamError(
                "Request to Notion upstream timed out.", retriable=True
            ) from exc
        except requests.exceptions.RequestException as exc:
            logger.error(f"Request failed: {exc}", exc_info=True)
            # 不暴露原始异常细节给用户
            raise NotionUpstreamError(
                "Request to Notion upstream failed. Please try again later.",
                retriable=True,
            ) from exc
        finally:
            if response is not None:
                response.close()

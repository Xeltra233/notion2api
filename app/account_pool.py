import time
import threading
from typing import Any, Dict, List

from app.config import get_config_store
from app.logger import logger
from app.notion_client import NotionOpusAPI


class AccountPool:
    def __init__(self, accounts: List[dict]):
        """
        从配置列表初始化，每个 dict 对应一组凭据。
        同时初始化客户端实例和它们的状态。
        """
        active_accounts = [acc for acc in accounts if bool(acc.get("enabled", True))]
        self.clients = (
            [NotionOpusAPI(acc) for acc in active_accounts] if active_accounts else []
        )
        # 记录每个客户端的冷却释放时间戳（0 表示可用）
        self.cooldown_until = [0.0 for _ in self.clients]
        self.invalid_until = [0.0 for _ in self.clients]
        self.last_error = ["" for _ in self.clients]
        self.last_status_code = [None for _ in self.clients]
        self.last_success_at = [0.0 for _ in self.clients]
        self.workspace_count = [0 for _ in self.clients]
        self.workspaces = [[] for _ in self.clients]
        self.plan_types = ["unknown" for _ in self.clients]
        self.subscription_tiers = ["" for _ in self.clients]
        self.keepalive_failures = [0 for _ in self.clients]
        self.last_refresh_at = [0.0 for _ in self.clients]
        self.last_refresh_error = ["" for _ in self.clients]
        self.last_refresh_action = ["" for _ in self.clients]
        self.reauthorize_required = [False for _ in self.clients]
        self.last_workspace_check_at = [0.0 for _ in self.clients]
        self.last_workspace_action = ["" for _ in self.clients]
        self.last_workspace_error = ["" for _ in self.clients]
        self.workspace_poll_count = [0 for _ in self.clients]
        self.workspace_expand_error = ["" for _ in self.clients]
        self.workspace_expand_status_code = [None for _ in self.clients]

        # 轮询索引
        self._current_index = 0
        self._lock = threading.Lock()

    def _should_defer_background_workspace_io(self, client: NotionOpusAPI) -> bool:
        status = client.status if isinstance(client.status, dict) else {}
        workspace = client.workspace if isinstance(client.workspace, dict) else {}
        if not bool(status.get("workspace_hydration_pending", False)):
            return False
        workspace_state = str(
            status.get("workspace_state") or workspace.get("state") or ""
        ).strip()
        return workspace_state == "workspace_creation_pending"

    def _build_background_deferred_result(
        self, client: NotionOpusAPI, action: str
    ) -> Dict[str, Any]:
        retry_after = int(client.status.get("workspace_hydration_retry_after") or 0)
        return {
            "account": client.account_key,
            "account_id": client.account_id,
            "space_id": client.space_id,
            "ok": True,
            "skipped": True,
            "action": action,
            "reason": "background_hydration_guard",
            "workspace_hydration_retry_after": retry_after,
        }

    def expand_workspaces(self, background_mode: bool = False) -> None:
        expanded_clients: List[NotionOpusAPI] = []
        seen_keys: set[tuple[str, str]] = set()

        for client in self.clients:
            pending_hydration = self._should_defer_background_workspace_io(client)
            pending_space = str(client.space_id or "").startswith("pending-signup-")
            if pending_hydration and (pending_space or background_mode):
                idx = self._find_client_object_index(client, raise_on_missing=False)
                if idx is not None:
                    self.workspace_expand_error[idx] = ""
                    self.workspace_expand_status_code[idx] = None
                workspaces = []
                client.sync_workspace_context(workspaces)
                key = (client.account_key, client.space_id)
                if key not in seen_keys:
                    seen_keys.add(key)
                    expanded_clients.append(client)
                continue
            try:
                workspaces = client.list_spaces()
                idx = self._find_client_object_index(client, raise_on_missing=False)
                if idx is not None:
                    self.workspace_expand_error[idx] = ""
                    self.workspace_expand_status_code[idx] = None
            except Exception as exc:
                logger.warning(
                    "Failed to expand workspaces for account",
                    extra={
                        "request_info": {
                            "event": "workspace_expand_failed",
                            "account": client.account_key,
                            "space_id": client.space_id,
                            "detail": str(exc)[:300],
                        }
                    },
                )
                idx = self._find_client_object_index(client, raise_on_missing=False)
                if idx is not None:
                    self.workspace_expand_error[idx] = str(exc)[:300]
                    self.workspace_expand_status_code[idx] = getattr(
                        getattr(exc, "response", None), "status_code", None
                    )
                workspaces = []
            client.sync_workspace_context(workspaces)
            if not workspaces:
                key = (client.account_key, client.space_id)
                if key not in seen_keys:
                    seen_keys.add(key)
                    expanded_clients.append(client)
                continue

            for workspace in workspaces:
                workspace_id = str(workspace.get("id", "") or "").strip()
                if not workspace_id:
                    continue
                key = (client.account_key, workspace_id)
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                cloned = NotionOpusAPI(
                    {
                        "id": client.account_id,
                        "token_v2": client.token_v2,
                        "space_id": workspace_id,
                        "user_id": client.user_id,
                        "space_view_id": client.space_view_id,
                        "user_name": client.user_name,
                        "user_email": client.user_email,
                        "plan_type": client.plan_type,
                        "oauth": client.oauth,
                        "workspace": client.workspace,
                        "status": client.status,
                    }
                )
                cloned.sync_workspace_context([workspace])
                expanded_clients.append(cloned)

        if not expanded_clients:
            return

        self.clients = expanded_clients
        self.cooldown_until = [0.0 for _ in self.clients]
        self.invalid_until = [0.0 for _ in self.clients]
        self.last_error = ["" for _ in self.clients]
        self.last_status_code = [None for _ in self.clients]
        self.last_success_at = [0.0 for _ in self.clients]
        self.workspace_count = [0 for _ in self.clients]
        self.workspaces = [[] for _ in self.clients]
        self.plan_types = ["unknown" for _ in self.clients]
        self.subscription_tiers = ["" for _ in self.clients]
        self.keepalive_failures = [0 for _ in self.clients]
        self.last_refresh_at = [0.0 for _ in self.clients]
        self.last_refresh_error = ["" for _ in self.clients]
        self.last_refresh_action = ["" for _ in self.clients]
        self.reauthorize_required = [False for _ in self.clients]
        self.last_workspace_check_at = [0.0 for _ in self.clients]
        self.last_workspace_action = ["" for _ in self.clients]
        self.last_workspace_error = ["" for _ in self.clients]
        self.workspace_poll_count = [0 for _ in self.clients]
        self.workspace_expand_error = ["" for _ in self.clients]
        self.workspace_expand_status_code = [None for _ in self.clients]
        self._current_index = 0

    def get_client(self) -> NotionOpusAPI:
        """
        轮询（Round-Robin）返回下一个可用客户端。
        过滤掉正处于冷却期中的客户端。
        若所有客户端均不可用，将抛出异常。
        """
        now = time.time()
        with self._lock:
            if not self.clients:
                raise RuntimeError("暂无可用账号，请先在后台添加或导入账号。")
            start_index = self._current_index

            while True:
                idx = self._current_index
                # 如果过了冷却时间，视为可用
                if self.cooldown_until[idx] <= now and self.invalid_until[idx] <= now:
                    # 轮询步进
                    self._current_index = (self._current_index + 1) % len(self.clients)
                    return self.clients[idx]

                # 不可用则顺延
                self._current_index = (self._current_index + 1) % len(self.clients)

                # 如果转了一圈都没找到可用的
                if self._current_index == start_index:
                    next_available = (
                        min(self.cooldown_until) if self.cooldown_until else now
                    )
                    wait_seconds = max(1, int(next_available - now))
                    raise RuntimeError(
                        f"Notion 账号限流中（触发官方公平使用政策），请在 {wait_seconds} 秒后重试。"
                    )

    def _find_client_object_index(
        self, client: NotionOpusAPI, raise_on_missing: bool = True
    ) -> int | None:
        for idx, item in enumerate(self.clients):
            if item is client:
                return idx
        if raise_on_missing:
            raise ValueError("Account not found in active pool")
        return None

    def get_status_summary(self) -> Dict[str, int]:
        """返回账号池简要状态，供健康检查和日志使用。"""
        now = time.time()
        with self._lock:
            active = 0
            cooling = 0
            invalid = 0
            for idx in range(len(self.clients)):
                if self.invalid_until[idx] > now:
                    invalid += 1
                elif self.cooldown_until[idx] > now:
                    cooling += 1
                else:
                    active += 1
            return {
                "total": len(self.clients),
                "active": active,
                "cooling": cooling,
                "invalid": invalid,
            }

    def get_detailed_status(self) -> List[Dict[str, Any]]:
        now = time.time()
        with self._lock:
            rows: List[Dict[str, Any]] = []
            for idx, client in enumerate(self.clients):
                oauth_status = client.get_oauth_status()
                has_workspace = self.workspace_count[idx] > 0
                persisted_workspace_state = str(
                    client.status.get("workspace_state")
                    or client.workspace.get("state")
                    or ""
                ).strip()
                if self.invalid_until[idx] > now:
                    state = "invalid"
                elif self.cooldown_until[idx] > now:
                    state = "cooling"
                elif oauth_status.get("expired"):
                    state = "oauth_expired"
                elif oauth_status.get("needs_refresh"):
                    state = "needs_refresh"
                elif persisted_workspace_state == "workspace_creation_pending":
                    state = "workspace_creation_pending"
                elif not has_workspace:
                    state = "no_workspace"
                else:
                    state = "active"
                rows.append(
                    {
                        "account": client.account_key,
                        "account_id": client.account_id,
                        "user_id": client.user_id,
                        "user_email": client.user_email,
                        "enabled": True,
                        "space_id": client.space_id,
                        "state": state,
                        "cooldown_until": int(self.cooldown_until[idx])
                        if self.cooldown_until[idx]
                        else 0,
                        "invalid_until": int(self.invalid_until[idx])
                        if self.invalid_until[idx]
                        else 0,
                        "last_status_code": self.last_status_code[idx],
                        "last_error": self.last_error[idx],
                        "last_success_at": int(self.last_success_at[idx])
                        if self.last_success_at[idx]
                        else 0,
                        "last_refresh_at": int(self.last_refresh_at[idx])
                        if self.last_refresh_at[idx]
                        else 0,
                        "last_refresh_error": self.last_refresh_error[idx],
                        "last_refresh_action": self.last_refresh_action[idx],
                        "reauthorize_required": self.reauthorize_required[idx],
                        "last_workspace_check_at": int(
                            self.last_workspace_check_at[idx]
                        )
                        if self.last_workspace_check_at[idx]
                        else 0,
                        "last_workspace_action": self.last_workspace_action[idx],
                        "last_workspace_error": self.last_workspace_error[idx],
                        "workspace_poll_count": self.workspace_poll_count[idx],
                        "keepalive_failures": self.keepalive_failures[idx],
                        "plan_type": self.plan_types[idx],
                        "subscription_tier": self.subscription_tiers[idx],
                        "workspace_count": self.workspace_count[idx],
                        "workspaces": self.workspaces[idx],
                        "oauth": oauth_status,
                        "usable": state == "active",
                        "needs_reauth": state in {"oauth_expired", "invalid"},
                    }
                )
            return rows

    def probe_accounts(self, background_mode: bool = False) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        for client in self.clients:
            if background_mode and self._should_defer_background_workspace_io(client):
                results.append(
                    self._build_background_deferred_result(client, "probe_deferred")
                )
                continue
            result = client.probe_account()
            self._apply_probe_result(client, result)
            results.append(
                {"account": client.account_key, "space_id": client.space_id, **result}
            )
        return results

    def _apply_probe_result(
        self, client: NotionOpusAPI, result: Dict[str, Any]
    ) -> None:
        now = time.time()
        profile_override: Dict[str, Any] | None = None
        state_for_sync = str(result.get("state", "active") or "active")
        should_attempt_refresh = False
        with self._lock:
            idx = self.clients.index(client)
            self.last_status_code[idx] = result.get("status_code")
            self.last_error[idx] = str(result.get("response_excerpt", "") or "")
            self.workspace_count[idx] = int(result.get("workspace_count") or 0)
            self.workspaces[idx] = result.get("workspaces") or []
            self.plan_types[idx] = str(result.get("plan_type") or "unknown")
            self.subscription_tiers[idx] = str(result.get("subscription_tier") or "")
            cooldown_seconds = int(result.get("cooldown_seconds") or 0)
            if result.get("ok"):
                self.cooldown_until[idx] = 0.0
                self.invalid_until[idx] = 0.0
                self.last_success_at[idx] = now
                self.keepalive_failures[idx] = 0
                state_for_sync = "active"
                profile_override = self._build_profile_override(client, idx)
            else:
                if state_for_sync == "invalid":
                    self.invalid_until[idx] = now + max(1, cooldown_seconds)
                    self.cooldown_until[idx] = 0.0
                    should_attempt_refresh = True
                else:
                    self.cooldown_until[idx] = now + max(1, cooldown_seconds)
                self.keepalive_failures[idx] += 1
        self._sync_account_metadata(
            client,
            idx,
            state=state_for_sync,
            profile_override=profile_override,
        )
        if should_attempt_refresh:
            self._attempt_refresh(client, idx)

    def _build_profile_override(
        self, client: NotionOpusAPI, idx: int
    ) -> Dict[str, Any]:
        current_space = next(
            (
                space
                for space in self.workspaces[idx]
                if space.get("id") == client.space_id
            ),
            None,
        )
        return {
            "account_id": client.account_id,
            "account_key": client.account_key,
            "user_id": client.user_id,
            "user_email": client.user_email,
            "user_name": client.user_name,
            "space_id": client.space_id,
            "space_view_id": client.space_view_id,
            "plan_type": str(
                (current_space or {}).get("plan_type")
                or self.plan_types[idx]
                or "unknown"
            ),
            "subscription_tier": str(
                (current_space or {}).get("subscription_tier")
                or self.subscription_tiers[idx]
                or ""
            ),
            "workspace_count": self.workspace_count[idx],
            "workspaces": list(self.workspaces[idx]),
            "oauth": client.oauth,
            "workspace": client.workspace,
            "status": client.status,
        }

    def mark_failed(self, client: NotionOpusAPI, cooldown_seconds: int = 10):
        """
        标记某个客户端为临时不可用（默认冷却 60 秒后恢复）
        """
        with self._lock:
            try:
                idx = self.clients.index(client)
                # 记录未来的冷却解封时间
                self.cooldown_until[idx] = time.time() + cooldown_seconds
                logger.warning(
                    "Account marked as failed",
                    extra={
                        "request_info": {
                            "event": "account_failed",
                            "account": client.account_key,
                            "space_id": client.space_id,
                            "cooldown_seconds": cooldown_seconds,
                        }
                    },
                )
            except ValueError:
                logger.warning(
                    "Attempted to mark unknown account as failed",
                    extra={"request_info": {"event": "account_failed_unknown"}},
                )

    def mark_upstream_error(
        self, client: NotionOpusAPI, status_code: int | None, response_excerpt: str = ""
    ):
        with self._lock:
            try:
                idx = self.clients.index(client)
            except ValueError:
                logger.warning(
                    "Attempted to mark unknown account upstream error",
                    extra={"request_info": {"event": "account_upstream_error_unknown"}},
                )
                return

        policy = client.classify_status(status_code)
        now = time.time()
        with self._lock:
            self.last_status_code[idx] = status_code
            self.last_error[idx] = response_excerpt[:300]
            if policy["state"] == "invalid":
                self.invalid_until[idx] = now + policy["cooldown_seconds"]
                self.cooldown_until[idx] = 0.0
            else:
                self.cooldown_until[idx] = now + policy["cooldown_seconds"]

        logger.warning(
            "Account marked from upstream status",
            extra={
                "request_info": {
                    "event": "account_upstream_error_marked",
                    "account": client.account_key,
                    "space_id": client.space_id,
                    "status_code": status_code,
                    "state": policy["state"],
                    "cooldown_seconds": policy["cooldown_seconds"],
                }
            },
        )

    def keepalive_accounts(self, background_mode: bool = False) -> List[Dict[str, Any]]:
        results = self.probe_accounts(background_mode=background_mode)
        return results

    def _find_client_index(self, account_id: str) -> int:
        target_id = str(account_id or "").strip()
        for idx, client in enumerate(self.clients):
            if str(client.account_id or "").strip() == target_id:
                return idx
        raise ValueError("Account not found in active pool")

    def probe_account_by_id(self, account_id: str) -> Dict[str, Any]:
        idx = self._find_client_index(account_id)
        client = self.clients[idx]
        result = client.probe_account()
        self._apply_probe_result(client, result)
        return {
            "account": client.account_key,
            "account_id": client.account_id,
            "space_id": client.space_id,
            **result,
        }

    def refresh_account_by_id(self, account_id: str) -> Dict[str, Any]:
        idx = self._find_client_index(account_id)
        client = self.clients[idx]
        refresh_result = client.try_refresh_session()
        with self._lock:
            self.last_refresh_at[idx] = time.time()
            self.last_refresh_error[idx] = (
                ""
                if bool(refresh_result.get("ok", False))
                else str(refresh_result.get("reason") or "")
            )
            self.last_refresh_action[idx] = str(refresh_result.get("action") or "")
            self.reauthorize_required[idx] = bool(
                refresh_result.get("reauthorize_required", False)
            )
            if refresh_result.get("ok"):
                self.reauthorize_required[idx] = False
        self._sync_account_metadata(
            client,
            idx,
            state="active" if refresh_result.get("ok") else "invalid",
        )
        return {
            "account": client.account_key,
            "account_id": client.account_id,
            "space_id": client.space_id,
            **refresh_result,
        }

    def sync_workspace_by_id(self, account_id: str) -> Dict[str, Any]:
        idx = self._find_client_index(account_id)
        client = self.clients[idx]
        spaces = client.list_spaces(
            allow_direct_fallback=client._should_allow_direct_workspace_fallback()
        )
        client.sync_workspace_context(spaces)
        self.workspace_count[idx] = len(spaces)
        self.workspaces[idx] = spaces
        self.last_workspace_check_at[idx] = time.time()
        self.workspace_poll_count[idx] += 1
        self.last_workspace_error[idx] = ""
        self.last_workspace_action[idx] = "sync_workspace"
        current_space = next(
            (space for space in spaces if space.get("id") == client.space_id), None
        )
        self.plan_types[idx] = str(
            (current_space or {}).get("plan_type") or self.plan_types[idx] or "unknown"
        )
        self.subscription_tiers[idx] = str(
            (current_space or {}).get("subscription_tier")
            or self.subscription_tiers[idx]
            or ""
        )
        self._sync_account_metadata(
            client,
            idx,
            state="active",
            profile_override=self._build_profile_override(client, idx),
        )
        return {
            "account": client.account_key,
            "account_id": client.account_id,
            "space_id": client.space_id,
            "workspace_count": len(spaces),
            "workspaces": spaces,
            "ok": True,
        }

    def create_workspace_by_id(self, account_id: str) -> Dict[str, Any]:
        idx = self._find_client_index(account_id)
        client = self.clients[idx]
        result = client.maybe_create_workspace()
        self.last_workspace_check_at[idx] = time.time()
        self.workspace_poll_count[idx] += 1
        self.last_workspace_action[idx] = str(
            result.get("action") or result.get("state") or "workspace_check"
        )
        self.last_workspace_error[idx] = (
            "" if bool(result.get("ok", False)) else str(result.get("reason") or "")
        )
        if result.get("ok"):
            self.reauthorize_required[idx] = False
        if result.get("created") and not result.get("workspaces"):
            spaces = client.list_spaces()
            client.sync_workspace_context(spaces)
            self.workspace_count[idx] = len(spaces)
            self.workspaces[idx] = spaces
        elif isinstance(result.get("workspaces"), list):
            client.sync_workspace_context(result.get("workspaces") or [])
            self.workspace_count[idx] = len(result.get("workspaces") or [])
            self.workspaces[idx] = result.get("workspaces") or []
        self._sync_account_metadata(
            client,
            idx,
            state=str(
                result.get("state")
                or (
                    "active"
                    if result.get("ok")
                    and not client.get_oauth_status().get("expired", False)
                    else "cooling"
                )
            ),
            profile_override=self._build_profile_override(client, idx),
        )
        return {
            "account": client.account_key,
            "account_id": client.account_id,
            "space_id": client.space_id,
            **result,
        }

    def refresh_account_sessions(self) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        for idx, client in enumerate(self.clients):
            refresh_result = client.try_refresh_session()
            with self._lock:
                self.last_refresh_at[idx] = time.time()
                self.last_refresh_error[idx] = (
                    ""
                    if refresh_result.get("ok")
                    else str(refresh_result.get("reason") or "")
                )
                self.last_refresh_action[idx] = str(refresh_result.get("action") or "")
                self.reauthorize_required[idx] = bool(
                    refresh_result.get("reauthorize_required", False)
                )
                if refresh_result.get("ok"):
                    self.reauthorize_required[idx] = False
            self._sync_account_metadata(
                client,
                idx,
                state="active" if refresh_result.get("ok") else "invalid",
            )
            results.append(
                {
                    "account": client.account_key,
                    "space_id": client.space_id,
                    **refresh_result,
                }
            )
        return results

    def sync_workspaces(self, background_mode: bool = False) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        for idx, client in enumerate(self.clients):
            if background_mode and self._should_defer_background_workspace_io(client):
                self.last_workspace_check_at[idx] = time.time()
                self.last_workspace_action[idx] = "sync_workspace_deferred"
                self.last_workspace_error[idx] = "background_hydration_guard"
                results.append(
                    self._build_background_deferred_result(
                        client, "sync_workspace_deferred"
                    )
                )
                continue
            try:
                spaces = client.list_spaces()
                client.sync_workspace_context(spaces)
                self.workspace_count[idx] = len(spaces)
                self.workspaces[idx] = spaces
                self.last_workspace_check_at[idx] = time.time()
                self.workspace_poll_count[idx] += 1
                self.last_workspace_error[idx] = ""
                self.last_workspace_action[idx] = "sync_workspace"
                current_space = next(
                    (space for space in spaces if space.get("id") == client.space_id),
                    None,
                )
                self.plan_types[idx] = str(
                    (current_space or {}).get("plan_type")
                    or self.plan_types[idx]
                    or "unknown"
                )
                self.subscription_tiers[idx] = str(
                    (current_space or {}).get("subscription_tier")
                    or self.subscription_tiers[idx]
                    or ""
                )
                self._sync_account_metadata(client, idx, state="active")
                results.append(
                    {
                        "account": client.account_key,
                        "space_id": client.space_id,
                        "workspace_count": len(spaces),
                        "workspaces": spaces,
                        "ok": True,
                    }
                )
            except Exception as exc:
                self.last_workspace_check_at[idx] = time.time()
                self.workspace_poll_count[idx] += 1
                self.last_workspace_error[idx] = str(exc)[:300]
                self.last_workspace_action[idx] = "sync_workspace_failed"
                results.append(
                    {
                        "account": client.account_key,
                        "space_id": client.space_id,
                        "ok": False,
                        "error": str(exc)[:300],
                    }
                )
        return results

    def create_missing_workspaces(self) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        for idx, client in enumerate(self.clients):
            try:
                result = client.maybe_create_workspace()
                self.last_workspace_check_at[idx] = time.time()
                self.workspace_poll_count[idx] += 1
                self.last_workspace_action[idx] = str(
                    result.get("action") or result.get("state") or "workspace_check"
                )
                self.last_workspace_error[idx] = (
                    "" if result.get("ok") else str(result.get("reason") or "")
                )
                if result.get("ok"):
                    self.reauthorize_required[idx] = False
                if result.get("created"):
                    spaces = result.get("workspaces")
                    if isinstance(spaces, list) and spaces:
                        client.sync_workspace_context(spaces)
                        self.workspace_count[idx] = len(spaces)
                        self.workspaces[idx] = spaces
                    else:
                        spaces = client.list_spaces()
                        client.sync_workspace_context(spaces)
                        self.workspace_count[idx] = len(spaces)
                        self.workspaces[idx] = spaces
                self._sync_account_metadata(
                    client,
                    idx,
                    state=str(
                        result.get("state")
                        or ("active" if result.get("ok") else "cooling")
                    ),
                    profile_override=self._build_profile_override(client, idx),
                )
                results.append(
                    {
                        "account": client.account_key,
                        "space_id": client.space_id,
                        **result,
                    }
                )
            except Exception as exc:
                self.last_workspace_check_at[idx] = time.time()
                self.workspace_poll_count[idx] += 1
                self.last_workspace_error[idx] = str(exc)[:300]
                self.last_workspace_action[idx] = "workspace_create_failed"
                results.append(
                    {
                        "account": client.account_key,
                        "space_id": client.space_id,
                        "ok": False,
                        "created": False,
                        "error": str(exc)[:300],
                    }
                )
        return results

    def _attempt_refresh(self, client: NotionOpusAPI, idx: int) -> None:
        refresh_result = client.try_refresh_session()
        with self._lock:
            self.last_refresh_at[idx] = time.time()
            self.last_refresh_error[idx] = (
                ""
                if bool(refresh_result.get("ok", False))
                else str(refresh_result.get("reason") or "")
            )
            self.last_refresh_action[idx] = str(refresh_result.get("action") or "")
            self.reauthorize_required[idx] = bool(
                refresh_result.get("reauthorize_required", False)
            )
        self._sync_account_metadata(
            client,
            idx,
            state="active" if refresh_result.get("ok") else "invalid",
        )

    def _sync_account_metadata(
        self,
        client: NotionOpusAPI,
        idx: int,
        state: str | None = None,
        profile_override: Dict[str, Any] | None = None,
    ) -> None:
        if isinstance(profile_override, dict):
            profile = profile_override
        else:
            try:
                profile = client.get_account_profile()
            except Exception:
                profile = {
                    "workspace_count": self.workspace_count[idx],
                    "workspaces": self.workspaces[idx],
                    "subscription_tier": self.subscription_tiers[idx],
                    "plan_type": self.plan_types[idx],
                }

        store = get_config_store()
        accounts = store.get_accounts()
        updated = False
        for account in accounts:
            if account.get("user_id") != client.user_id:
                continue
            oauth_status = client.get_oauth_status()
            existing_status = (
                account.get("status") if isinstance(account.get("status"), dict) else {}
            )
            account["space_id"] = client.space_id
            account["space_view_id"] = client.space_view_id
            account["plan_type"] = profile.get(
                "plan_type", account.get("plan_type", "unknown")
            )
            account["workspace"] = {
                "workspace_count": profile.get("workspace_count", 0),
                "workspaces": profile.get("workspaces", []),
                "subscription_tier": profile.get("subscription_tier", ""),
                "state": "ready"
                if profile.get("workspace_count", 0) > 0
                else "missing",
            }
            if state in {
                "workspace_creation_pending",
                "workspace_creation_unimplemented",
                "workspace_creation_unverified",
            }:
                account["workspace"]["state"] = state
            elif (
                bool(existing_status.get("workspace_hydration_pending", False))
                and str(client.space_id or "").startswith("pending-signup-")
                and profile.get("workspace_count", 0) <= 0
            ):
                account["workspace"]["state"] = "workspace_creation_pending"
            account["status"] = {
                "state": state
                or ("invalid" if self.invalid_until[idx] > time.time() else "active"),
                "last_success_at": int(self.last_success_at[idx])
                if self.last_success_at[idx]
                else 0,
                "last_refresh_at": int(self.last_refresh_at[idx])
                if self.last_refresh_at[idx]
                else 0,
                "last_refresh_error": self.last_refresh_error[idx],
                "last_refresh_action": self.last_refresh_action[idx],
                "keepalive_failures": self.keepalive_failures[idx],
                "last_error": self.last_error[idx],
                "last_status_code": self.last_status_code[idx],
                "oauth_expired": oauth_status.get("expired", False),
                "oauth_expires_at": oauth_status.get("expires_at"),
                "needs_refresh": oauth_status.get("needs_refresh", False),
                "usable": bool(
                    account.get("enabled", True)
                    and not oauth_status.get("expired", False)
                    and not oauth_status.get("needs_refresh", False)
                    and profile.get("workspace_count", 0) > 0
                    and (state or "active") == "active"
                ),
                "no_workspace": profile.get("workspace_count", 0) <= 0,
                "needs_reauth": bool(
                    oauth_status.get("expired", False)
                    or (state or "") == "invalid"
                    or self.reauthorize_required[idx]
                ),
                "workspace_state": account["workspace"].get("state", "missing"),
                "reauthorize_required": self.reauthorize_required[idx],
                "last_refresh_action": self.last_refresh_action[idx],
                "last_workspace_check_at": int(self.last_workspace_check_at[idx])
                if self.last_workspace_check_at[idx]
                else 0,
                "last_workspace_action": self.last_workspace_action[idx],
                "last_workspace_error": self.last_workspace_error[idx],
                "workspace_poll_count": self.workspace_poll_count[idx],
                "workspace_expand_error": self.workspace_expand_error[idx],
                "workspace_expand_status_code": self.workspace_expand_status_code[idx],
            }
            if bool(existing_status.get("workspace_hydration_pending", False)):
                account["status"]["workspace_hydration_pending"] = bool(
                    existing_status.get("workspace_hydration_pending", False)
                )
                account["status"]["workspace_hydration_retry_after"] = int(
                    existing_status.get("workspace_hydration_retry_after") or 0
                )
                account["status"]["workspace_hydration_backoff_seconds"] = int(
                    existing_status.get("workspace_hydration_backoff_seconds") or 0
                )
                if str(
                    existing_status.get("workspace_hydration_retry_policy") or ""
                ).strip():
                    account["status"]["workspace_hydration_retry_policy"] = str(
                        existing_status.get("workspace_hydration_retry_policy") or ""
                    ).strip()
                if str(
                    existing_status.get("last_workspace_failure_category") or ""
                ).strip():
                    account["status"]["last_workspace_failure_category"] = str(
                        existing_status.get("last_workspace_failure_category") or ""
                    ).strip()
            account["oauth"] = {**account.get("oauth", {}), **oauth_status}
            account["updated_at"] = int(time.time())
            updated = True
            break

        if updated:
            store.set_accounts(accounts)

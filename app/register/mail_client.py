import re
import time
import random
from abc import ABC, abstractmethod
from typing import Callable, Optional
from datetime import datetime, timedelta

import requests

from app.logger import logger
from app.config import (
    get_runtime_config,
    get_upstream_http_proxy,
    get_upstream_https_proxy,
    get_upstream_proxy,
    get_upstream_proxy_mode,
    get_upstream_socks5_proxy,
    get_upstream_warp_enabled,
    get_upstream_warp_proxy,
)


def build_proxy_dict(
    proxy: str = "", *, http: str = "", https: str = "", socks5: str = ""
) -> Optional[dict]:
    proxies = {}
    base_proxy = str(proxy or "").strip()
    http_proxy = str(http or "").strip() or base_proxy
    https_proxy = str(https or "").strip() or base_proxy
    socks5_proxy = str(socks5 or "").strip()
    if socks5_proxy:
        proxies["http"] = socks5_proxy
        proxies["https"] = socks5_proxy
        return proxies
    if http_proxy:
        proxies["http"] = http_proxy
    if https_proxy:
        proxies["https"] = https_proxy
    return proxies or None


def build_runtime_proxy_dict(explicit_proxy: str = "") -> Optional[dict]:
    explicit = str(explicit_proxy or "").strip()
    if explicit:
        return build_proxy_dict(proxy=explicit)
    proxy_mode = get_upstream_proxy_mode().strip().lower()
    warp_proxy = get_upstream_warp_proxy().strip()
    socks5_proxy = get_upstream_socks5_proxy().strip()
    if proxy_mode == "direct":
        return None
    if proxy_mode == "warp":
        if not get_upstream_warp_enabled() or not warp_proxy:
            return None
        return build_proxy_dict(socks5=warp_proxy)
    if proxy_mode == "socks5":
        return build_proxy_dict(socks5=socks5_proxy)
    return build_proxy_dict(
        proxy=get_upstream_proxy(),
        http=get_upstream_http_proxy(),
        https=get_upstream_https_proxy(),
        socks5=socks5_proxy if proxy_mode == "mixed" else "",
    )


def is_runtime_proxy_active() -> bool:
    proxy_mode = get_upstream_proxy_mode().strip().lower()
    if proxy_mode == "direct":
        return False
    if proxy_mode == "warp":
        return bool(get_upstream_warp_enabled() and get_upstream_warp_proxy().strip())
    if proxy_mode == "socks5":
        return bool(get_upstream_socks5_proxy().strip())
    if proxy_mode in {"http", "https", "mixed"}:
        return bool(
            get_upstream_proxy().strip()
            or get_upstream_http_proxy().strip()
            or get_upstream_https_proxy().strip()
            or get_upstream_socks5_proxy().strip()
        )
    return False


def _parse_mail_timestamp(raw: object) -> Optional[datetime]:
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    candidates = [text, text.replace("Z", "+00:00")]
    for candidate in candidates:
        try:
            return datetime.fromisoformat(candidate)
        except Exception:
            pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(text, fmt)
        except Exception:
            pass
    return None


class TempMailClient(ABC):
    def __init__(
        self,
        base_url: str = "",
        proxy: str = "",
        log_callback: Optional[Callable[[str, str], None]] = None,
    ):
        self.base_url = base_url.rstrip("/") if base_url else ""
        self.proxy = proxy
        self.log_callback = log_callback
        self.email = ""
        self.password = ""
        self.email_id = ""

    def _log(self, level: str, message: str) -> None:
        if self.log_callback:
            self.log_callback(level, message)
        else:
            getattr(logger, level, logger.info)(message)

    def _get_proxies(self) -> Optional[dict]:
        if self.proxy:
            return build_runtime_proxy_dict(self.proxy)
        if not is_runtime_proxy_active():
            return None
        return build_runtime_proxy_dict()

    @abstractmethod
    def register_account(self, domain: Optional[str] = None) -> bool:
        pass

    @abstractmethod
    def poll_for_code(
        self,
        timeout: int = 120,
        interval: int = 5,
        since_time: Optional[datetime] = None,
    ) -> Optional[str]:
        pass

    def extract_code(self, text: str) -> Optional[str]:
        patterns = [
            r"(?<![0-9])(\d{6})(?![0-9])",
            r"(?<![0-9])(\d{5})(?![0-9])",
            r"(?<![0-9])(\d{4})(?![0-9])",
            r"验证码[：:\s]*(\d{4,6})",
            r"code[：:\s]*(\d{4,6})",
            r"Code[：:\s]*(\d{4,6})",
            r"CODE[：:\s]*(\d{4,6})",
            r"verification code[：:\s]*(\d{4,6})",
            r"Verification Code[：:\s]*(\d{4,6})",
        ]
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return match.group(1)
        return None


class MoemailClient(TempMailClient):
    def __init__(
        self,
        base_url: str = "https://moemail.app",
        api_key: str = "",
        proxy: str = "",
        domain: Optional[str] = None,
        log_callback: Optional[Callable[[str, str], None]] = None,
    ):
        super().__init__(base_url=base_url, proxy=proxy, log_callback=log_callback)
        self.api_key = api_key
        self.domain = domain or ""
        self.jwt_token = ""

    def register_account(self, domain: Optional[str] = None) -> bool:
        try:
            self._log("info", "正在注册 Moemail 临时邮箱...")
            url = f"{self.base_url}/api/mail/generate"
            headers = {"Content-Type": "application/json"}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"
            payload = {}
            if domain or self.domain:
                payload["domain"] = domain or self.domain
            resp = requests.post(
                url,
                json=payload,
                headers=headers,
                proxies=self._get_proxies(),
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            self.email = data.get("email", "")
            self.jwt_token = data.get("token", "")
            self.email_id = data.get("id", "")
            self.password = self.email_id
            if not self.email:
                self._log("error", "Moemail 响应中未找到邮箱地址")
                return False
            self._log("info", f"Moemail 邮箱注册成功: {self.email}")
            return True
        except Exception as e:
            self._log("error", f"Moemail 注册失败: {e}")
            return False

    def poll_for_code(
        self,
        timeout: int = 120,
        interval: int = 5,
        since_time: Optional[datetime] = None,
    ) -> Optional[str]:
        if not self.email:
            return None
        self._log("info", f"开始轮询邮箱: {self.email}")
        start_time = time.time()
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        if self.jwt_token:
            headers["Authorization"] = f"Bearer {self.jwt_token}"
        while time.time() - start_time < timeout:
            try:
                url = f"{self.base_url}/api/mail/inbox"
                params = {"email": self.email}
                resp = requests.get(
                    url,
                    params=params,
                    headers=headers,
                    proxies=self._get_proxies(),
                    timeout=30,
                )
                resp.raise_for_status()
                data = resp.json()
                messages = data.get("messages", []) or data.get("emails", []) or data
                if isinstance(messages, list) and messages:
                    for msg in reversed(messages):
                        if since_time:
                            msg_time_str = (
                                msg.get("time")
                                or msg.get("date")
                                or msg.get("receivedAt")
                            )
                            if msg_time_str:
                                try:
                                    msg_time = datetime.fromisoformat(
                                        msg_time_str.replace("Z", "+00:00")
                                    )
                                    if msg_time < since_time:
                                        continue
                                except Exception:
                                    pass
                        body = (
                            msg.get("body")
                            or msg.get("text")
                            or msg.get("content")
                            or msg.get("html", "")
                        )
                        subject = msg.get("subject", "")
                        full_text = f"{subject} {body}"
                        code = self.extract_code(full_text)
                        if code:
                            self._log("info", f"找到验证码: {code}")
                            return code
            except Exception as e:
                self._log("warning", f"轮询邮箱异常: {e}")
            time.sleep(interval)
        self._log("warning", "验证码轮询超时")
        return None


class DuckMailClient(TempMailClient):
    def __init__(
        self,
        base_url: str = "https://duckmail.sbs",
        api_key: str = "",
        proxy: str = "",
        verify_ssl: bool = True,
        log_callback: Optional[Callable[[str, str], None]] = None,
    ):
        super().__init__(base_url=base_url, proxy=proxy, log_callback=log_callback)
        self.api_key = api_key
        self.verify_ssl = verify_ssl

    def register_account(self, domain: Optional[str] = None) -> bool:
        try:
            self._log("info", "正在注册 DuckMail 临时邮箱...")
            url = f"{self.base_url}/api/auth/register"
            headers = {"Content-Type": "application/json"}
            if self.api_key:
                headers["X-API-Key"] = self.api_key
            payload = {}
            if domain:
                payload["domain"] = domain
            resp = requests.post(
                url,
                json=payload,
                headers=headers,
                proxies=self._get_proxies(),
                timeout=30,
                verify=self.verify_ssl,
            )
            resp.raise_for_status()
            data = resp.json()
            self.email = data.get("email", "")
            self.password = data.get("password", "")
            if not self.email:
                self._log("error", "DuckMail 响应中未找到邮箱地址")
                return False
            self._log("info", f"DuckMail 邮箱注册成功: {self.email}")
            return True
        except Exception as e:
            self._log("error", f"DuckMail 注册失败: {e}")
            return False

    def poll_for_code(
        self,
        timeout: int = 120,
        interval: int = 5,
        since_time: Optional[datetime] = None,
    ) -> Optional[str]:
        if not self.email:
            return None
        self._log("info", f"开始轮询邮箱: {self.email}")
        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                url = f"{self.base_url}/api/mail/inbox"
                headers = {"Content-Type": "application/json"}
                if self.api_key:
                    headers["X-API-Key"] = self.api_key
                params = {"email": self.email}
                resp = requests.get(
                    url,
                    params=params,
                    headers=headers,
                    proxies=self._get_proxies(),
                    timeout=30,
                    verify=self.verify_ssl,
                )
                resp.raise_for_status()
                data = resp.json()
                messages = data.get("messages", []) or data.get("emails", []) or data
                if isinstance(messages, list) and messages:
                    for msg in reversed(messages):
                        if since_time:
                            msg_time_str = (
                                msg.get("time")
                                or msg.get("date")
                                or msg.get("receivedAt")
                            )
                            if msg_time_str:
                                try:
                                    msg_time = datetime.fromisoformat(
                                        msg_time_str.replace("Z", "+00:00")
                                    )
                                    if msg_time < since_time:
                                        continue
                                except Exception:
                                    pass
                        body = (
                            msg.get("body")
                            or msg.get("text")
                            or msg.get("content")
                            or msg.get("html", "")
                        )
                        subject = msg.get("subject", "")
                        full_text = f"{subject} {body}"
                        code = self.extract_code(full_text)
                        if code:
                            self._log("info", f"找到验证码: {code}")
                            return code
            except Exception as e:
                self._log("warning", f"轮询邮箱异常: {e}")
            time.sleep(interval)
        self._log("warning", "验证码轮询超时")
        return None


class GPTMailClient(TempMailClient):
    def __init__(
        self,
        base_url: str = "https://mail.chatgpt.org.uk",
        api_key: str = "gpt-test",
        proxy: str = "",
        verify_ssl: bool = True,
        domain: Optional[str] = None,
        log_callback: Optional[Callable[[str, str], None]] = None,
    ):
        super().__init__(base_url=base_url, proxy=proxy, log_callback=log_callback)
        self.api_key = api_key
        self.verify_ssl = verify_ssl
        self.domain = domain or ""

    def register_account(self, domain: Optional[str] = None) -> bool:
        try:
            self._log("info", "正在注册 GPTMail 临时邮箱...")
            url = f"{self.base_url}/api/auth/register"
            headers = {"Content-Type": "application/json"}
            if self.api_key:
                headers["X-API-Key"] = self.api_key
            payload = {}
            if domain or self.domain:
                payload["domain"] = domain or self.domain
            resp = requests.post(
                url,
                json=payload,
                headers=headers,
                proxies=self._get_proxies(),
                timeout=30,
                verify=self.verify_ssl,
            )
            resp.raise_for_status()
            data = resp.json()
            self.email = data.get("email", "")
            self.password = data.get("password", "")
            if not self.email:
                self._log("error", "GPTMail 响应中未找到邮箱地址")
                return False
            self._log("info", f"GPTMail 邮箱注册成功: {self.email}")
            return True
        except Exception as e:
            self._log("error", f"GPTMail 注册失败: {e}")
            return False

    def poll_for_code(
        self,
        timeout: int = 120,
        interval: int = 5,
        since_time: Optional[datetime] = None,
    ) -> Optional[str]:
        if not self.email:
            return None
        self._log("info", f"开始轮询邮箱: {self.email}")
        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                url = f"{self.base_url}/api/mail/inbox"
                headers = {"Content-Type": "application/json"}
                if self.api_key:
                    headers["X-API-Key"] = self.api_key
                params = {"email": self.email}
                resp = requests.get(
                    url,
                    params=params,
                    headers=headers,
                    proxies=self._get_proxies(),
                    timeout=30,
                    verify=self.verify_ssl,
                )
                resp.raise_for_status()
                data = resp.json()
                messages = data.get("messages", []) or data.get("emails", []) or data
                if isinstance(messages, list) and messages:
                    for msg in reversed(messages):
                        if since_time:
                            msg_time_str = (
                                msg.get("time")
                                or msg.get("date")
                                or msg.get("receivedAt")
                            )
                            if msg_time_str:
                                try:
                                    msg_time = datetime.fromisoformat(
                                        msg_time_str.replace("Z", "+00:00")
                                    )
                                    if msg_time < since_time:
                                        continue
                                except Exception:
                                    pass
                        body = (
                            msg.get("body")
                            or msg.get("text")
                            or msg.get("content")
                            or msg.get("html", "")
                        )
                        subject = msg.get("subject", "")
                        full_text = f"{subject} {body}"
                        code = self.extract_code(full_text)
                        if code:
                            self._log("info", f"找到验证码: {code}")
                            return code
            except Exception as e:
                self._log("warning", f"轮询邮箱异常: {e}")
            time.sleep(interval)
        self._log("warning", "验证码轮询超时")
        return None


class FreemailClient(TempMailClient):
    def __init__(
        self,
        base_url: str = "http://your-freemail-server.com",
        api_key: str = "",
        proxy: str = "",
        verify_ssl: bool = True,
        domain: Optional[str] = None,
        log_callback: Optional[Callable[[str, str], None]] = None,
    ):
        super().__init__(base_url=base_url, proxy=proxy, log_callback=log_callback)
        self.api_key = api_key.strip()
        self.verify_ssl = verify_ssl
        self.domain = domain or ""

    def register_account(self, domain: Optional[str] = None) -> bool:
        try:
            self._log("info", "正在注册 Freemail 临时邮箱...")
            params = {"admin_token": self.api_key}
            if domain or self.domain:
                params["domain"] = domain or self.domain
            resp = requests.post(
                f"{self.base_url}/api/generate",
                params=params,
                proxies=self._get_proxies(),
                timeout=30,
                verify=self.verify_ssl,
            )
            resp.raise_for_status()
            data = resp.json()
            self.email = data.get("email") or data.get("mailbox", "")
            if not self.email:
                self._log("error", "Freemail 响应中未找到邮箱地址")
                return False
            self.password = ""
            self._log("info", f"Freemail 邮箱注册成功: {self.email}")
            return True
        except Exception as e:
            self._log("error", f"Freemail 注册失败: {e}")
            return False

    def poll_for_code(
        self,
        timeout: int = 120,
        interval: int = 5,
        since_time: Optional[datetime] = None,
    ) -> Optional[str]:
        if not self.email:
            return None
        self._log("info", f"开始轮询邮箱: {self.email}")
        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                resp = requests.get(
                    f"{self.base_url}/api/emails",
                    params={"mailbox": self.email, "admin_token": self.api_key},
                    proxies=self._get_proxies(),
                    timeout=30,
                    verify=self.verify_ssl,
                )
                resp.raise_for_status()
                messages = resp.json()
                if isinstance(messages, list) and messages:
                    filtered_messages = []
                    effective_since = since_time
                    notion_poll_start = getattr(self, "notion_poll_start", None)
                    if effective_since is None and notion_poll_start:
                        try:
                            effective_since = datetime.fromtimestamp(
                                float(notion_poll_start)
                            )
                        except Exception:
                            effective_since = None
                    for msg in messages:
                        msg_time = _parse_mail_timestamp(
                            msg.get("created_at")
                            or msg.get("createdAt")
                            or msg.get("receivedAt")
                            or msg.get("received_at")
                        )
                        if effective_since and msg_time:
                            comparable = msg_time.replace(tzinfo=None)
                            if comparable < effective_since:
                                continue
                        filtered_messages.append((msg_time, msg))
                    ordered_messages = [
                        msg
                        for _, msg in sorted(
                            filtered_messages,
                            key=lambda item: item[0] or datetime.min,
                            reverse=True,
                        )
                    ] or list(reversed(messages))
                    for msg in ordered_messages:
                        body = (
                            msg.get("body")
                            or msg.get("content")
                            or msg.get("html_content")
                            or ""
                        )
                        subject = msg.get("subject", "")
                        preview = str(msg.get("preview") or "")
                        verification_code = str(
                            msg.get("verification_code") or ""
                        ).strip()
                        full_text = f"{subject} {preview} {body}"
                        code = self.extract_code(full_text)
                        if not code and verification_code:
                            code = verification_code
                        if code:
                            self._log("info", f"找到验证码: {code}")
                            return code
                        message_id = msg.get("id")
                        if not message_id:
                            continue
                        detail_resp = requests.get(
                            f"{self.base_url}/api/email/{message_id}",
                            params={"admin_token": self.api_key},
                            proxies=self._get_proxies(),
                            timeout=30,
                            verify=self.verify_ssl,
                        )
                        detail_resp.raise_for_status()
                        detail = detail_resp.json()
                        detail_text = " ".join(
                            [
                                str(detail.get("subject") or ""),
                                str(detail.get("content") or ""),
                                str(detail.get("html_content") or ""),
                                str(detail.get("preview") or ""),
                            ]
                        )
                        code = self.extract_code(detail_text)
                        if not code:
                            detail_code = str(
                                detail.get("verification_code")
                                or detail.get("code")
                                or ""
                            ).strip()
                            if detail_code:
                                code = detail_code
                        if code:
                            self._log("info", f"找到验证码: {code}")
                            return code
            except Exception as e:
                self._log("warning", f"轮询邮箱异常: {e}")
            time.sleep(interval)
        self._log("warning", "验证码轮询超时")
        return None


def create_temp_mail_client(
    provider: str = "moemail",
    base_url: str = "",
    api_key: str = "",
    proxy: str = "",
    domain: Optional[str] = None,
    verify_ssl: bool = True,
    log_callback: Optional[Callable[[str, str], None]] = None,
) -> TempMailClient:
    provider = (provider or "moemail").lower()
    if provider == "duckmail":
        return DuckMailClient(
            base_url=base_url or "https://duckmail.sbs",
            api_key=api_key,
            proxy=proxy,
            verify_ssl=verify_ssl,
            log_callback=log_callback,
        )
    elif provider == "gptmail":
        return GPTMailClient(
            base_url=base_url or "https://mail.chatgpt.org.uk",
            api_key=api_key or "gpt-test",
            proxy=proxy,
            verify_ssl=verify_ssl,
            domain=domain,
            log_callback=log_callback,
        )
    elif provider == "freemail":
        return FreemailClient(
            base_url=base_url or "http://your-freemail-server.com",
            api_key=api_key,
            proxy=proxy,
            verify_ssl=verify_ssl,
            domain=domain,
            log_callback=log_callback,
        )
    else:
        return MoemailClient(
            base_url=base_url or "https://moemail.app",
            api_key=api_key,
            proxy=proxy,
            domain=domain,
            log_callback=log_callback,
        )

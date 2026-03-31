import os
import json
import time
import random
import string
import uuid
import platform
from typing import Any, Callable, Dict, List, Optional
from datetime import datetime, timedelta
from urllib.parse import quote

import requests

from app.logger import logger
from app.register.mail_client import TempMailClient, build_proxy_dict

try:
    from DrissionPage import ChromiumPage, ChromiumOptions

    DRISSION_AVAILABLE = True
except ImportError:
    DRISSION_AVAILABLE = False

NOTION_SIGNUP_URL = "https://www.notion.so/signup"
NOTION_LOGIN_URL = "https://www.notion.so/login"
NOTION_API_SIGNUP = "https://www.notion.so/api/v3/createUser"
NOTION_API_SEND_EMAIL_CODE = "https://www.notion.so/api/v3/sendSignUpEmailCode"
NOTION_API_VERIFY_EMAIL_CODE = "https://www.notion.so/api/v3/verifySignUpEmailCode"
NOTION_API_LOAD_USER_CONTENT = "https://www.notion.so/api/v3/loadUserContent"
NOTION_API_GET_SELF = "https://www.notion.so/api/v3/getUserAnalyticsSettings"
DEFAULT_REGISTER_DISPLAY_NAME = "zhatianbang66fasdgewfas"

CHROMIUM_PATHS = [
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
    "/usr/bin/google-chrome",
    "/usr/bin/google-chrome-stable",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
    "C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe",
]

REGISTER_NAMES = [
    "James Smith",
    "John Johnson",
    "Robert Williams",
    "Michael Brown",
    "William Jones",
    "David Garcia",
    "Mary Miller",
    "Patricia Davis",
    "Jennifer Rodriguez",
    "Linda Martinez",
    "Barbara Anderson",
    "Susan Thomas",
    "Jessica Jackson",
    "Sarah White",
    "Karen Harris",
    "Lisa Martin",
    "Nancy Thompson",
    "Betty Garcia",
    "Margaret Martinez",
    "Sandra Robinson",
]

COMMON_VIEWPORTS = [
    (1366, 768),
    (1440, 900),
    (1536, 864),
    (1280, 720),
    (1920, 1080),
    (1600, 900),
    (1280, 800),
    (1360, 768),
]


def find_chromium_path() -> Optional[str]:
    for path in CHROMIUM_PATHS:
        if os.path.isfile(path) or os.path.exists(path):
            return path
    return None


class NotionRegisterResult:
    def __init__(
        self,
        success: bool = False,
        error: str = "",
        token_v2: str = "",
        user_id: str = "",
        space_id: str = "",
        space_view_id: str = "",
        email: str = "",
        register_method: str = "",
        attempted_api: bool = False,
        used_browser_fallback: bool = False,
        workspace_count: int = 0,
    ):
        self.success = success
        self.error = error
        self.token_v2 = token_v2
        self.user_id = user_id
        self.space_id = space_id
        self.space_view_id = space_view_id
        self.email = email
        self.register_method = register_method
        self.attempted_api = attempted_api
        self.used_browser_fallback = used_browser_fallback
        self.workspace_count = workspace_count

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "error": self.error,
            "token_v2": self.token_v2,
            "user_id": self.user_id,
            "space_id": self.space_id,
            "space_view_id": self.space_view_id,
            "email": self.email,
            "register_method": self.register_method,
            "attempted_api": self.attempted_api,
            "used_browser_fallback": self.used_browser_fallback,
            "workspace_count": self.workspace_count,
        }


class NotionRegisterService:
    def __init__(
        self,
        proxy: str = "",
        headless: bool = True,
        timeout: int = 120,
        log_callback: Optional[Callable[[str, str], None]] = None,
    ):
        self.proxy = proxy
        self.headless = headless
        self.timeout = timeout
        self.log_callback = log_callback
        self._page = None

    def _write_debug_artifacts(self, page, email: str, tag: str) -> None:
        try:
            debug_dir = os.path.join(
                os.path.dirname(__file__), "..", "..", "data", "register_debug"
            )
            os.makedirs(debug_dir, exist_ok=True)
            safe_email = email.replace("@", "_at_").replace(".", "_")
            html_path = os.path.join(debug_dir, f"{safe_email}_{tag}.html")
            json_path = os.path.join(debug_dir, f"{safe_email}_{tag}.json")
            html = page.html or ""
            state = self._extract_json_state(page)
            cookies = page.cookies()
            current_url = ""
            title = ""
            try:
                current_url = str(getattr(page, "url", "") or "")
            except Exception:
                current_url = ""
            try:
                title = str(getattr(page, "title", "") or "")
            except Exception:
                title = ""
            visible_text = ""
            try:
                visible_text = str(
                    page.run_js("return document.body ? document.body.innerText : ''; ")
                    or ""
                )
            except Exception:
                visible_text = ""
            with open(html_path, "w", encoding="utf-8") as f:
                f.write(html)
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "email": email,
                        "tag": tag,
                        "url": current_url,
                        "title": title,
                        "visible_text": visible_text,
                        "cookies": cookies,
                        "state": state,
                    },
                    f,
                    ensure_ascii=False,
                    indent=2,
                )
            self._log("info", f"已写入注册调试文件: {html_path}")
        except Exception as exc:
            self._log("warning", f"写入注册调试文件失败: {exc}")

    def _log(self, level: str, message: str) -> None:
        if self.log_callback:
            self.log_callback(level, message)
        else:
            getattr(logger, level, logger.info)(message)

    def _get_proxies(self) -> Optional[dict]:
        return build_proxy_dict(proxy=self.proxy)

    def register_with_api(
        self,
        email: str,
        mail_client: TempMailClient,
    ) -> NotionRegisterResult:
        result = NotionRegisterResult(
            email=email,
            register_method="api",
            attempted_api=True,
        )
        try:
            self._log("info", f"尝试 API 方式注册: {email}")
            session = requests.Session()
            session.headers.update(
                {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    "Content-Type": "application/json",
                    "Accept": "*/*",
                    "Origin": "https://www.notion.so",
                    "Referer": "https://www.notion.so/signup",
                }
            )
            if self.proxy:
                session.proxies = self._get_proxies()
            self._log("info", "发送注册邮件验证码...")
            send_resp = session.post(
                NOTION_API_SEND_EMAIL_CODE,
                json={"email": email},
                timeout=30,
            )
            if send_resp.status_code not in (200, 201):
                result.error = f"发送验证码失败: HTTP {send_resp.status_code}"
                self._log("error", result.error)
                return result
            self._log("info", "等待邮箱验证码...")
            code = mail_client.poll_for_code(timeout=180, interval=5)
            if not code:
                result.error = "验证码超时"
                self._log("error", result.error)
                return result
            self._log("info", f"收到验证码: {code}")
            first_name, last_name = random.choice(REGISTER_NAMES).split(maxsplit=1)
            username = email.split("@")[0]
            verify_resp = session.post(
                NOTION_API_VERIFY_EMAIL_CODE,
                json={"email": email, "code": code},
                timeout=30,
            )
            if verify_resp.status_code not in (200, 201):
                result.error = f"验证码验证失败: HTTP {verify_resp.status_code}"
                self._log("error", result.error)
                return result
            verify_data = verify_resp.json()
            temp_token = verify_data.get("token", "")
            if not temp_token:
                result.error = "未获取到临时 token"
                self._log("error", result.error)
                return result
            self._log("info", "创建 Notion 账户...")
            create_resp = session.post(
                NOTION_API_SIGNUP,
                json={
                    "email": email,
                    "firstName": first_name,
                    "lastName": last_name,
                    "username": username,
                    "token": temp_token,
                },
                timeout=30,
            )
            if create_resp.status_code not in (200, 201):
                result.error = f"创建账户失败: HTTP {create_resp.status_code}"
                self._log("error", result.error)
                return result
            create_data = create_resp.json()
            for cookie in session.cookies:
                if cookie.name == "token_v2":
                    result.token_v2 = cookie.value
                    break
            user_data = create_data.get("user", {}) or create_data.get(
                "recordMap", {}
            ).get("notion_user", {})
            if isinstance(user_data, dict):
                for uid, uobj in user_data.items():
                    if isinstance(uobj, dict) and "value" in uobj:
                        result.user_id = uid
                        break
            if not result.user_id:
                result.user_id = create_data.get("userId", "") or create_data.get(
                    "user_id", ""
                )
            if result.token_v2 and result.user_id:
                self._log("info", "获取工作空间信息...")
                time.sleep(2)
                spaces = self._get_user_spaces(session, result.token_v2, result.user_id)
                result.workspace_count = len(spaces)
                if spaces:
                    result.space_id = spaces[0].get("id", "")
                self._hydrate_result_from_self_profile(result)
                load_user_content = self._fetch_load_user_content(result.token_v2)
                if load_user_content and not result.space_view_id:
                    result.space_view_id = self._extract_space_view_id_from_content(
                        load_user_content
                    )
                result.success = True
                self._log(
                    "info",
                    f"API 注册成功: {email} · workspace_count={result.workspace_count} · space_id={result.space_id or 'n/a'} · space_view_id={result.space_view_id or 'n/a'}",
                )
            else:
                result.error = "未能获取完整凭据"
                self._log("error", result.error)
            return result
        except Exception as e:
            result.error = f"API 注册异常: {e}"
            self._log("error", result.error)
            return result

    def _get_user_spaces(
        self,
        session: requests.Session,
        token_v2: str,
        user_id: str,
    ) -> List[Dict[str, Any]]:
        try:
            session.cookies.set("token_v2", token_v2, domain=".notion.so")
            resp = session.post(
                "https://www.notion.so/api/v3/getSpaces",
                json={},
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            user_root = data.get(user_id, {})
            if not user_root:
                first_val = next(iter(data.values()), None)
                if isinstance(first_val, dict):
                    user_root = first_val
            spaces = user_root.get("space", {})
            result = []
            for space_id, space_obj in spaces.items():
                if isinstance(space_obj, dict) and "value" in space_obj:
                    value = space_obj["value"]
                    result.append(
                        {
                            "id": space_id,
                            "name": value.get("name", ""),
                        }
                    )
            return result
        except Exception as e:
            self._log("warning", f"获取工作空间失败: {e}")
            return []

    def register_with_browser(
        self,
        email: str,
        mail_client: TempMailClient,
    ) -> NotionRegisterResult:
        result = NotionRegisterResult(
            email=email,
            register_method="browser",
        )
        if not DRISSION_AVAILABLE:
            result.error = "DrissionPage 未安装，请运行: pip install DrissionPage"
            self._log("error", result.error)
            return result
        page = None
        try:
            self._log("info", f"启动浏览器注册: {email}")
            page = self._create_browser_page()
            self._page = page
            page.get(NOTION_SIGNUP_URL, timeout=self.timeout)
            time.sleep(random.uniform(2, 4))
            self._log("info", "输入邮箱地址...")
            email_input = page.ele("css:input[type='email']", timeout=10)
            if not email_input:
                email_input = page.ele("css:input[name='email']", timeout=5)
            if not email_input:
                result.error = "未找到邮箱输入框"
                return result
            email_input.input(email, clear=True)
            time.sleep(random.uniform(0.5, 1))
            continue_btn = self._find_button(
                page, ["Continue", "继续", "Sign up", "注册"]
            )
            if continue_btn:
                continue_btn.click()
            else:
                email_input.input("\n")
            self._log("info", "等待验证码...")
            code = mail_client.poll_for_code(timeout=180, interval=5)
            if not code:
                result.error = "验证码超时"
                return result
            self._log("info", f"收到验证码: {code}")
            if not self._submit_verification_code(page, code):
                result.error = "未找到验证码输入框"
                return result
            time.sleep(random.uniform(2, 4))
            if self._retry_verification_step(page, code):
                time.sleep(random.uniform(2, 4))
            self._log("info", "填写用户信息...")
            name_input = page.ele("css:input[name='name']", timeout=10)
            if not name_input:
                name_input = page.ele("css:input[placeholder*='name']", timeout=5)
            if name_input:
                full_name = DEFAULT_REGISTER_DISPLAY_NAME
                name_input.input(full_name, clear=True)
                time.sleep(random.uniform(0.5, 1))
                submit_btn = self._find_button(
                    page, ["Create account", "创建账户", "Continue", "继续"]
                )
                if submit_btn:
                    submit_btn.click()
            self._log("info", "等待注册完成...")
            time.sleep(random.uniform(5, 10))
            self._complete_post_signup_flow(page)
            self._retry_email_submit_if_signup_reset(page, email)
            self._log("info", "提取凭据...")
            result.token_v2 = self._extract_token_v2(page)
            result.user_id = self._extract_user_id(page)
            self._hydrate_registered_workspace(result, page, email)
            self._hydrate_result_from_self_profile(result)
            if result.token_v2 and result.user_id:
                result.success = True
                self._log("info", f"浏览器注册成功: {email}")
            else:
                self._write_debug_artifacts(page, email, "credential_extract_failed")
                result.error = "未能提取完整凭据"
            return result
        except Exception as e:
            result.error = f"浏览器注册异常: {e}"
            self._log("error", result.error)
            return result
        finally:
            if page:
                try:
                    page.quit()
                except Exception:
                    pass
            self._page = None

    def _create_browser_page(self):
        options = ChromiumOptions()
        chromium_path = find_chromium_path()
        if chromium_path:
            options.set_browser_path(chromium_path)
        options.set_argument("--incognito")
        options.set_argument("--disable-extensions")
        is_linux = platform.system().lower() == "linux"
        if is_linux:
            options.set_argument("--no-sandbox")
            options.set_argument("--disable-dev-shm-usage")
        vw, vh = random.choice(COMMON_VIEWPORTS)
        options.set_argument(f"--window-size={vw},{vh}")
        options.set_user_agent(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        if self.headless:
            options.set_argument("--headless=new")
            options.set_argument("--disable-gpu")
        if self.proxy:
            options.set_argument(f"--proxy-server={self.proxy}")
        options.auto_port()
        page = ChromiumPage(options)
        page.set.timeouts(self.timeout)
        return page

    def _find_button(self, page, keywords: List[str]):
        selectors = ["tag:button", "css:[role='button']", "css:input[type='submit']"]
        for selector in selectors:
            try:
                elements = page.eles(selector, timeout=2)
                for el in elements:
                    text = (el.text or "").strip().lower()
                    if any(kw.lower() in text for kw in keywords):
                        return el
            except Exception:
                continue
        return None

    def _extract_cookie_value(self, page, cookie_name: str) -> str:
        try:
            cookies = page.cookies()
            for cookie in cookies:
                if cookie.get("name") == cookie_name and cookie.get("value"):
                    return str(cookie.get("value") or "")
        except Exception:
            pass
        try:
            value = page.run_js(
                f"return document.cookie.split('; ').find(row => row.startsWith('{cookie_name}='))?.split('=')[1] || '';"
            )
            return str(value or "")
        except Exception:
            return ""

    def _extract_json_state(self, page) -> dict[str, Any]:
        candidate_scripts = [
            "return window.__INITIAL_STATE__ || null;",
            "return window.__NEXT_DATA__ || null;",
            "return window.__NUXT__ || null;",
            "return window.__BOOTSTRAP__ || null;",
            "return JSON.parse(localStorage.getItem('redux-state') || 'null');",
            "return JSON.parse(localStorage.getItem('LRU:State') || 'null');",
            "return JSON.parse(sessionStorage.getItem('redux-state') || 'null');",
        ]
        for script in candidate_scripts:
            try:
                value = page.run_js(script)
                if isinstance(value, dict) and value:
                    return value
            except Exception:
                continue
        return {}

    def _get_visible_text(self, page) -> str:
        try:
            text = page.run_js("return document.body ? document.body.innerText : ''; ")
            return str(text or "")
        except Exception:
            return ""

    def _page_looks_reset_to_signup(self, page) -> bool:
        visible_text = self._get_visible_text(page)
        if not visible_text:
            return False
        markers = [
            "使用工作电子邮件地址注册",
            "无效的邮件地址",
            "Existing user? Log in",
            "工作邮件",
        ]
        return all(marker in visible_text for marker in markers)

    def _retry_email_submit_if_signup_reset(self, page, email: str) -> bool:
        if not self._page_looks_reset_to_signup(page):
            return False
        self._log("warning", "注册页面疑似重置回初始态，尝试重新提交邮箱")
        try:
            email_input = page.ele("css:input[type='email']", timeout=5)
            if not email_input:
                email_input = page.ele("css:input[name='email']", timeout=3)
            if not email_input:
                return False
            email_input.input(email, clear=True)
            time.sleep(random.uniform(0.3, 0.8))
            continue_btn = self._find_button(
                page, ["Continue", "继续", "Sign up", "注册"]
            )
            if continue_btn:
                continue_btn.click()
            else:
                email_input.input("\n")
            time.sleep(random.uniform(2, 4))
            return True
        except Exception as exc:
            self._log("warning", f"重新提交邮箱失败: {exc}")
            return False

    def _page_still_on_verification_step(self, page) -> bool:
        visible_text = self._get_visible_text(page)
        if not visible_text:
            try:
                return bool(page.ele("css:input[inputmode='numeric']", timeout=1))
            except Exception:
                return False
        markers = [
            "验证码",
            "我们已将验证码发送到你的收件箱",
            "重新发送",
            "verification code",
            "enter code",
            "6-digit code",
        ]
        lowered = visible_text.lower()
        if any(marker.lower() in lowered for marker in markers):
            return True
        try:
            selectors = [
                "css:input[inputmode='numeric']",
                "css:input[autocomplete='one-time-code']",
                "css:input[name*='code']",
                "css:input[id*='code']",
                "css:input[aria-label*='code']",
            ]
            return any(page.ele(selector, timeout=1) for selector in selectors)
        except Exception:
            return False

    def _retry_verification_step(self, page, code: str) -> bool:
        if not self._page_still_on_verification_step(page):
            return False
        self._log("warning", "页面仍停留在验证码步骤，尝试重新提交验证码")
        return self._submit_verification_code(page, code)

    def _submit_verification_code(self, page, code: str) -> bool:
        code = str(code or "").strip()
        if not code:
            return False
        segmented_inputs = []
        try:
            segmented_inputs = page.eles(
                "css:input[inputmode='numeric'],input[autocomplete='one-time-code'],input[name*='code'],input[id*='code']",
                timeout=3,
            )
        except Exception:
            segmented_inputs = []
        segmented_inputs = [item for item in segmented_inputs if item]
        if len(segmented_inputs) >= min(len(code), 4):
            self._log("info", f"检测到分段验证码输入框: {len(segmented_inputs)}")
            for idx, char in enumerate(code):
                if idx >= len(segmented_inputs):
                    break
                try:
                    segmented_inputs[idx].input(char, clear=True)
                    time.sleep(random.uniform(0.05, 0.15))
                except Exception:
                    continue
            time.sleep(random.uniform(0.5, 1.0))
            verify_btn = self._find_button(page, ["Verify", "验证", "Continue", "继续"])
            if verify_btn:
                verify_btn.click()
            return True

        code_input = None
        selectors = [
            "css:input[autocomplete='one-time-code']",
            "css:input[name*='code']",
            "css:input[id*='code']",
            "css:input[aria-label*='code']",
            "css:input[inputmode='numeric']",
            "css:input[type='tel']",
            "css:input[type='text']",
        ]
        for selector in selectors:
            try:
                code_input = page.ele(selector, timeout=2)
            except Exception:
                code_input = None
            if code_input:
                break
        if not code_input:
            return False
        code_input.input(code, clear=True)
        time.sleep(random.uniform(0.5, 1))
        verify_btn = self._find_button(page, ["Verify", "验证", "Continue", "继续"])
        if verify_btn:
            verify_btn.click()
        else:
            code_input.input("\n")
        return True

    def _find_value_recursive(self, payload: Any, target_keys: set[str]) -> str:
        if isinstance(payload, dict):
            for key, value in payload.items():
                if key in target_keys and isinstance(value, str) and value.strip():
                    return value.strip()
                found = self._find_value_recursive(value, target_keys)
                if found:
                    return found
        elif isinstance(payload, list):
            for item in payload:
                found = self._find_value_recursive(item, target_keys)
                if found:
                    return found
        return ""

    def _extract_token_v2(self, page) -> str:
        token = self._extract_cookie_value(page, "token_v2")
        if token:
            return token
        state = self._extract_json_state(page)
        return self._find_value_recursive(state, {"token_v2", "tokenV2", "token"})

    def _extract_user_id(self, page) -> str:
        cookie_candidates = ["notion_user_id", "user_id", "userId"]
        for cookie_name in cookie_candidates:
            cookie_value = self._extract_cookie_value(page, cookie_name)
            if cookie_value:
                return cookie_value
        try:
            cookies = page.cookies()
            for cookie in cookies:
                if cookie.get("name") == "p_sync_session":
                    raw_value = str(cookie.get("value") or "")
                    try:
                        parsed = json.loads(unquote(raw_value))
                        user_ids = (
                            parsed.get("userIds") if isinstance(parsed, dict) else None
                        )
                        if isinstance(user_ids, list) and user_ids:
                            return str(user_ids[0] or "")
                    except Exception:
                        pass
        except Exception:
            pass
        try:
            html = page.html or ""
            import re

            patterns = [
                r'"userId"\s*:\s*"([^"]+)"',
                r'"user_id"\s*:\s*"([^"]+)"',
                r'"id"\s*:\s*"([a-f0-9-]{36})"',
            ]
            for pattern in patterns:
                match = re.search(pattern, html)
                if match:
                    return match.group(1)
        except Exception:
            pass
        try:
            state = self._extract_json_state(page)
            found = self._find_value_recursive(
                state,
                {"userId", "user_id", "userID", "id"},
            )
            if found:
                return found
        except Exception:
            pass
        return ""

    def _hydrate_registered_workspace(
        self, result: NotionRegisterResult, page, email: str
    ) -> None:
        if not result.token_v2:
            result.token_v2 = self._extract_token_v2(page)
        if not result.user_id:
            result.user_id = self._extract_user_id(page)
        if not (result.token_v2 and result.user_id):
            return
        try:
            session = requests.Session()
            if self.proxy:
                session.proxies = self._get_proxies()
            session.headers.update(
                {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    "Content-Type": "application/json",
                    "Accept": "*/*",
                    "Origin": "https://www.notion.so",
                    "Referer": "https://www.notion.so/",
                }
            )
            session.cookies.set("token_v2", result.token_v2, domain=".notion.so")
            spaces = self._get_user_spaces(session, result.token_v2, result.user_id)
            result.workspace_count = len(spaces)
            if spaces:
                result.space_id = spaces[0].get("id", "")
            if not result.space_view_id:
                result.space_view_id = ""
            self._log(
                "info",
                f"浏览器注册凭据提取结果: token={'yes' if bool(result.token_v2) else 'no'} user_id={result.user_id or 'n/a'} space_id={result.space_id or 'n/a'}",
            )
        except Exception as exc:
            self._log("warning", f"浏览器注册后补充工作空间信息失败: {exc}")

    def _fetch_self_profile(self, token_v2: str) -> dict[str, Any]:
        if not token_v2:
            return {}
        try:
            session = requests.Session()
            if self.proxy:
                session.proxies = self._get_proxies()
            session.headers.update(
                {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    "Content-Type": "application/json",
                    "Accept": "*/*",
                    "Origin": "https://www.notion.so",
                    "Referer": "https://www.notion.so/",
                }
            )
            session.cookies.set("token_v2", token_v2, domain=".notion.so")
            resp = session.post(NOTION_API_GET_SELF, json={}, timeout=30)
            if not resp.ok:
                return {}
            data = resp.json()
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _fetch_load_user_content(self, token_v2: str) -> dict[str, Any]:
        if not token_v2:
            return {}
        try:
            session = requests.Session()
            if self.proxy:
                session.proxies = self._get_proxies()
            session.headers.update(
                {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    "Content-Type": "application/json",
                    "Accept": "*/*",
                    "Origin": "https://www.notion.so",
                    "Referer": "https://www.notion.so/",
                }
            )
            session.cookies.set("token_v2", token_v2, domain=".notion.so")
            resp = session.post(NOTION_API_LOAD_USER_CONTENT, json={}, timeout=30)
            if not resp.ok:
                return {}
            data = resp.json()
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _extract_space_view_id_from_content(self, payload: dict[str, Any]) -> str:
        if not isinstance(payload, dict):
            return ""
        record_map = (
            payload.get("recordMap")
            if isinstance(payload.get("recordMap"), dict)
            else payload
        )
        collection_view = (
            record_map.get("collection_view")
            if isinstance(record_map.get("collection_view"), dict)
            else {}
        )
        for view_id, view_obj in collection_view.items():
            if not isinstance(view_obj, dict):
                continue
            value = (
                view_obj.get("value") if isinstance(view_obj.get("value"), dict) else {}
            )
            space_id = str(value.get("space_id") or "").strip()
            if space_id:
                return str(view_id or "").strip()
        return self._find_value_recursive(payload, {"space_view_id", "spaceViewId"})

    def _complete_post_signup_flow(self, page) -> None:
        action_keywords = [
            ["For myself", "个人使用", "Use for myself"],
            ["Skip", "跳过", "Not now", "稍后"],
            ["Continue", "继续", "Next", "下一步", "Get started", "开始使用"],
            ["Open Notion", "Launch Notion", "进入 Notion"],
        ]
        avoid_keywords = [
            "workspace",
            "team",
            "organization",
            "company",
            "join",
            "加入工作空间",
            "加入团队",
            "加入组织",
            "go to workspace",
        ]
        for _ in range(8):
            time.sleep(random.uniform(1.0, 2.0))
            current_url = ""
            try:
                current_url = str(getattr(page, "url", "") or "")
            except Exception:
                current_url = ""
            if current_url and "/signup" not in current_url:
                return
            clicked = False
            for keywords in action_keywords:
                btn = self._find_button(page, keywords)
                if btn:
                    try:
                        btn_text = (btn.text or "").strip().lower()
                        if any(item in btn_text for item in avoid_keywords):
                            continue
                        self._log("info", f"点击注册后续按钮: {'/'.join(keywords[:2])}")
                        btn.click()
                        clicked = True
                        break
                    except Exception:
                        continue
            if not clicked:
                break

    def _hydrate_result_from_self_profile(self, result: NotionRegisterResult) -> None:
        profile = self._fetch_self_profile(result.token_v2)
        if not profile:
            return
        if not result.user_id:
            result.user_id = self._find_value_recursive(
                profile,
                {"user_id", "userId", "id"},
            )
        if not result.space_id:
            result.space_id = self._find_value_recursive(
                profile,
                {"space_id", "spaceId", "active_space_id", "activeSpaceId"},
            )

    def finalize_account_record(self, account: dict[str, Any]) -> dict[str, Any]:
        finalized = dict(account)
        token_v2 = str(finalized.get("token_v2") or "").strip()
        user_id = str(finalized.get("user_id") or "").strip()
        if not (token_v2 and user_id):
            return finalized
        try:
            session = requests.Session()
            if self.proxy:
                session.proxies = self._get_proxies()
            session.headers.update(
                {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    "Content-Type": "application/json",
                    "Accept": "*/*",
                    "Origin": "https://www.notion.so",
                    "Referer": "https://www.notion.so/",
                }
            )
            session.cookies.set("token_v2", token_v2, domain=".notion.so")
            spaces = self._get_user_spaces(session, token_v2, user_id)
            if spaces:
                finalized["space_id"] = str(
                    finalized.get("space_id") or spaces[0].get("id") or ""
                )
                finalized.setdefault("workspace", {})
                finalized["workspace"] = {
                    **(
                        finalized.get("workspace")
                        if isinstance(finalized.get("workspace"), dict)
                        else {}
                    ),
                    "workspace_count": len(spaces),
                    "workspaces": spaces,
                    "state": "ready",
                }
            profile = self._fetch_self_profile(token_v2)
            if profile:
                if not str(finalized.get("space_id") or "").strip():
                    finalized["space_id"] = self._find_value_recursive(
                        profile,
                        {"space_id", "spaceId", "active_space_id", "activeSpaceId"},
                    )
                if not str(finalized.get("user_email") or "").strip():
                    finalized["user_email"] = self._find_value_recursive(
                        profile,
                        {"email", "user_email", "userEmail"},
                    )
                if not str(finalized.get("user_name") or "").strip():
                    finalized["user_name"] = self._find_value_recursive(
                        profile,
                        {"name", "full_name", "fullName", "given_name", "givenName"},
                    )
            load_user_content = self._fetch_load_user_content(token_v2)
            if load_user_content:
                if not str(finalized.get("space_view_id") or "").strip():
                    finalized["space_view_id"] = (
                        self._extract_space_view_id_from_content(load_user_content)
                    )
        except Exception as exc:
            self._log("warning", f"注册后账户补全失败: {exc}")
        return finalized

    def register(
        self,
        mail_client: TempMailClient,
        use_api: bool = True,
    ) -> NotionRegisterResult:
        if not mail_client.email:
            return NotionRegisterResult(error="邮箱客户端未初始化")
        email = mail_client.email
        if use_api:
            result = self.register_with_api(email, mail_client)
            if result.success:
                return result
            self._log("warning", f"API 注册失败，尝试浏览器模式: {result.error}")
            browser_result = self.register_with_browser(email, mail_client)
            browser_result.attempted_api = True
            browser_result.used_browser_fallback = True
            if not browser_result.register_method:
                browser_result.register_method = "browser"
            return browser_result
        browser_result = self.register_with_browser(email, mail_client)
        browser_result.register_method = browser_result.register_method or "browser"
        return browser_result

    def stop(self) -> None:
        if self._page:
            try:
                self._page.quit()
            except Exception:
                pass
            self._page = None

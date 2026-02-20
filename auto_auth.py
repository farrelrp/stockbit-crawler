"""
Auto-login to Stockbit using pure Python + 2Captcha API.

Sends reCAPTCHA task to 2Captcha, receives a solved token, then POSTs
to Stockbit's login endpoint. No browser automation needed.

Flow:
  1. Submit reCAPTCHA task to 2Captcha (sitekey + pageURL)
  2. Poll for solved token (~15-60 seconds)
  3. POST to Stockbit /api/login/email with credentials + solved token
  4. Extract JWT from JSON response
"""
import json
import logging
import threading
import time
import requests
from typing import Dict, Any

from config import (
    TWOCAPTCHA_API_KEY,
    RECAPTCHA_SITE_KEY,
    STOCKBIT_LOGIN_URL,
    LOGIN_HEADERS,
)

logger = logging.getLogger(__name__)

TWOCAPTCHA_CREATE_TASK = "https://api.2captcha.com/createTask"
TWOCAPTCHA_GET_RESULT = "https://api.2captcha.com/getTaskResult"
LOGIN_PAGE_URL = "https://stockbit.com/login"


class AutoAuth:
    """Automates Stockbit login via 2Captcha + direct HTTP POST."""

    def __init__(self, token_manager):
        self.token_manager = token_manager
        self._running = False
        self._status = "idle"
        self._progress = []
        self._result = None
        self._session_cookies = ""

    def _log(self, message: str):
        logger.info(f"[AutoAuth] {message}")
        self._progress.append(message)

    def get_status(self) -> Dict[str, Any]:
        return {
            "running": self._running,
            "status": self._status,
            "progress": self._progress.copy(),
            "result": self._result,
        }

    # ---- 2Captcha solver ----

    def _solve_recaptcha(self) -> str:
        """
        Submit reCAPTCHA to 2Captcha and poll until solved.
        Tries v3 first (score-based), falls back to v2 invisible if v3 fails.
        """
        self._status = "submitting_captcha"
        self._log("Submitting reCAPTCHA v3 to 2Captcha...")

        payload = {
            "clientKey": TWOCAPTCHA_API_KEY,
            "task": {
                "type": "RecaptchaV3TaskProxyless",
                "websiteURL": LOGIN_PAGE_URL,
                "websiteKey": RECAPTCHA_SITE_KEY,
                "minScore": 0.9,
                "pageAction": "login",
            },
        }

        resp = requests.post(TWOCAPTCHA_CREATE_TASK, json=payload, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        if data.get("errorId"):
            err = data.get("errorDescription", data)
            # if v3 submission fails entirely, try v2 invisible
            self._log(f"v3 submit failed ({err}), trying v2 invisible...")
            payload["task"] = {
                "type": "RecaptchaV2TaskProxyless",
                "websiteURL": LOGIN_PAGE_URL,
                "websiteKey": RECAPTCHA_SITE_KEY,
                "isInvisible": True,
            }
            resp = requests.post(TWOCAPTCHA_CREATE_TASK, json=payload, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            if data.get("errorId"):
                raise Exception(f"2Captcha createTask error: {data.get('errorDescription', data)}")

        task_id = data["taskId"]
        self._log(f"Task submitted (ID: {task_id}). Waiting for solver...")
        self._status = "waiting_captcha_solution"

        # wait before first poll
        time.sleep(10)

        for attempt in range(36):  # up to ~3 minutes
            poll_resp = requests.post(
                TWOCAPTCHA_GET_RESULT,
                json={"clientKey": TWOCAPTCHA_API_KEY, "taskId": task_id},
                timeout=30,
            )
            poll_resp.raise_for_status()
            poll_data = poll_resp.json()

            if poll_data.get("errorId"):
                raise Exception(f"2Captcha poll error: {poll_data.get('errorDescription', poll_data)}")

            if poll_data.get("status") == "ready":
                token = poll_data["solution"]["gRecaptchaResponse"]
                self._log(f"Captcha solved! Token length: {len(token)}")
                self._status = "captcha_solved"
                return token

            self._log(f"Not ready yet... (poll {attempt + 1}/36)")
            time.sleep(5)

        raise Exception("2Captcha timeout — solution not ready after 3 minutes")

    # ---- Stockbit login ----

    def _post_login(self, email: str, password: str, captcha_token: str) -> Dict:
        """
        POST to Stockbit login endpoint with credentials + solved captcha token.
        Uses a session to maintain any cookies from visiting the login page.
        """
        self._status = "posting_login"

        session = requests.Session()
        session.headers.update(LOGIN_HEADERS)

        # visit login page first to pick up any session cookies
        self._log("Visiting login page for session cookies...")
        session.get(LOGIN_PAGE_URL, timeout=15)

        self._log("Sending login request to Stockbit...")
        body = {
            "username": email,
            "password": password,
            "verificationToken": captcha_token,
            "recaptchaVersion": "RECAPTCHA_VERSION_3",
        }

        resp = session.post(STOCKBIT_LOGIN_URL, json=body, timeout=30)

        self._status = "processing_login_response"
        self._log(f"Login response status: {resp.status_code}")

        if resp.status_code != 200:
            text = resp.text[:500]
            raise Exception(f"Login failed (HTTP {resp.status_code}): {text}")

        # keep cookies from the authenticated session
        self._session_cookies = "; ".join(f"{k}={v}" for k, v in session.cookies.items())

        return resp.json()

    # ---- Main flow ----

    def _do_login(self, email: str, password: str):
        """Full login flow: solve captcha -> POST login -> extract token."""
        self._running = True
        self._status = "starting"
        self._progress = []
        self._result = None

        try:
            if not TWOCAPTCHA_API_KEY:
                raise Exception("TWOCAPTCHA_API_KEY not set. Add it to your .env file.")

            self._log(f"Starting auto-login for {email[:3]}***")

            # step 1 — solve captcha
            captcha_token = self._solve_recaptcha()

            # step 2 — login
            login_data = self._post_login(email, password, captcha_token)

            # step 3 — extract bearer token from response
            token = (
                login_data.get("data", {}).get("access_token")
                or login_data.get("data", {}).get("token")
                or login_data.get("access_token")
                or login_data.get("token")
                or login_data.get("data", {}).get("user", {}).get("access_token")
            )

            if not token or len(token) < 50:
                self._log(f"Response keys: {list(login_data.keys())}")
                if "data" in login_data and isinstance(login_data["data"], dict):
                    self._log(f"data keys: {list(login_data['data'].keys())}")
                raise Exception(
                    f"No valid Bearer token in response. Response: {json.dumps(login_data)[:500]}"
                )

            self._log(f"Got Bearer token ({len(token)} chars)")

            # use session cookies, fall back to a static Google Sign-In marker
            ws_cookie = self._session_cookies or "G_ENABLED_IDPS=google"
            self._log(f"WS cookies ({len(ws_cookie)} chars)")

            # step 4 — save via TokenManager
            self._status = "setting_token"
            self._log("Saving token via TokenManager...")

            token_result = self.token_manager.set_token(token, ws_cookie)

            if token_result.get("success"):
                self._status = "success"
                self._result = {
                    "success": True,
                    "message": "Auto-login successful!",
                    "token_length": len(token),
                    "has_cookies": bool(ws_cookie),
                    "cookie_length": len(ws_cookie),
                    "expires_at": token_result.get("expires_at"),
                }
                self._log(f"Done! Token expires: {token_result.get('expires_at')}")
            else:
                self._status = "error"
                self._result = {
                    "success": False,
                    "error": f"Token validation failed: {token_result.get('error')}",
                }
                self._log(f"Token validation failed: {token_result.get('error')}")

        except Exception as e:
            self._status = "error"
            self._result = {"success": False, "error": str(e)}
            self._log(f"Error: {e}")
            logger.error(f"Auto-login failed: {e}", exc_info=True)

        finally:
            self._running = False

    def start_login(self, email: str = None, password: str = None, **_kwargs) -> Dict[str, Any]:
        """Kick off the login flow in a background thread."""
        if self._running:
            return {"success": False, "error": "Auto-login already in progress"}

        if not email or not password:
            return {"success": False, "error": "Email and password are required."}

        thread = threading.Thread(
            target=self._do_login,
            args=(email, password),
            daemon=True,
            name="auto-auth",
        )
        thread.start()

        return {
            "success": True,
            "message": "Auto-login started. Check /api/token/auto-login/status for progress.",
        }

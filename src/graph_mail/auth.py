"""Delegated auth for Microsoft Graph: PKCE loopback sign-in + refresh.

Why this shape:

- **Public client, no secret.** The app registration has no client secret, so the
  CLI uses authorization code + PKCE against a loopback redirect. Nothing sensitive
  can leak from a file on disk.
- **Refresh token in the Keychain.** On macOS the token bundle lives in a generic
  password item (service ``graph-mail``), so other local processes cannot read it
  as a plain file. On Linux (or with ``GRAPH_MAIL_NO_KEYCHAIN=1``) it falls back to
  ``~/.config/graph-mail/tokens.json`` with 0600 permissions.
- **Rotation-safe.** Entra rotates refresh tokens; every refresh writes the new one
  back, otherwise the next run would be logged out.
- **Fail loud, not empty.** A missing/expired grant raises ``AUTH_REQUIRED`` with the
  exact command to fix it, instead of returning zero messages.
"""

from __future__ import annotations

import base64
import hashlib
import http.server
import json
import os
import secrets
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from . import config
from .errors import GraphMailError, auth_failed, auth_required

REFRESH_MARGIN_SECONDS = 180


# --------------------------------------------------------------------------- #
# Token storage
# --------------------------------------------------------------------------- #

def _keychain_available() -> bool:
    return sys.platform == "darwin" and bool(shutil.which("security")) and config.use_keychain()


def _keychain_read() -> dict | None:
    proc = subprocess.run(
        [
            "security", "find-generic-password",
            "-s", config.KEYCHAIN_SERVICE,
            "-a", config.KEYCHAIN_ACCOUNT,
            "-w",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return None
    raw = proc.stdout.strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def _keychain_write(tokens: dict) -> None:
    payload = json.dumps(tokens, separators=(",", ":"))
    proc = subprocess.run(
        [
            "security", "add-generic-password",
            "-U",
            "-s", config.KEYCHAIN_SERVICE,
            "-a", config.KEYCHAIN_ACCOUNT,
            "-l", "graph-mail Microsoft Graph token",
            "-w", payload,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise GraphMailError(
            "CONFIG_ERROR",
            f"Could not write the token to the Keychain: {proc.stderr.strip() or proc.returncode}",
        )


def _keychain_delete() -> None:
    subprocess.run(
        [
            "security", "delete-generic-password",
            "-s", config.KEYCHAIN_SERVICE,
            "-a", config.KEYCHAIN_ACCOUNT,
        ],
        capture_output=True,
        text=True,
        check=False,
    )


def _file_read() -> dict | None:
    path = config.token_file()
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def _file_write(tokens: dict) -> None:
    path = config.token_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(tokens, indent=2))
    os.chmod(tmp, 0o600)
    tmp.replace(path)


def _file_delete() -> None:
    config.token_file().unlink(missing_ok=True)


def storage_backend() -> str:
    return "keychain" if _keychain_available() else "file"


def load_tokens() -> dict | None:
    if _keychain_available():
        data = _keychain_read()
        if data:
            return data
    return _file_read()


def save_tokens(tokens: dict) -> None:
    if _keychain_available():
        _keychain_write(tokens)
        # Drop any stale plain-file copy from an earlier install.
        _file_delete()
        return
    _file_write(tokens)


def clear_tokens() -> None:
    if _keychain_available():
        _keychain_delete()
    _file_delete()


# --------------------------------------------------------------------------- #
# PKCE
# --------------------------------------------------------------------------- #

def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _pkce_pair() -> tuple[str, str]:
    verifier = _b64url(secrets.token_bytes(32))
    challenge = _b64url(hashlib.sha256(verifier.encode()).digest())
    return verifier, challenge


# --------------------------------------------------------------------------- #
# Token endpoint
# --------------------------------------------------------------------------- #

def _post_form(url: str, form: dict) -> dict:
    status, payload = _post_form_raw(url, form)
    if status != 200:
        raise auth_failed(
            payload.get("error_description") or payload.get("error") or "Token request failed",
            details={"status": status, "code": payload.get("error")},
        )
    return payload


def _post_form_raw(url: str, form: dict) -> tuple[int, dict]:
    """POST a form and return (status, payload) without raising on a 4xx.

    Device-code polling needs the error code from a 400, so the raw form keeps it.
    """
    body = urllib.parse.urlencode(form).encode()
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode(errors="replace")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = {"error": f"HTTP_{exc.code}", "error_description": raw}
        return exc.code, payload
    except (urllib.error.URLError, TimeoutError) as exc:
        raise GraphMailError("NETWORK_ERROR", f"Could not reach the Microsoft identity service: {exc}")


def _record_from_response(payload: dict, *, previous: dict | None = None) -> dict:
    now = int(time.time())
    record = {
        "access_token": payload["access_token"],
        "token_type": payload.get("token_type", "Bearer"),
        "scope": payload.get("scope", config.scope_string()),
        "expires_at": now + int(payload.get("expires_in", 3600)),
        "obtained_at": now,
    }
    refresh = payload.get("refresh_token") or (previous or {}).get("refresh_token")
    if refresh:
        record["refresh_token"] = refresh
    for key in ("account", "account_id", "tenant_id"):
        if previous and previous.get(key):
            record[key] = previous[key]
    return record


def refresh_tokens(tokens: dict | None = None) -> dict:
    tokens = tokens if tokens is not None else load_tokens()
    if not tokens or not tokens.get("refresh_token"):
        raise auth_required("no refresh token stored")
    payload = _post_form(
        config.token_url(),
        {
            "client_id": config.client_id(),
            "grant_type": "refresh_token",
            "refresh_token": tokens["refresh_token"],
            "scope": config.scope_string(),
        },
    )
    record = _record_from_response(payload, previous=tokens)
    save_tokens(record)
    return record


def access_token(force_refresh: bool = False) -> str:
    """Return a valid access token, refreshing when it is close to expiry."""
    tokens = load_tokens()
    if not tokens:
        raise auth_required("no token stored")
    fresh = tokens.get("expires_at", 0) - REFRESH_MARGIN_SECONDS
    if force_refresh or fresh <= int(time.time()):
        if not tokens.get("refresh_token"):
            raise auth_required("access token expired and no refresh token stored")
        tokens = refresh_tokens(tokens)
    return tokens["access_token"]


# --------------------------------------------------------------------------- #
# Interactive login
# --------------------------------------------------------------------------- #

class _CallbackServer(http.server.HTTPServer):
    """Single-shot HTTP server that captures the authorization code."""

    allow_reuse_address = True
    result: dict | None = None


def _make_handler(expected_state: str):
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - stdlib naming
            url = urllib.parse.urlparse(self.path)
            if url.path != "/callback":
                self.send_response(404)
                self.end_headers()
                return
            params = urllib.parse.parse_qs(url.query)
            if params.get("state", [""])[0] != expected_state:
                self._finish(400, "<h1>State mismatch</h1><p>Sign-in aborted.</p>")
                self.server.result = {"error": "state_mismatch"}
                return
            if "error" in params:
                desc = params.get("error_description", [params["error"][0]])[0]
                self._finish(400, f"<h1>Sign-in failed</h1><p>{desc}</p>")
                self.server.result = {
                    "error": params["error"][0],
                    "error_description": desc,
                }
                return
            self._finish(
                200,
                "<h1>Signed in to graph-mail</h1><p>You can close this tab.</p>",
            )
            self.server.result = {"code": params.get("code", [""])[0]}

        def _finish(self, status: int, body: str):
            data = f"<!doctype html><meta charset=utf-8><body style='font:16px system-ui;padding:3rem'>{body}</body>".encode()
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *a):  # silence stderr noise
            return

    return Handler


def login(*, open_browser: bool = True, timeout: int = 300, echo=print) -> dict:
    """Run the loopback PKCE sign-in and store the resulting tokens."""
    verifier, challenge = _pkce_pair()
    state = _b64url(secrets.token_bytes(16))
    p = config.port()
    redirect = config.redirect_uri(p)

    query = urllib.parse.urlencode(
        {
            "client_id": config.client_id(),
            "response_type": "code",
            "redirect_uri": redirect,
            "response_mode": "query",
            "scope": config.scope_string(),
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "prompt": "select_account",
        }
    )
    url = f"{config.authorize_url()}?{query}"

    server = _CallbackServer(("localhost", p), _make_handler(state))
    # One second per accept, so an early /favicon.ico or prefetch request cannot
    # consume the single callback; keep serving until the real code arrives.
    server.timeout = 1.0

    echo("Opening the Microsoft sign-in page in your browser. If it does not open, visit:")
    echo(url)
    if open_browser:
        _open_browser(url)

    deadline = time.time() + timeout
    while time.time() < deadline and server.result is None:
        server.handle_request()

    server.server_close()

    result = server.result
    if not result:
        raise auth_failed("Timed out waiting for the browser sign-in to complete.")
    if result.get("error"):
        raise auth_failed(
            result.get("error_description") or result["error"],
            details={"code": result["error"]},
        )

    payload = _post_form(
        config.token_url(),
        {
            "client_id": config.client_id(),
            "grant_type": "authorization_code",
            "code": result["code"],
            "redirect_uri": redirect,
            "code_verifier": verifier,
            "scope": config.scope_string(),
        },
    )
    record = _record_from_response(payload)
    save_tokens(record)
    return record


def _open_browser(url: str) -> None:
    try:
        if sys.platform == "darwin":
            subprocess.run(["open", url], check=False)
        elif os.environ.get("DISPLAY") or sys.platform.startswith("linux"):
            subprocess.run(["xdg-open", url], check=False)
    except OSError:
        pass


DEVICE_CODE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"


def login_device_code(*, timeout: int | None = None, echo=print, sleep=time.sleep) -> dict:
    """Sign in on a headless host: show a code, poll until the browser approves.

    The loopback flow needs the browser and the CLI on one machine. This flow does
    not, which is what makes the CLI usable on the Ubuntu VM.
    """
    status, start = _post_form_raw(
        config.devicecode_url(),
        {"client_id": config.client_id(), "scope": config.scope_string()},
    )
    if status != 200:
        raise auth_failed(
            start.get("error_description") or start.get("error") or "Could not start device-code sign-in",
            details={"status": status, "code": start.get("error")},
        )

    echo(start.get("message") or f"Open {start.get('verification_uri')} and enter {start.get('user_code')}")
    echo(f"Code: {start.get('user_code')}  URL: {start.get('verification_uri')}")

    deadline = time.time() + (timeout or int(start.get("expires_in", 900)))
    interval = int(start.get("interval", 5))

    while time.time() < deadline:
        sleep(interval)
        status, payload = _post_form_raw(
            config.token_url(),
            {
                "client_id": config.client_id(),
                "grant_type": DEVICE_CODE_GRANT,
                "device_code": start["device_code"],
            },
        )
        if status == 200:
            record = _record_from_response(payload)
            save_tokens(record)
            return record
        error = payload.get("error")
        if error == "authorization_pending":
            continue
        if error == "slow_down":
            interval += 5
            continue
        raise auth_failed(
            payload.get("error_description") or error or "Device-code sign-in failed",
            details={"code": error},
        )

    raise auth_failed("Timed out waiting for the device-code sign-in to complete.")


# --------------------------------------------------------------------------- #
# Status
# --------------------------------------------------------------------------- #

def status() -> dict:
    tokens = load_tokens()
    if not tokens:
        return {"authenticated": False, "backend": storage_backend()}
    expires_at = int(tokens.get("expires_at", 0))
    remaining = expires_at - int(time.time())
    return {
        "authenticated": True,
        "backend": storage_backend(),
        "account": tokens.get("account"),
        "scope": tokens.get("scope"),
        "hasRefreshToken": bool(tokens.get("refresh_token")),
        "expiresAt": datetime.fromtimestamp(expires_at, tz=timezone.utc).astimezone().isoformat(),
        "expiresInSeconds": remaining,
        "valid": remaining > 0,
    }

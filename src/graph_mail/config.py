"""Configuration: the Entra app registration, scopes, and local storage paths.

Defaults target the Nutanix "Personal Productivity - Outlook Read-Only Mail and
Calendar Automation" app registration. Override with environment variables when a
different tenant/app is used.
"""

from __future__ import annotations

import os
from pathlib import Path

DEFAULT_CLIENT_ID = "981611a3-6f7b-4ff2-b5db-1de3a51e2ae8"
DEFAULT_TENANT = "bb047546-786f-4de1-bd75-24e5b6f79043"

# Delegated, read-only. offline_access buys the refresh token that makes
# unattended runs possible.
SCOPES = ("offline_access", "Mail.Read", "Calendars.Read", "User.Read")

# Must match a redirect URI registered on the app (Mobile and desktop
# applications platform). Changing the port means re-registering it.
DEFAULT_PORT = 8400

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
KEYCHAIN_SERVICE = "graph-mail"
KEYCHAIN_ACCOUNT = "graph-mail"


def client_id() -> str:
    return os.environ.get("GRAPH_MAIL_CLIENT_ID") or DEFAULT_CLIENT_ID


def tenant() -> str:
    return os.environ.get("GRAPH_MAIL_TENANT") or DEFAULT_TENANT


def port() -> int:
    try:
        return int(os.environ.get("GRAPH_MAIL_PORT") or DEFAULT_PORT)
    except ValueError:
        return DEFAULT_PORT


def redirect_uri(p: int | None = None) -> str:
    return f"http://localhost:{p or port()}/callback"


def config_dir() -> Path:
    override = os.environ.get("GRAPH_MAIL_CONFIG_DIR")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".config" / "graph-mail"


def token_file() -> Path:
    return config_dir() / "tokens.json"


def use_keychain() -> bool:
    return os.environ.get("GRAPH_MAIL_NO_KEYCHAIN") != "1"


def authorize_url(p: int | None = None) -> str:
    return f"https://login.microsoftonline.com/{tenant()}/oauth2/v2.0/authorize"


def token_url() -> str:
    return f"https://login.microsoftonline.com/{tenant()}/oauth2/v2.0/token"


def scope_string() -> str:
    return " ".join(SCOPES)

"""Stable error taxonomy.

Every failure is surfaced as a ``GraphMailError`` carrying a stable ``code`` (safe to
branch on programmatically) and a human-readable ``message``. The CLI turns these
into ``{"ok": false, "error": {"code", "message"}}`` envelopes on stderr.
"""

from __future__ import annotations

# Map of stable codes -> process exit codes. Anything not listed exits 1.
EXIT_CODES: dict[str, int] = {
    "VALIDATION_ERROR": 2,
    "AUTH_REQUIRED": 3,
    "AUTH_FAILED": 4,
    "NOT_FOUND": 5,
    "CONFIG_ERROR": 6,
    "GRAPH_ERROR": 7,
    "NETWORK_ERROR": 8,
}


class GraphMailError(Exception):
    """A user-surfaceable error with a stable code."""

    def __init__(self, code: str, message: str, *, details: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}

    @property
    def exit_code(self) -> int:
        return EXIT_CODES.get(self.code, 1)

    def to_dict(self) -> dict:
        err: dict = {"code": self.code, "message": self.message}
        if self.details:
            err["details"] = self.details
        return err


def validation(msg: str) -> GraphMailError:
    return GraphMailError("VALIDATION_ERROR", msg)


def not_found(what: str) -> GraphMailError:
    return GraphMailError("NOT_FOUND", what)


def auth_required(reason: str = "") -> GraphMailError:
    msg = (
        "Not signed in to Microsoft Graph. Run `graph-mail auth login` once; the "
        "refresh token is stored in the macOS Keychain, so later runs are silent."
    )
    if reason:
        msg = f"{msg} ({reason})"
    return GraphMailError("AUTH_REQUIRED", msg)


def auth_failed(msg: str, *, details: dict | None = None) -> GraphMailError:
    return GraphMailError("AUTH_FAILED", msg, details=details)


def graph_error(status: int, payload, *, request_id: str | None = None) -> GraphMailError:
    if isinstance(payload, dict):
        err = payload.get("error") or {}
        code = err.get("code") or f"HTTP_{status}"
        message = err.get("message") or f"Graph returned HTTP {status}"
    else:
        code, message = f"HTTP_{status}", f"Graph returned HTTP {status}: {payload!r}"
    details = {"status": status, "graphCode": code}
    if request_id:
        details["requestId"] = request_id
    return GraphMailError("GRAPH_ERROR", message, details=details)


def network_error(exc: Exception) -> GraphMailError:
    return GraphMailError("NETWORK_ERROR", f"Could not reach Microsoft Graph: {exc}")

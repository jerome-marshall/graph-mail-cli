"""``graph-mail`` command-line entrypoint.

Read-only by construction: there are no send, reply, move, or delete commands.
Output flags (``--json`` default, ``--ndjson``, ``--toon``) live on each leaf
command. Run ``graph-mail doctor`` once at the start of a session.
"""

from __future__ import annotations

import argparse
import platform
import sys
from datetime import datetime, timedelta

from . import __version__, auth, cal
from .client import GraphClient
from .envelope import JSON, NDJSON, TOON, emit_error, emit_success
from .errors import GraphMailError, auth_required
from .mail import cmd_list as mail_list
from .mail import cmd_mailboxes, cmd_read, cmd_search


def _output_parent() -> argparse.ArgumentParser:
    out = argparse.ArgumentParser(add_help=False)
    group = out.add_mutually_exclusive_group()
    group.add_argument("--json", dest="fmt", action="store_const", const=JSON,
                       help="JSON envelope (default; pretty on a TTY)")
    group.add_argument("--ndjson", dest="fmt", action="store_const", const=NDJSON,
                       help="newline-delimited JSON (one item per line for lists)")
    group.add_argument("--toon", dest="fmt", action="store_const", const=TOON,
                       help="TOON encoding (token-efficient, for LLM prompts)")
    out.set_defaults(fmt=JSON)
    return out


def _page_parent() -> argparse.ArgumentParser:
    page = argparse.ArgumentParser(add_help=False)
    page.add_argument("--limit", type=int, default=50, help="max results (default 50)")
    page.add_argument("--offset", type=int, default=0, help="results to skip")
    return page


def _account(client: GraphClient) -> dict | None:
    me = client.get("/me", {"$select": "displayName,mail,userPrincipalName"})
    if not me:
        return None
    return {
        "displayName": me.get("displayName"),
        "mail": me.get("mail"),
        "userPrincipalName": me.get("userPrincipalName"),
    }


def _mail_health(client: GraphClient) -> dict:
    try:
        inbox = client.get(
            "/me/mailFolders/inbox",
            {"$select": "totalItemCount,unreadItemCount"},
        )
    except GraphMailError as exc:
        return {"ok": False, "error": {"code": exc.code, "message": exc.message}}
    return {
        "ok": True,
        "inboxUnread": inbox.get("unreadItemCount"),
        "inboxTotal": inbox.get("totalItemCount"),
    }


def _calendar_health(client: GraphClient) -> dict:
    start = datetime.now().astimezone()
    try:
        body = client.get(
            "/me/calendarView",
            {
                "startDateTime": cal.graph_dt(start),
                "endDateTime": cal.graph_dt(start + timedelta(days=7)),
                "$top": "1",
                "$count": "true",
                "$select": "id",
            },
        )
    except GraphMailError as exc:
        return {"ok": False, "error": {"code": exc.code, "message": exc.message}}
    total = body.get("@odata.count")
    if total is None:
        total = len(body.get("value") or [])
    return {"ok": True, "eventsNext7Days": total}


def _cmd_version(args) -> dict:
    return {"name": "graph-mail", "version": __version__}


def _cmd_doctor(args) -> dict:
    state = auth.status()
    if not state.get("authenticated"):
        raise auth_required("no token stored")

    client = GraphClient()
    try:
        account = _account(client)
    except GraphMailError:
        account = None
    return {
        "graphMailVersion": __version__,
        "python": platform.python_version(),
        "platform": {"system": platform.system(), "release": platform.release()},
        "auth": state,
        "account": account,
        "mail": _mail_health(client),
        "calendar": _calendar_health(client),
    }


def _cmd_auth_login(args) -> dict:
    # Sign-in instructions go to stderr so stdout stays a single JSON envelope.
    echo = lambda msg: print(msg, file=sys.stderr)  # noqa: E731
    if getattr(args, "device_code", False):
        auth.login_device_code(echo=echo)
    else:
        auth.login(echo=echo)
    client = GraphClient()
    try:
        account = _account(client)
    except GraphMailError:
        account = None
    if account:
        # Persist the identity so `auth status` and `doctor` need no network.
        tokens = auth.load_tokens() or {}
        tokens["account"] = account.get("mail") or account.get("userPrincipalName")
        auth.save_tokens(tokens)
    return {
        "authenticated": True,
        "account": account,
        "backend": auth.storage_backend(),
    }


def _cmd_auth_status(args) -> dict:
    return auth.status()


def _cmd_auth_logout(args) -> dict:
    auth.clear_tokens()
    return {"authenticated": False, "backend": auth.storage_backend()}


def build_parser() -> argparse.ArgumentParser:
    out = _output_parent()
    page = _page_parent()

    parser = argparse.ArgumentParser(
        prog="graph-mail",
        description="Read-only, JSON-first CLI over Microsoft 365 mail and calendar.",
    )
    parser.add_argument("--version", action="version", version=f"graph-mail {__version__}")
    parser.set_defaults(func=None)
    top = parser.add_subparsers(dest="group")

    # ---- top level ----
    pv = top.add_parser("version", parents=[out], help="print version")
    pv.set_defaults(func=_cmd_version)
    pd = top.add_parser("doctor", parents=[out], help="health check (run once per session)")
    pd.set_defaults(func=_cmd_doctor)

    # ---- auth ----
    a = top.add_parser("auth", help="sign in and inspect the stored token")
    a.set_defaults(func=None)
    asub = a.add_subparsers(dest="cmd")

    a_login = asub.add_parser("login", parents=[out], help="sign in with the browser (PKCE)")
    a_login.add_argument(
        "--device-code",
        dest="device_code",
        action="store_true",
        help="show a code to enter on another device (headless hosts)",
    )
    a_login.set_defaults(func=_cmd_auth_login)
    a_status = asub.add_parser("status", parents=[out], help="show stored token state")
    a_status.set_defaults(func=_cmd_auth_status)
    a_logout = asub.add_parser("logout", parents=[out], help="delete the stored token")
    a_logout.set_defaults(func=_cmd_auth_logout)

    # ---- mail ----
    mail = top.add_parser("mail", help="read mail")
    mail.set_defaults(func=None)
    msub = mail.add_subparsers(dest="cmd")

    m_list = msub.add_parser("list", parents=[out, page], help="list messages (lightweight)")
    m_list.add_argument("--mailbox", help="mailbox id or name substring")
    m_list.add_argument("--after", help="ISO datetime (naked=local, Z=UTC)")
    m_list.add_argument("--before", help="ISO datetime (naked=local, Z=UTC)")
    m_list.add_argument("--unread", action="store_true", help="only unread")
    m_list.set_defaults(func=mail_list)

    m_search = msub.add_parser("search", parents=[out, page],
                               help="search subject/body/participants (lightweight)")
    m_search.add_argument("query", help="text to match")
    m_search.add_argument("--mailbox", help="mailbox id or name substring")
    m_search.add_argument("--after", help="ISO datetime (naked=local, Z=UTC)")
    m_search.add_argument("--before", help="ISO datetime (naked=local, Z=UTC)")
    m_search.add_argument("--unread", action="store_true", help="only unread")
    m_search.set_defaults(func=cmd_search)

    m_read = msub.add_parser("read", parents=[out], help="full message with body")
    m_read.add_argument("id", help="message id from list/search")
    m_read.set_defaults(func=cmd_read)

    m_get = msub.add_parser("get", parents=[out], help="alias of read")
    m_get.add_argument("id", help="message id from list/search")
    m_get.set_defaults(func=cmd_read)

    m_mb = msub.add_parser("mailboxes", parents=[out], help="list mailboxes/folders")
    m_mb.set_defaults(func=cmd_mailboxes)

    # ---- cal ----
    cal_group = top.add_parser("cal", help="read calendar")
    cal_group.set_defaults(func=None)
    csub = cal_group.add_subparsers(dest="cmd")

    c_cals = csub.add_parser("calendars", parents=[out], help="list calendars")
    c_cals.set_defaults(func=cal.cmd_calendars)

    c_list = csub.add_parser("list", parents=[out], help="list events in a window (lightweight)")
    c_list.add_argument("--start", help="ISO datetime (naked=local)")
    c_list.add_argument("--end", help="ISO datetime (naked=local)")
    c_list.add_argument("--days", type=int, default=7,
                        help="from start through N days ahead (default 7)")
    c_list.add_argument("--calendar", help="calendar name substring")
    c_list.add_argument("--limit", type=int, default=50, help="max events (default 50)")
    c_list.set_defaults(func=cal.cmd_list)

    c_get = csub.add_parser("get", parents=[out], help="full event with body")
    c_get.add_argument("id", help="event id from cal list")
    c_get.set_defaults(func=cal.cmd_get)

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    fmt = getattr(args, "fmt", JSON)
    func = getattr(args, "func", None)

    if func is None:
        parser.print_help(sys.stderr)
        return 2

    try:
        data = func(args)
    except GraphMailError as exc:
        emit_error(exc, fmt)
        return exc.exit_code
    except BrokenPipeError:
        return 0
    except KeyboardInterrupt:
        return 130
    except Exception as exc:  # noqa: BLE001 - last-resort envelope, never a traceback
        emit_error(GraphMailError("INTERNAL_ERROR", f"{type(exc).__name__}: {exc}"), fmt)
        return 1

    emit_success(data, fmt)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import json

import pytest

from graph_mail import cli
from graph_mail.errors import GraphMailError

LEAVES = [
    ["version"],
    ["doctor"],
    ["auth", "login"],
    ["auth", "status"],
    ["auth", "logout"],
    ["mail", "list"],
    ["mail", "search", "quarterly numbers"],
    ["mail", "read", "m1"],
    ["mail", "get", "m1"],
    ["mail", "mailboxes"],
    ["cal", "calendars"],
    ["cal", "list"],
    ["cal", "get", "e1"],
]


class AuthErrorClient:
    def get(self, path, params=None):
        raise GraphMailError("AUTH_REQUIRED", "no token stored")


class OkClient:
    def get(self, path, params=None):
        if path == "/me":
            return {
                "displayName": "Jerome Marshall",
                "mail": "jerome@example.com",
                "userPrincipalName": "jerome@corp.example",
            }
        if path == "/me/mailFolders/inbox":
            return {"totalItemCount": 4200, "unreadItemCount": 7}
        if path == "/me/calendarView":
            return {"@odata.count": 12, "value": [{"id": "e1"}]}
        raise AssertionError(f"unexpected path {path}")


@pytest.mark.parametrize("argv", LEAVES)
def test_every_leaf_command_wires_a_handler(argv):
    args = cli.build_parser().parse_args(argv)

    assert args.func is not None


def test_output_flags_default_to_json():
    assert cli.build_parser().parse_args(["mail", "list"]).fmt == "json"
    assert cli.build_parser().parse_args(["mail", "list", "--ndjson"]).fmt == "ndjson"
    assert cli.build_parser().parse_args(["mail", "list", "--toon"]).fmt == "toon"


def test_cal_list_defaults_to_a_seven_day_window():
    args = cli.build_parser().parse_args(["cal", "list"])

    assert args.days == 7
    assert args.start is None
    assert args.end is None
    assert args.calendar is None
    assert args.limit == 50


def test_doctor_requires_a_stored_token(monkeypatch):
    monkeypatch.setattr(cli.auth, "status", lambda: {"authenticated": False, "backend": "keychain"})

    with pytest.raises(GraphMailError) as excinfo:
        cli._cmd_doctor(None)

    assert excinfo.value.code == "AUTH_REQUIRED"


def test_doctor_reports_dead_grant_sections_without_raising(monkeypatch):
    monkeypatch.setattr(cli.auth, "status", lambda: {"authenticated": True, "backend": "file"})
    monkeypatch.setattr(cli, "GraphClient", AuthErrorClient)

    data = cli._cmd_doctor(None)

    assert data["auth"] == {"authenticated": True, "backend": "file"}
    assert data["account"] is None
    assert data["mail"] == {
        "ok": False,
        "error": {"code": "AUTH_REQUIRED", "message": "no token stored"},
    }
    assert data["calendar"]["ok"] is False
    assert data["calendar"]["error"]["code"] == "AUTH_REQUIRED"
    assert data["graphMailVersion"]
    assert data["python"]
    assert set(data["platform"]) == {"system", "release"}


def test_doctor_reports_mail_and_calendar_counts(monkeypatch):
    monkeypatch.setattr(cli.auth, "status", lambda: {"authenticated": True, "backend": "file"})
    monkeypatch.setattr(cli, "GraphClient", OkClient)

    data = cli._cmd_doctor(None)

    assert data["account"] == {
        "displayName": "Jerome Marshall",
        "mail": "jerome@example.com",
        "userPrincipalName": "jerome@corp.example",
    }
    assert data["mail"] == {"ok": True, "inboxUnread": 7, "inboxTotal": 4200}
    assert data["calendar"] == {"ok": True, "eventsNext7Days": 12}


def test_doctor_exits_3_without_a_token(monkeypatch, capsys):
    monkeypatch.setattr(cli.auth, "status", lambda: {"authenticated": False, "backend": "file"})

    assert cli.main(["doctor"]) == 3

    captured = capsys.readouterr()
    assert captured.out == ""
    envelope = json.loads(captured.err)
    assert envelope["ok"] is False
    assert envelope["error"]["code"] == "AUTH_REQUIRED"
    assert "graph-mail auth login" in envelope["error"]["message"]
    assert "Traceback" not in captured.err


def test_main_returns_2_when_no_command_is_given(capsys):
    assert cli.main([]) == 2
    assert "usage:" in capsys.readouterr().err

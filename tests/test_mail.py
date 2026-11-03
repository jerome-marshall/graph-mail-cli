from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from graph_mail import mail
from graph_mail.errors import GraphMailError

MESSAGE = {
    "id": "AAMk-message-1",
    "internetMessageId": "<abc@example.com>",
    "conversationId": "conv-1",
    "subject": "Q3 numbers",
    "bodyPreview": "Attached the numbers.",
    "receivedDateTime": "2026-11-03T16:53:50Z",
    "sentDateTime": "2026-11-03T16:53:00Z",
    "from": {"emailAddress": {"name": "Ada Lovelace", "address": "ada@example.com"}},
    "toRecipients": [{"emailAddress": {"name": "Jerome", "address": "jerome@example.com"}}],
    "ccRecipients": [{"emailAddress": {"address": "cc@example.com"}}],
    "isRead": False,
    "flag": {"flagStatus": "flagged"},
    "hasAttachments": True,
    "importance": "high",
    "webLink": "https://outlook.example/message/1",
    "parentFolderId": "folder-inbox",
}


class NoCalls:
    def paginate(self, *args, **kwargs):
        raise AssertionError("this lookup must not reach Graph")


class EmptyFolders:
    def paginate(self, *args, **kwargs):
        return [], False


class OneFolder:
    def paginate(self, path, params=None, **kwargs):
        if path == "/me/mailFolders":
            return [{"id": "f1", "displayName": "Projects"}], False
        return [], False


def test_to_item_maps_a_message():
    item = mail.to_item(MESSAGE, {"folder-inbox": "Inbox"})

    assert item["id"] == "AAMk-message-1"
    assert item["messageId"] == "<abc@example.com>"
    assert item["from"] == "Ada Lovelace <ada@example.com>"
    assert item["to"] == ["Jerome <jerome@example.com>"]
    assert item["cc"] == ["cc@example.com"]
    assert item["date"] == datetime(2026, 11, 3, 16, 53, 50, tzinfo=timezone.utc).astimezone().isoformat()
    assert item["preview"] == "Attached the numbers."
    assert item["isUnread"] is True
    assert item["isFlagged"] is True
    assert item["hasAttachments"] is True
    assert item["mailbox"] == "Inbox"


def test_to_item_treats_read_and_unflagged_as_false():
    message = {**MESSAGE, "isRead": True, "flag": {"flagStatus": "notFlagged"}}

    item = mail.to_item(message)

    assert item["isUnread"] is False
    assert item["isFlagged"] is False
    assert item["mailbox"] is None


def test_to_item_survives_an_empty_payload():
    item = mail.to_item({})

    assert item["from"] is None
    assert item["to"] == []
    assert item["cc"] == []
    assert item["date"] is None
    assert item["isUnread"] is False
    assert item["hasAttachments"] is False


def test_html_to_text_drops_markup_and_scripts():
    html = (
        "<html><head><style>p{color:red}</style></head><body>"
        "<p>Hello <b>there</b></p><script>alert(1)</script><p>Second line</p>"
        "</body></html>"
    )

    assert mail.html_to_text(html) == "Hello there\n\nSecond line"


def test_html_to_text_returns_plain_text_unchanged():
    assert mail.html_to_text("Just text") == "Just text"


def test_date_filter_builds_a_graph_filter():
    args = SimpleNamespace(
        after="2026-11-01T00:00:00Z",
        before="2026-11-30T23:59:59Z",
        unread=True,
    )

    assert mail._date_filter(args) == (
        "receivedDateTime ge 2026-11-01T00:00:00Z and "
        "receivedDateTime le 2026-11-30T23:59:59Z and isRead eq false"
    )


def test_date_filter_is_none_without_constraints():
    args = SimpleNamespace(after=None, before=None, unread=False)

    assert mail._date_filter(args) is None


def test_resolve_folder_well_known_names_need_no_request():
    cases = [
        ("inbox", "inbox"),
        ("SENT ITEMS", "sentitems"),
        ("trash", "deleteditems"),
        ("Junk Email", "junkemail"),
        ("Archive", "archive"),
    ]
    for name, segment in cases:
        assert mail.resolve_folder(NoCalls(), name) == (f"/me/mailFolders/{segment}/messages", None)

    assert mail.resolve_folder(NoCalls(), None) == ("/me/messages", None)


def test_resolve_folder_matches_a_name_substring():
    assert mail.resolve_folder(OneFolder(), "proj") == ("/me/mailFolders/f1/messages", "Projects")


def test_resolve_folder_reports_an_unknown_name():
    with pytest.raises(GraphMailError) as excinfo:
        mail.resolve_folder(EmptyFolders(), "Nope")

    assert excinfo.value.code == "NOT_FOUND"
    assert "Nope" in excinfo.value.message

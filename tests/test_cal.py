from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from graph_mail import cal, mail
from graph_mail.errors import GraphMailError

EVENT = {
    "id": "AAMk-event-1",
    "subject": "Design review",
    "start": {"dateTime": "2026-11-03T17:00:00.0000000", "timeZone": "UTC"},
    "end": {"dateTime": "2026-11-03T18:00:00.0000000", "timeZone": "UTC"},
    "isAllDay": False,
    "location": {"displayName": "Room 3"},
    "organizer": {"emailAddress": {"name": "Ada Lovelace", "address": "ada@example.com"}},
    "attendees": [
        {
            "type": "required",
            "status": {"response": "accepted"},
            "emailAddress": {"name": "Jerome", "address": "jerome@example.com"},
        },
        {
            "type": "optional",
            "status": {"response": "none"},
            "emailAddress": {"address": "guest@example.com"},
        },
    ],
    "isCancelled": False,
    "isOnlineMeeting": True,
    "onlineMeetingUrl": "https://teams.example/meet/1",
    "bodyPreview": "Walk through the calendar module.",
    "webLink": "https://outlook.example/event/1",
    "responseStatus": {"response": "accepted"},
    "seriesMasterId": None,
}

EVENT_KEYS = {
    "id",
    "eventId",
    "subject",
    "start",
    "end",
    "allDay",
    "calendar",
    "location",
    "organizer",
    "attendees",
    "isCancelled",
    "isOnlineMeeting",
    "onlineMeetingUrl",
    "preview",
    "webLink",
    "responseStatus",
    "seriesMasterId",
}


class FakeClient:
    def __init__(self, *, pages=None, calendars=None, gets=None):
        self.pages = pages if pages is not None else ([], False)
        self.calendars = calendars or []
        self.gets = gets or {}
        self.calls: list[tuple] = []

    def paginate(self, path, params=None, *, limit=50, offset=0):
        self.calls.append(("paginate", path, params))
        if path == "/me/calendars":
            return self.calendars, False
        return self.pages

    def get(self, path, params=None):
        self.calls.append(("get", path, params))
        return self.gets[path]


def test_to_event_has_the_documented_keys():
    assert set(cal.to_event(EVENT, "Work")) == EVENT_KEYS


def test_to_event_maps_a_graph_event():
    item = cal.to_event(EVENT, "Work")

    assert item["id"] == "AAMk-event-1"
    assert item["eventId"] == "AAMk-event-1"
    assert item["subject"] == "Design review"
    assert item["start"] == datetime(2026, 11, 3, 17, 0, tzinfo=timezone.utc).astimezone().isoformat()
    assert item["end"] == datetime(2026, 11, 3, 18, 0, tzinfo=timezone.utc).astimezone().isoformat()
    assert item["allDay"] is False
    assert item["calendar"] == "Work"
    assert item["location"] == "Room 3"
    assert item["organizer"] == "Ada Lovelace <ada@example.com>"
    assert item["attendees"] == [
        {
            "name": "Jerome",
            "address": "jerome@example.com",
            "type": "required",
            "response": "accepted",
        },
        {"name": None, "address": "guest@example.com", "type": "optional", "response": "none"},
    ]
    assert item["isCancelled"] is False
    assert item["isOnlineMeeting"] is True
    assert item["onlineMeetingUrl"] == "https://teams.example/meet/1"
    assert item["preview"] == "Walk through the calendar module."
    assert item["webLink"] == "https://outlook.example/event/1"
    assert item["responseStatus"] == "accepted"
    assert item["seriesMasterId"] is None


def test_to_event_converts_a_windows_time_zone_name():
    try:
        ZoneInfo("Asia/Kolkata")
    except ZoneInfoNotFoundError:
        pytest.skip("no tzdata for Asia/Kolkata")

    event = {
        **EVENT,
        "start": {"dateTime": "2026-11-03T09:30:00.0000000", "timeZone": "India Standard Time"},
    }

    assert cal.to_event(event)["start"] == datetime(
        2026, 11, 3, 4, 0, tzinfo=timezone.utc
    ).astimezone().isoformat()


def test_to_event_truncates_seven_fractional_digits():
    event = {
        **EVENT,
        "start": {"dateTime": "2026-11-03T17:00:00.1234567", "timeZone": "UTC"},
    }

    assert cal.to_event(event)["start"] == datetime(
        2026, 11, 3, 17, 0, 0, 123456, tzinfo=timezone.utc
    ).astimezone().isoformat()


def test_to_event_falls_back_to_utc_for_an_unknown_zone():
    event = {
        **EVENT,
        "start": {"dateTime": "2026-11-03T17:00:00", "timeZone": "Some Custom Zone"},
    }

    assert cal.to_event(event)["start"] == datetime(
        2026, 11, 3, 17, 0, tzinfo=timezone.utc
    ).astimezone().isoformat()


def test_to_event_keeps_a_datetime_that_already_has_an_offset():
    event = {
        **EVENT,
        "start": {"dateTime": "2026-11-03T09:00:00+01:00", "timeZone": "W. Europe Standard Time"},
    }

    assert cal.to_event(event)["start"] == datetime(
        2026, 11, 3, 8, 0, tzinfo=timezone.utc
    ).astimezone().isoformat()


def test_to_event_keeps_the_all_day_date():
    event = {
        **EVENT,
        "isAllDay": True,
        "start": {"dateTime": "2026-11-04T00:00:00.0000000", "timeZone": "UTC"},
        "end": {"dateTime": "2026-11-05T00:00:00.0000000", "timeZone": "UTC"},
    }

    item = cal.to_event(event)

    assert item["allDay"] is True
    assert item["start"] == datetime(2026, 11, 4, 0, 0).astimezone().isoformat()
    assert item["end"] == datetime(2026, 11, 5, 0, 0).astimezone().isoformat()


def test_window_defaults_to_now_through_seven_days():
    before = datetime.now().astimezone()
    start, end = cal._window(SimpleNamespace(start=None, end=None, days=7))

    assert timedelta(0) <= start - before < timedelta(seconds=5)
    assert end - start == timedelta(days=7)


def test_window_starts_at_the_given_time_and_counts_days():
    start, end = cal._window(SimpleNamespace(start="2026-11-03T09:00:00", end=None, days=2))

    assert start.replace(tzinfo=None) == datetime(2026, 11, 3, 9, 0)
    assert end - start == timedelta(days=2)


def test_window_honours_an_explicit_end():
    start, end = cal._window(
        SimpleNamespace(start="2026-11-03T00:00:00", end="2026-11-04T12:00:00", days=9)
    )

    assert start.replace(tzinfo=None) == datetime(2026, 11, 3, 0, 0)
    assert end.replace(tzinfo=None) == datetime(2026, 11, 4, 12, 0)


def test_window_rejects_an_inverted_range():
    with pytest.raises(GraphMailError) as excinfo:
        cal._window(
            SimpleNamespace(start="2026-11-04T00:00:00", end="2026-11-03T00:00:00", days=7)
        )

    assert excinfo.value.code == "VALIDATION_ERROR"


def test_cmd_list_asks_graph_for_the_requested_window(monkeypatch):
    fake = FakeClient(pages=([EVENT], False))
    monkeypatch.setattr(cal, "GraphClient", lambda: fake)
    args = SimpleNamespace(
        start="2026-11-03T09:00:00Z", end=None, days=2, calendar=None, limit=25
    )

    data = cal.cmd_list(args)

    path, params = fake.calls[0][1], fake.calls[0][2]
    assert path == "/me/calendarView"
    assert params["startDateTime"] == "2026-11-03T09:00:00Z"
    assert params["endDateTime"] == "2026-11-05T09:00:00Z"
    assert params["$orderby"] == "start/dateTime asc"
    assert params["$top"] == "100"
    assert params["$select"] == cal.LIST_SELECT
    assert data["window"] == {
        "start": mail.local_iso("2026-11-03T09:00:00Z"),
        "end": mail.local_iso("2026-11-05T09:00:00Z"),
    }
    assert data["calendar"] is None
    assert data["items"][0]["calendar"] is None
    assert data["items"][0]["subject"] == "Design review"
    assert data["count"] == 1
    assert data["hasMore"] is False
    assert data["source"] == "graph"


def test_cmd_list_scopes_to_a_calendar_name_substring(monkeypatch):
    fake = FakeClient(
        pages=([EVENT], False),
        calendars=[{"id": "cal-1", "name": "Work"}, {"id": "cal-2", "name": "Personal"}],
    )
    monkeypatch.setattr(cal, "GraphClient", lambda: fake)
    args = SimpleNamespace(
        start="2026-11-03T00:00:00Z", end="2026-11-03T23:00:00Z", days=7,
        calendar="work", limit=10,
    )

    data = cal.cmd_list(args)

    view_paths = [call[1] for call in fake.calls if call[1].endswith("/calendarView")]
    assert view_paths == ["/me/calendars/cal-1/calendarView"]
    assert data["calendar"] == "Work"
    assert data["items"][0]["calendar"] == "Work"


def test_cmd_list_reports_the_available_calendars(monkeypatch):
    fake = FakeClient(
        calendars=[{"id": "cal-1", "name": "Work"}, {"id": "cal-2", "name": "Personal"}]
    )
    monkeypatch.setattr(cal, "GraphClient", lambda: fake)
    args = SimpleNamespace(
        start="2026-11-03T00:00:00Z", end=None, days=7, calendar="Team", limit=10
    )

    with pytest.raises(GraphMailError) as excinfo:
        cal.cmd_list(args)

    assert excinfo.value.code == "NOT_FOUND"
    assert "Work" in excinfo.value.message
    assert "Personal" in excinfo.value.message


def test_cmd_calendars_maps_owner_and_flags(monkeypatch):
    fake = FakeClient(
        calendars=[
            {
                "id": "cal-1",
                "name": "Work",
                "color": "lightBlue",
                "isDefaultCalendar": True,
                "canEdit": False,
                "owner": {"name": "Ada Lovelace", "address": "ada@example.com"},
            }
        ]
    )
    monkeypatch.setattr(cal, "GraphClient", lambda: fake)

    data = cal.cmd_calendars(None)

    assert data["items"] == [
        {
            "id": "cal-1",
            "name": "Work",
            "color": "lightBlue",
            "isDefaultCalendar": True,
            "canEdit": False,
            "ownerName": "Ada Lovelace",
        }
    ]
    assert data["count"] == 1
    assert data["source"] == "graph"


def test_cmd_get_returns_the_event_body(monkeypatch):
    fake = FakeClient(gets={
        "/me/events/e1": {
            **EVENT,
            "body": {"contentType": "html", "content": "<p>Agenda</p><p>Decisions</p>"},
        }
    })
    monkeypatch.setattr(cal, "GraphClient", lambda: fake)

    data = cal.cmd_get(SimpleNamespace(id="e1"))

    assert data["subject"] == "Design review"
    assert data["bodyContentType"] == "html"
    assert data["body"] == "Agenda\n\nDecisions"
    assert data["bodyAvailable"] is True
    select = fake.calls[0][2]["$select"]
    assert "body" in select
    assert "attendees" in select

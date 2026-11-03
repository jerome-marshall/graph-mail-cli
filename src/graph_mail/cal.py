"""Calendar reads over Microsoft Graph.

Same contract as ``mail``: every command returns plain dicts, only GET requests
are issued, and nothing here can mutate a calendar.
"""

from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import mail
from .client import GraphClient
from .errors import not_found, validation
from .timeparse import parse_dt

CALENDAR_SELECT = "id,name,color,isDefaultCalendar,canEdit,owner"
LIST_SELECT = (
    "id,subject,start,end,isAllDay,location,organizer,attendees,isCancelled,"
    "isOnlineMeeting,onlineMeetingUrl,bodyPreview,webLink,responseStatus,seriesMasterId"
)

_FRACTION = re.compile(r"(\.\d{6})\d+")

# Graph reports event times in the mailbox's Windows time zone name. Map the
# common ones (plus IANA names) so a 09:00 meeting does not become 01:00 local.
WINDOWS_ZONES = {
    "UTC": "UTC",
    "Alaskan Standard Time": "America/Anchorage",
    "Atlantic Standard Time": "America/Halifax",
    "Central Standard Time": "America/Chicago",
    "Central Europe Standard Time": "Europe/Budapest",
    "Central European Standard Time": "Europe/Warsaw",
    "China Standard Time": "Asia/Shanghai",
    "E. Australia Standard Time": "Australia/Brisbane",
    "E. Europe Standard Time": "Europe/Chisinau",
    "E. South America Standard Time": "America/Sao_Paulo",
    "Eastern Standard Time": "America/New_York",
    "Egypt Standard Time": "Africa/Cairo",
    "FLE Standard Time": "Europe/Kiev",
    "GMT Standard Time": "Europe/London",
    "GTB Standard Time": "Europe/Bucharest",
    "Greenwich Standard Time": "Atlantic/Reykjavik",
    "Hawaiian Standard Time": "Pacific/Honolulu",
    "India Standard Time": "Asia/Kolkata",
    "Iran Standard Time": "Asia/Tehran",
    "Israel Standard Time": "Asia/Jerusalem",
    "Korea Standard Time": "Asia/Seoul",
    "Mountain Standard Time": "America/Denver",
    "Nepal Standard Time": "Asia/Kathmandu",
    "New Zealand Standard Time": "Pacific/Auckland",
    "Newfoundland Standard Time": "America/St_Johns",
    "Pakistan Standard Time": "Asia/Karachi",
    "Romance Standard Time": "Europe/Paris",
    "Russian Standard Time": "Europe/Moscow",
    "SA Pacific Standard Time": "America/Bogota",
    "SE Asia Standard Time": "Asia/Bangkok",
    "Singapore Standard Time": "Asia/Singapore",
    "South Africa Standard Time": "Africa/Johannesburg",
    "Sri Lanka Standard Time": "Asia/Colombo",
    "Taipei Standard Time": "Asia/Taipei",
    "Tokyo Standard Time": "Asia/Tokyo",
    "Turkey Standard Time": "Europe/Istanbul",
    "US Mountain Standard Time": "America/Phoenix",
    "W. Australia Standard Time": "Australia/Perth",
    "W. Europe Standard Time": "Europe/Berlin",
}


# --------------------------------------------------------------------------- #
# Formatting helpers
# --------------------------------------------------------------------------- #

def graph_dt(value: datetime) -> str:
    """Format an aware datetime the way Graph's calendarView params expect."""
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _zone(name: str | None):
    if not name:
        return timezone.utc
    for candidate in (name, WINDOWS_ZONES.get(name)):
        if not candidate:
            continue
        try:
            return ZoneInfo(candidate)
        except (ZoneInfoNotFoundError, ValueError):
            continue
    return timezone.utc


def _event_time(value: dict | None, all_day: bool = False) -> str | None:
    """Graph sends a naive ``dateTime`` plus a separate zone name."""
    if not value:
        return None
    # Graph always pads to 7 fractional digits; keep the 6 that Python parses.
    raw = _FRACTION.sub(r"\1", (value.get("dateTime") or "").strip())
    if not raw:
        return None
    if all_day and len(raw) >= 10:
        # Graph anchors an all-day event at midnight UTC, which would land on the
        # previous day once converted. Keep the calendar date instead.
        try:
            day = date.fromisoformat(raw[:10])
        except ValueError:
            return raw
        return datetime.combine(day, time.min).astimezone().isoformat()
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return raw
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_zone(value.get("timeZone")))
    return dt.astimezone().isoformat()


def _attendees(entries: list | None) -> list[dict]:
    out = []
    for entry in entries or []:
        email = entry.get("emailAddress") or {}
        if not (email.get("address") or email.get("name")):
            continue
        out.append(
            {
                "name": email.get("name"),
                "address": email.get("address"),
                "type": entry.get("type"),
                "response": (entry.get("status") or {}).get("response"),
            }
        )
    return out


# --------------------------------------------------------------------------- #
# Item mapping
# --------------------------------------------------------------------------- #

def to_event(event: dict, calendar: str | None = None) -> dict:
    location = (event.get("location") or {}).get("displayName")
    all_day = bool(event.get("isAllDay"))
    return {
        "id": event.get("id"),
        "eventId": event.get("id"),
        "subject": event.get("subject"),
        "start": _event_time(event.get("start"), all_day),
        "end": _event_time(event.get("end"), all_day),
        "allDay": all_day,
        "calendar": calendar,
        "location": location or None,
        "organizer": mail._address(event.get("organizer")),
        "attendees": _attendees(event.get("attendees")),
        "attendeeCount": len(event.get("attendees") or []),
        "isRecurring": bool(event.get("seriesMasterId")),
        "isCancelled": bool(event.get("isCancelled")),
        "isOnlineMeeting": bool(event.get("isOnlineMeeting")),
        "onlineMeetingUrl": event.get("onlineMeetingUrl"),
        "preview": event.get("bodyPreview"),
        "webLink": event.get("webLink"),
        "responseStatus": (event.get("responseStatus") or {}).get("response"),
        "seriesMasterId": event.get("seriesMasterId"),
    }


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #

def _window(args) -> tuple[datetime, datetime]:
    start = parse_dt(args.start) if args.start else datetime.now().astimezone()
    end = parse_dt(args.end) if args.end else start + timedelta(days=args.days or 7)
    if end <= start:
        raise validation("--end must be later than --start")
    return start, end


def _calendar_rows(client: GraphClient) -> list[dict]:
    rows, _ = client.paginate(
        "/me/calendars",
        {"$top": "100", "$select": CALENDAR_SELECT},
        limit=200,
    )
    return rows


def _resolve_calendar(client: GraphClient, name: str | None) -> dict | None:
    """Match one calendar by case-insensitive substring of its name."""
    if not name:
        return None
    rows = _calendar_rows(client)
    key = name.strip().lower()
    matches = [c for c in rows if key in (c.get("name") or "").lower()]
    if not matches:
        available = ", ".join(sorted(c.get("name") or "" for c in rows)) or "none"
        raise not_found(f"no calendar matching {name!r}; available calendars: {available}")
    exact = [c for c in matches if (c.get("name") or "").lower() == key]
    return (exact or matches)[0]


def cmd_calendars(args) -> dict:
    client = GraphClient()
    rows = _calendar_rows(client)
    items = [
        {
            "id": c.get("id"),
            "name": c.get("name"),
            "color": c.get("color"),
            "isDefaultCalendar": bool(c.get("isDefaultCalendar")),
            "canEdit": bool(c.get("canEdit")),
            "ownerName": (c.get("owner") or {}).get("name"),
        }
        for c in rows
    ]
    return {"items": items, "count": len(items), "hasMore": False, "source": "graph"}


def cmd_list(args) -> dict:
    client = GraphClient()
    start, end = _window(args)
    calendar = _resolve_calendar(client, args.calendar)
    path = (
        f"/me/calendars/{calendar['id']}/calendarView"
        if calendar
        else "/me/calendarView"
    )
    params = {
        "startDateTime": graph_dt(start),
        "endDateTime": graph_dt(end),
        "$orderby": "start/dateTime asc",
        "$top": "100",
        "$select": LIST_SELECT,
    }
    rows, has_more = client.paginate(path, params, limit=args.limit)
    name = calendar.get("name") if calendar else None
    return {
        "items": [to_event(e, name) for e in rows],
        "count": len(rows),
        "hasMore": has_more,
        "window": {"start": mail.local_iso(graph_dt(start)), "end": mail.local_iso(graph_dt(end))},
        "calendar": name,
        "source": "graph",
    }


def cmd_get(args) -> dict:
    client = GraphClient()
    event = client.get(f"/me/events/{args.id}", {"$select": LIST_SELECT + ",body"})
    if not event:
        raise not_found(f"no event with id {args.id}")

    item = to_event(event)
    body = event.get("body") or {}
    content, content_type = body.get("content") or "", body.get("contentType") or "text"
    item.update(
        {
            "bodyContentType": content_type,
            "body": mail.html_to_text(content) if content_type.lower() == "html" else content,
            "bodyAvailable": bool(content),
        }
    )
    return item

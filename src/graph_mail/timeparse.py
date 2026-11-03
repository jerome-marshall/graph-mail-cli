"""Date/time parsing with the same semantics as ``olk`` and ``apple-mail``.

Rules (matching the outlook-cli contract so agents can reuse muscle memory):

- **Naked ISO** (``2026-06-24T00:00:00``) -> interpreted as **local** time in the
  machine's current timezone. This is what you want for "today"/"tomorrow".
- **Trailing Z** (``...Z``) -> UTC.
- **Explicit offset** (``...+05:30``) -> that offset.

Plain ``YYYY-MM-DD`` is accepted and treated as local midnight.
Everything returns a timezone-aware ``datetime`` (and a helper gives epoch secs).
"""

from __future__ import annotations

from datetime import date, datetime, time, timezone

from .errors import validation


def local_tz():
    """The machine's current local timezone as a tzinfo."""
    return datetime.now().astimezone().tzinfo


def parse_dt(value: str) -> datetime:
    """Parse an ISO 8601 string into an aware datetime using olk semantics."""
    if value is None:
        raise validation("expected a date/time string, got nothing")
    s = value.strip()
    if not s:
        raise validation("expected a date/time string, got empty string")

    # Bare calendar date -> local midnight.
    if len(s) == 10 and s[4] == "-" and s[7] == "-":
        try:
            d = date.fromisoformat(s)
        except ValueError as exc:
            raise validation(f"invalid date {value!r}: {exc}") from None
        return datetime.combine(d, time(0, 0, 0), tzinfo=local_tz())

    try:
        dt = datetime.fromisoformat(s)
    except ValueError as exc:
        raise validation(
            f"invalid ISO datetime {value!r}: {exc}. "
            "Use forms like 2026-06-24, 2026-06-24T09:00:00 (local), "
            "2026-06-24T09:00:00Z (UTC), or 2026-06-24T09:00:00+05:30."
        ) from None

    if dt.tzinfo is None:
        # Naked ISO -> local time.
        dt = dt.replace(tzinfo=local_tz())
    return dt


def to_epoch(value: str) -> float:
    """Parse and return POSIX seconds (UTC)."""
    return parse_dt(value).timestamp()


# Apple Core Foundation absolute time epoch (2001-01-01) in Unix seconds.
_CF_EPOCH = 978307200


def store_to_unix(ts) -> float | None:
    """Normalise a timestamp pulled from the Envelope Index to Unix seconds.

    Apple Mail generally stores ``date_received``/``date_sent`` as Unix seconds,
    but some columns/versions use CoreFoundation absolute time (seconds since
    2001). Values below ~1e9 in this era are almost certainly CF time, so we add
    the CF epoch to bring them back to Unix.
    """
    if ts is None:
        return None
    try:
        f = float(ts)
    except (TypeError, ValueError):
        return None
    if f <= 0:
        return None
    if f < 1_000_000_000:  # < 2001-09; today's Unix time is ~1.7e9
        f += _CF_EPOCH
    return f


def unix_to_local_iso(ts) -> str | None:
    """Format Unix seconds as an ISO 8601 string in local time."""
    if ts is None:
        return None
    return datetime.fromtimestamp(float(ts), tz=timezone.utc).astimezone().isoformat()


def store_to_local_iso(ts) -> str | None:
    """Convenience: Envelope-Index timestamp -> local ISO string."""
    return unix_to_local_iso(store_to_unix(ts))

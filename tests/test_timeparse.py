from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from graph_mail.errors import GraphMailError
from graph_mail.timeparse import parse_dt


def test_naked_datetime_is_local_wall_clock():
    dt = parse_dt("2026-06-24T09:00:00")

    assert dt.tzinfo is not None
    assert dt.replace(tzinfo=None) == datetime(2026, 6, 24, 9, 0)


def test_trailing_z_is_utc():
    assert parse_dt("2026-06-24T09:00:00Z") == datetime(2026, 6, 24, 9, 0, tzinfo=timezone.utc)


def test_explicit_offset_is_kept():
    dt = parse_dt("2026-06-24T09:00:00+05:30")

    assert dt.utcoffset() == timedelta(hours=5, minutes=30)
    assert dt == datetime(2026, 6, 24, 3, 30, tzinfo=timezone.utc)


def test_bare_date_is_local_midnight():
    dt = parse_dt("2026-06-24")

    assert dt.tzinfo is not None
    assert dt.replace(tzinfo=None) == datetime(2026, 6, 24, 0, 0)


def test_empty_value_is_a_validation_error():
    with pytest.raises(GraphMailError) as excinfo:
        parse_dt("   ")

    assert excinfo.value.code == "VALIDATION_ERROR"


def test_unparseable_value_is_a_validation_error():
    with pytest.raises(GraphMailError) as excinfo:
        parse_dt("next tuesday")

    assert excinfo.value.code == "VALIDATION_ERROR"

from __future__ import annotations

import io
import json

from graph_mail.envelope import JSON, NDJSON, TOON, emit_error, emit_success, list_payload
from graph_mail.errors import GraphMailError


def test_list_payload_has_the_documented_shape():
    payload = list_payload([{"id": "a"}, {"id": "b"}], has_more=True)

    assert payload == {
        "items": [{"id": "a"}, {"id": "b"}],
        "count": 2,
        "hasMore": True,
    }


def test_list_payload_adds_the_optional_total():
    assert list_payload([], total=0)["total"] == 0
    assert "total" not in list_payload([])


def test_json_envelope_wraps_the_data():
    stream = io.StringIO()
    emit_success({"mailbox": "Inbox"}, JSON, stream)

    assert json.loads(stream.getvalue()) == {"ok": True, "data": {"mailbox": "Inbox"}}


def test_ndjson_emits_one_line_per_list_item():
    stream = io.StringIO()
    emit_success(list_payload([{"id": "a"}, {"id": "b"}]), NDJSON, stream)

    assert [json.loads(line) for line in stream.getvalue().splitlines()] == [{"id": "a"}, {"id": "b"}]


def test_ndjson_keeps_the_envelope_for_a_single_object():
    stream = io.StringIO()
    emit_success({"mailbox": "Inbox"}, NDJSON, stream)

    assert json.loads(stream.getvalue()) == {"ok": True, "data": {"mailbox": "Inbox"}}


def test_toon_encodes_the_whole_envelope():
    stream = io.StringIO()
    emit_success({"count": 2}, TOON, stream)

    assert stream.getvalue().startswith("ok: true\n")


def test_error_envelope_carries_the_stable_code():
    stream = io.StringIO()
    emit_error(GraphMailError("AUTH_REQUIRED", "run `graph-mail auth login`"), JSON, stream)

    envelope = json.loads(stream.getvalue())
    assert envelope["ok"] is False
    assert envelope["error"]["code"] == "AUTH_REQUIRED"
    assert envelope["error"]["message"] == "run `graph-mail auth login`"

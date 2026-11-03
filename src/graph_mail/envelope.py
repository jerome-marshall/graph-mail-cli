"""Response envelope construction and output formatting.

Contract (same as ``apple-mail`` and ``olk`` so refs and skills can be reused):

- success scalar : ``{"ok": true, "data": <object>}``
- success list   : ``{"ok": true, "data": {"items": [...], "count": N, "hasMore": bool}}``
- error          : ``{"ok": false, "error": {"code": "...", "message": "..."}}``

Output modes: ``--json`` (default; pretty on a TTY, compact otherwise),
``--ndjson`` (one object per line; for lists each item is its own line),
``--toon`` (token-efficient TOON encoding of the whole envelope).
"""

from __future__ import annotations

import json
import sys

from . import toon
from .errors import GraphMailError

JSON = "json"
NDJSON = "ndjson"
TOON = "toon"


def list_payload(items: list, *, has_more: bool = False, total: int | None = None) -> dict:
    """Build the standard list/search payload shape."""
    payload: dict = {"items": items, "count": len(items), "hasMore": has_more}
    if total is not None:
        payload["total"] = total
    return payload


def _is_list_payload(data) -> bool:
    return isinstance(data, dict) and "items" in data and isinstance(data["items"], list)


def emit_success(data, fmt: str = JSON, stream=None) -> None:
    stream = stream if stream is not None else sys.stdout
    envelope = {"ok": True, "data": data}

    if fmt == NDJSON:
        if _is_list_payload(data):
            for item in data["items"]:
                stream.write(json.dumps(item, ensure_ascii=False, default=str) + "\n")
        else:
            stream.write(json.dumps(envelope, ensure_ascii=False, default=str) + "\n")
        return

    if fmt == TOON:
        stream.write(toon.encode(envelope))
        return

    pretty = stream.isatty()
    if pretty:
        stream.write(json.dumps(envelope, ensure_ascii=False, indent=2, default=str) + "\n")
    else:
        stream.write(
            json.dumps(envelope, ensure_ascii=False, separators=(",", ":"), default=str) + "\n"
        )


def emit_error(err: GraphMailError, fmt: str = JSON, stream=None) -> None:
    stream = stream if stream is not None else sys.stderr
    envelope = {"ok": False, "error": err.to_dict()}

    if fmt == TOON:
        stream.write(toon.encode(envelope))
        return

    pretty = stream.isatty()
    if pretty:
        stream.write(json.dumps(envelope, ensure_ascii=False, indent=2, default=str) + "\n")
    else:
        stream.write(
            json.dumps(envelope, ensure_ascii=False, separators=(",", ":"), default=str) + "\n"
        )

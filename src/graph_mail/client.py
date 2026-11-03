"""Thin Microsoft Graph v1.0 client (stdlib only).

Handles the three things every caller would otherwise repeat: bearer auth with a
transparent refresh-and-retry on 401, throttling (429/503 with ``Retry-After``),
and following ``@odata.nextLink`` until a caller-supplied limit is reached.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request

from . import auth, config
from .errors import GraphMailError, graph_error, network_error

RETRY_STATUSES = {429, 500, 502, 503, 504}
MAX_RETRIES = 3


class GraphClient:
    def __init__(self, timeout: int = 45):
        self.timeout = timeout

    # -- low level --------------------------------------------------------- #
    def _request(self, url: str, token: str) -> tuple[int, dict, dict]:
        req = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
                "User-Agent": "graph-mail/0.1",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode()
                return resp.status, json.loads(raw) if raw else {}, dict(resp.headers)
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode(errors="replace")
            try:
                payload = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                payload = {"error": {"code": f"HTTP_{exc.code}", "message": raw}}
            return exc.code, payload, dict(exc.headers or {})
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            raise network_error(exc) from None

    def get(self, path: str, params: dict | None = None) -> dict:
        """GET a Graph path (or absolute nextLink) and return the parsed body."""
        url = path if path.startswith("http") else self._url(path, params)
        token = auth.access_token()

        for attempt in range(MAX_RETRIES + 1):
            status, payload, headers = self._request(url, token)
            if status == 200:
                return payload
            if status == 401 and attempt == 0:
                token = auth.access_token(force_refresh=True)
                continue
            if status in RETRY_STATUSES and attempt < MAX_RETRIES:
                delay = _retry_after(headers)
                time.sleep(delay)
                continue
            raise graph_error(status, payload, request_id=headers.get("request-id"))
        raise GraphMailError("GRAPH_ERROR", "Graph request failed after retries.")

    def paginate(self, path: str, params: dict | None = None, *, limit: int = 50,
                 offset: int = 0) -> tuple[list, bool]:
        """Collect ``limit`` items starting at ``offset``; returns (items, has_more).

        Graph does not support ``$skip`` on message collections, so paging happens
        here: walk ``@odata.nextLink`` until we have ``offset + limit`` items.
        """
        want = offset + limit
        items: list = []
        url = self._url(path, params)
        has_more = False
        while url and len(items) < want:
            body = self.get(url)
            batch = body.get("value", [])
            items.extend(batch)
            url = body.get("@odata.nextLink")
            has_more = bool(url)
            if not batch:
                break
        sliced = items[offset:offset + limit]
        return sliced, has_more or len(items) > offset + len(sliced)

    # -- helpers ----------------------------------------------------------- #
    @staticmethod
    def _url(path: str, params: dict | None) -> str:
        base = path if path.startswith("http") else config.GRAPH_BASE + path
        if not params:
            return base
        clean = {k: v for k, v in params.items() if v is not None}
        if not clean:
            return base
        joiner = "&" if "?" in base else "?"
        return f"{base}{joiner}{urllib.parse.urlencode(clean, quote_via=urllib.parse.quote)}"


def _retry_after(headers: dict) -> float:
    raw = headers.get("Retry-After") or headers.get("retry-after")
    try:
        return min(max(float(raw), 1.0), 30.0)
    except (TypeError, ValueError):
        return 2.0

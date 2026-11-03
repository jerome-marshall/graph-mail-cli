"""A pragmatic TOON encoder.

TOON (Token-Oriented Object Notation, https://toonformat.dev) is an
indentation-based, token-efficient encoding that collapses uniform arrays of
objects into a header + rows. It is meant for pasting structured data into LLM
prompts more cheaply than JSON, while staying lossless.

This encoder covers the shapes ``apple-mail`` actually emits:

- objects (key: value, nested by indentation)
- arrays of scalars            -> ``key[N]: a,b,c``
- arrays of uniform flat objects -> ``key[N]{f1,f2}:`` then comma rows
- everything else (nested / non-uniform arrays) -> a ``-`` block list

It is faithful for the first three (the common case) and falls back to a
lossless block-list form for anything irregular.
"""

from __future__ import annotations

import json
import re

_INDENT = "  "
_NUMBER_RE = re.compile(r"^-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?$")
_SPECIAL_CHARS = set(':,[]{}"\n\r\t#')
_RESERVED = {"true", "false", "null"}


def encode(obj) -> str:
    """Encode a Python value (typically the response envelope) as TOON text."""
    lines: list[str] = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            _emit_kv(key, value, 0, lines)
    elif isinstance(obj, list):
        _emit_array("items", obj, 0, lines)
    else:
        lines.append(_fmt_scalar(obj))
    return "\n".join(lines) + "\n"


def _is_scalar(v) -> bool:
    return v is None or isinstance(v, (bool, int, float, str))


def _fmt_scalar(v) -> str:
    if v is None:
        return "null"
    if v is True:
        return "true"
    if v is False:
        return "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return repr(v)
    return _fmt_string(str(v))


def _needs_quote(s: str) -> bool:
    if s == "":
        return True
    if s != s.strip():
        return True
    if s in _RESERVED:
        return True
    if _NUMBER_RE.match(s):
        return True
    if s[0] in "-[{\"'":
        return True
    return any(ch in _SPECIAL_CHARS for ch in s)


def _fmt_string(s: str) -> str:
    return json.dumps(s, ensure_ascii=False) if _needs_quote(s) else s


def _fmt_key(key) -> str:
    k = str(key)
    return json.dumps(k, ensure_ascii=False) if _needs_quote(k) else k


def _emit_kv(key, value, indent: int, lines: list[str]) -> None:
    pad = _INDENT * indent
    k = _fmt_key(key)
    if _is_scalar(value):
        lines.append(f"{pad}{k}: {_fmt_scalar(value)}")
    elif isinstance(value, dict):
        lines.append(f"{pad}{k}:")
        for kk, vv in value.items():
            _emit_kv(kk, vv, indent + 1, lines)
    elif isinstance(value, list):
        _emit_array(k, value, indent, lines)
    else:
        lines.append(f"{pad}{k}: {_fmt_scalar(value)}")


def _is_uniform_flat_objects(arr: list) -> bool:
    if not arr or not all(isinstance(x, dict) for x in arr):
        return False
    keys = list(arr[0].keys())
    if not keys:
        return False
    keyset = set(keys)
    for row in arr:
        if set(row.keys()) != keyset:
            return False
        if not all(_is_scalar(v) for v in row.values()):
            return False
    return True


def _emit_array(key: str, arr: list, indent: int, lines: list[str]) -> None:
    pad = _INDENT * indent
    n = len(arr)
    if n == 0:
        lines.append(f"{pad}{key}[0]:")
        return
    if all(_is_scalar(x) for x in arr):
        lines.append(f"{pad}{key}[{n}]: " + ",".join(_fmt_scalar(x) for x in arr))
        return
    if _is_uniform_flat_objects(arr):
        fields = list(arr[0].keys())
        header = ",".join(_fmt_key(f) for f in fields)
        lines.append(f"{pad}{key}[{n}]{{{header}}}:")
        cpad = _INDENT * (indent + 1)
        for row in arr:
            lines.append(cpad + ",".join(_fmt_scalar(row[f]) for f in fields))
        return
    # Irregular: lossless block-list fallback.
    lines.append(f"{pad}{key}[{n}]:")
    for item in arr:
        _emit_list_item(item, indent + 1, lines)


def _emit_list_item(item, indent: int, lines: list[str]) -> None:
    pad = _INDENT * indent
    if _is_scalar(item):
        lines.append(f"{pad}- {_fmt_scalar(item)}")
    elif isinstance(item, dict):
        lines.append(f"{pad}-")
        for kk, vv in item.items():
            _emit_kv(kk, vv, indent + 1, lines)
    elif isinstance(item, list):
        lines.append(f"{pad}-")
        for sub in item:
            _emit_list_item(sub, indent + 1, lines)
    else:
        lines.append(f"{pad}- {_fmt_scalar(item)}")

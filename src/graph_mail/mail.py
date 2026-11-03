"""Mail reads over Microsoft Graph.

Every command returns plain dicts; the CLI wraps them in the ``{ok, data}``
envelope. Nothing here can mutate mail: only GET requests are issued.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from html.parser import HTMLParser

from .client import GraphClient
from .errors import not_found
from .timeparse import parse_dt

# Folders that can be addressed without a lookup round-trip.
WELL_KNOWN = {
    "inbox": "inbox",
    "sent": "sentitems",
    "sentitems": "sentitems",
    "sent items": "sentitems",
    "drafts": "drafts",
    "deleted": "deleteditems",
    "deleteditems": "deleteditems",
    "deleted items": "deleteditems",
    "trash": "deleteditems",
    "archive": "archive",
    "junk": "junkemail",
    "junkemail": "junkemail",
    "junk email": "junkemail",
    "outbox": "outbox",
}

LIST_SELECT = (
    "id,internetMessageId,conversationId,subject,bodyPreview,receivedDateTime,"
    "sentDateTime,from,toRecipients,ccRecipients,isRead,flag,hasAttachments,"
    "importance,webLink,parentFolderId"
)
READ_SELECT = LIST_SELECT + ",body,bccRecipients,replyTo,categories"


# --------------------------------------------------------------------------- #
# Formatting helpers
# --------------------------------------------------------------------------- #

def local_iso(value: str | None) -> str | None:
    """Graph returns UTC ('2026-11-03T16:53:50Z'); emit local ISO 8601."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone().isoformat()


def _address(entry: dict | None) -> str | None:
    email = ((entry or {}).get("emailAddress") or {})
    name, addr = email.get("name"), email.get("address")
    if name and addr:
        return f"{name} <{addr}>"
    return addr or name


def _addresses(entries: list | None) -> list[str]:
    return [a for a in (_address(e) for e in (entries or [])) if a]


class _TextExtractor(HTMLParser):
    BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "head"):
            self._skip += 1
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "head") and self._skip:
            self._skip -= 1
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    parser = _TextExtractor()
    parser.feed(html)
    text = "".join(parser.parts)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# --------------------------------------------------------------------------- #
# Folder resolution
# --------------------------------------------------------------------------- #

def list_folders(client: GraphClient) -> list[dict]:
    folders, _ = client.paginate(
        "/me/mailFolders",
        {"$top": "100", "$select": "id,displayName,parentFolderId,totalItemCount,unreadItemCount"},
        limit=500,
    )
    for folder in folders:
        children, _ = client.paginate(
            f"/me/mailFolders/{folder['id']}/childFolders",
            {"$top": "100", "$select": "id,displayName,parentFolderId,totalItemCount,unreadItemCount"},
            limit=200,
        )
        folders.extend(children)
    return folders


def resolve_folder(client: GraphClient, name: str | None) -> tuple[str, str | None]:
    """Return (folder path segment, display name). Raises NOT_FOUND if unknown."""
    if not name:
        return "/me/messages", None
    key = name.strip().lower()
    if key in WELL_KNOWN:
        return f"/me/mailFolders/{WELL_KNOWN[key]}/messages", None

    matches = [f for f in list_folders(client) if key in (f.get("displayName") or "").lower()]
    if not matches:
        raise not_found(f"no mailbox matching {name!r}")
    exact = [f for f in matches if (f.get("displayName") or "").lower() == key]
    folder = (exact or matches)[0]
    return f"/me/mailFolders/{folder['id']}/messages", folder.get("displayName")


def _folder_names(client: GraphClient) -> dict[str, str]:
    return {f["id"]: f.get("displayName") for f in list_folders(client)}


# --------------------------------------------------------------------------- #
# Item mapping
# --------------------------------------------------------------------------- #

def to_item(msg: dict, folders: dict[str, str] | None = None) -> dict:
    flag = (msg.get("flag") or {}).get("flagStatus")
    folder_id = msg.get("parentFolderId")
    sender = ((msg.get("from") or {}).get("emailAddress") or {})
    return {
        "id": msg.get("id"),
        "messageId": msg.get("internetMessageId"),
        "conversationId": msg.get("conversationId"),
        "subject": msg.get("subject"),
        "from": _address(msg.get("from")),
        "fromName": sender.get("name"),
        "fromAddress": sender.get("address"),
        "to": _addresses(msg.get("toRecipients")),
        "cc": _addresses(msg.get("ccRecipients")),
        "date": local_iso(msg.get("receivedDateTime")),
        "sentDate": local_iso(msg.get("sentDateTime")),
        "preview": msg.get("bodyPreview"),
        "isUnread": msg.get("isRead") is False,
        "isFlagged": flag == "flagged",
        "hasAttachments": bool(msg.get("hasAttachments")),
        "importance": msg.get("importance"),
        "mailbox": (folders or {}).get(folder_id),
        "webLink": msg.get("webLink"),
    }


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #

def _date_filter(args) -> str | None:
    clauses = []
    if getattr(args, "after", None):
        clauses.append(f"receivedDateTime ge {parse_dt(args.after).astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}")
    if getattr(args, "before", None):
        clauses.append(f"receivedDateTime le {parse_dt(args.before).astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}")
    if getattr(args, "unread", False):
        clauses.append("isRead eq false")
    return " and ".join(clauses) or None


def cmd_list(args) -> dict:
    client = GraphClient()
    path, display = resolve_folder(client, args.mailbox)
    folders = None if display else _folder_names(client)
    params = {
        "$top": str(min(max(args.limit, 1), 50)),
        "$select": LIST_SELECT,
        "$orderby": "receivedDateTime desc",
        "$filter": _date_filter(args),
    }
    rows, has_more = client.paginate(path, params, limit=args.limit, offset=args.offset)
    return {
        "items": [to_item(m, folders) for m in rows],
        "count": len(rows),
        "hasMore": has_more,
        "mailbox": display or ("all mail" if not args.mailbox else args.mailbox),
        "source": "graph",
    }


def _kql_quote(value: str) -> str:
    return value if value.startswith('"') else f'"{value}"'


def cmd_search(args) -> dict:
    client = GraphClient()
    path, display = resolve_folder(client, args.mailbox)
    folders = None if display else _folder_names(client)

    terms = [args.query]
    if getattr(args, "after", None):
        terms.append(f"received>={parse_dt(args.after).strftime('%Y-%m-%d')}")
    if getattr(args, "before", None):
        terms.append(f"received<={parse_dt(args.before).strftime('%Y-%m-%d')}")
    params = {
        "$search": _kql_quote(" AND ".join(terms)),
        "$top": str(min(max(args.limit, 1), 50)),
        "$select": LIST_SELECT,
    }
    rows, has_more = client.paginate(path, params, limit=args.limit, offset=args.offset)
    items = [to_item(m, folders) for m in rows]
    if getattr(args, "unread", False):
        items = [i for i in items if i["isUnread"]]
    return {
        "items": items,
        "count": len(items),
        "hasMore": has_more,
        "query": args.query,
        "mailbox": display or ("all mail" if not args.mailbox else args.mailbox),
        "source": "graph",
    }


def cmd_read(args) -> dict:
    client = GraphClient()
    msg = client.get(
        f"/me/messages/{args.id}",
        {"$select": READ_SELECT},
    )
    if not msg:
        raise not_found(f"no message with id {args.id}")

    body = msg.get("body") or {}
    content, content_type = body.get("content") or "", body.get("contentType") or "text"
    item = to_item(msg, _folder_names(client))
    item.update(
        {
            "bcc": _addresses(msg.get("bccRecipients")),
            "replyTo": _addresses(msg.get("replyTo")),
            "categories": msg.get("categories") or [],
            "bodyContentType": content_type,
            "body": html_to_text(content) if content_type.lower() == "html" else content,
            "bodyAvailable": bool(content),
        }
    )
    if content_type.lower() == "html":
        item["bodyHtml"] = content

    if msg.get("hasAttachments"):
        atts, _ = client.paginate(
            f"/me/messages/{args.id}/attachments",
            {"$top": "50", "$select": "id,name,contentType,size,isInline"},
            limit=100,
        )
        item["attachments"] = [
            {
                "attachmentId": a.get("id"),
                "filename": a.get("name"),
                "contentType": a.get("contentType"),
                "size": a.get("size"),
                "isInline": a.get("isInline"),
            }
            for a in atts
        ]
    else:
        item["attachments"] = []
    return item


def cmd_mailboxes(args) -> dict:
    client = GraphClient()
    folders = list_folders(client)
    items = [
        {
            "id": f["id"],
            "name": f.get("displayName"),
            "parentId": f.get("parentFolderId"),
            "totalCount": f.get("totalItemCount"),
            "unreadCount": f.get("unreadItemCount"),
        }
        for f in folders
    ]
    items.sort(key=lambda f: (f["name"] or "").lower())
    return {"items": items, "count": len(items), "hasMore": False, "source": "graph"}

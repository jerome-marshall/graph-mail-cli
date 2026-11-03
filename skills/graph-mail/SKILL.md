---
name: graph-mail
description: >
  Read the user's Microsoft 365 mail and calendar over Microsoft Graph with the
  `graph-mail` CLI, including mail that has not reached the local Apple Mail store:
  summarize unread mail, search bodies for a topic, find a thread, check whether
  someone replied, list a day's calendar, or name who is on a meeting. Trigger on
  "summarize my emails today", "what's on my calendar tomorrow", "find the thread
  about <topic>", "did <person> reply", "who is on the 3pm meeting", "any unread
  mail", "search my mail for <words>", even when the user does not name the tool.
  READ-ONLY: it cannot send, reply, move, delete, flag, or schedule; for any write,
  use the `outlook-cli` (`olk`) skill instead.
---

# Microsoft 365 mail and calendar (`graph-mail`)

`graph-mail` is a JSON-first, read-only CLI over Microsoft Graph. Each call is one
shot against the service, so it sees mail and calendar state that the local Apple
Mail store may not have synced yet.

## Before you start

Once per session that touches mail or calendar, run:

```bash
graph-mail doctor --json
```

- Exit 0: signed in. Continue when `data.auth.authenticated` is true.
- Exit 3 with `AUTH_REQUIRED`: no stored token. Run `graph-mail auth login`, have the
  user finish the browser page, then run `doctor` again.
- `data.mail.ok` and `data.calendar.ok` can be false while the token still works. Each
  section then carries `error.code` and `error.message`. Report that code instead of
  re-running `doctor`.

The token refreshes on its own, so one check per session is enough.

## Sign in

```bash
graph-mail auth login --json
```

The command prints the sign-in URL to stderr and opens the browser. It listens on
`http://localhost:8400/callback`, so the user must complete the page on the same
machine. It returns once the code exchange succeeds, with `data.authenticated` true
and `data.account` filled in.

On a headless host, such as the Ubuntu VM, add `--device-code`. It prints a code to
enter at `https://microsoft.com/devicelogin` from any browser, then waits for approval.
Set `GRAPH_MAIL_NO_KEYCHAIN=1` there so the token lands in
`~/.config/graph-mail/tokens.json`.

```bash
graph-mail auth status --json     # expiry, scope, backend; never the token
graph-mail auth logout --json     # delete the stored token
```

## Commands

| Command | Use |
|---------|-----|
| `graph-mail mail list --limit 20` | Newest messages first, all mailboxes |
| `graph-mail mail list --mailbox inbox --unread` | Unread inbox only |
| `graph-mail mail search "quarterly review"` | Search subject, body, and people |
| `graph-mail mail read <id>` | One message with `body` and `attachments` |
| `graph-mail mail mailboxes` | Folder names with total and unread counts |
| `graph-mail cal calendars` | Calendar names, owner, and edit rights |
| `graph-mail cal list --days 2` | Events from now through two days ahead |
| `graph-mail cal get <id>` | One event with `body` |
| `graph-mail doctor` | Health check |
| `graph-mail version` | Version |

## Examples

```bash
# Today's unread, inbox only
graph-mail mail list --mailbox inbox --unread --after 2026-11-03T00:00:00 --limit 50

# Mail about a topic since the start of the month
graph-mail mail search "budget approval" --after 2026-10-01 --limit 25

# A person's mail from the last week
graph-mail mail search "from:ada@example.com" --after 2026-10-27

# Read one message in full
graph-mail mail read AAMkADk0M2JlYzQ1

# Tomorrow, one calendar
graph-mail cal list --start 2026-11-04T00:00:00 --end 2026-11-04T23:59:59 --calendar Work

# The next two weeks
graph-mail cal list --days 14 --limit 100

# One event with its body and attendee list
graph-mail cal get AAMkADk0M2JlYzQ1
```

- `mail list` and `mail search` take `--mailbox`, `--after`, `--before`, `--unread`,
  `--limit`, `--offset`.
- `cal list` takes `--start`, `--end`, `--days` (default 7), `--calendar`, `--limit`.
- `--mailbox` and `--calendar` match a case-insensitive substring. `mail mailboxes` and
  `cal calendars` give the exact names.
- `cal list` reads the default calendar unless `--calendar` names another one.

## Date semantics

`--after`, `--before`, `--start`, and `--end` accept ISO 8601:

- Naked (`2026-11-04T09:00:00`) is local time. Use it for "today" and "tomorrow".
- Trailing `Z` is UTC. An explicit offset such as `+05:30` is that offset.
- A bare date (`2026-11-04`) is local midnight.

`cal list` starts at now, or at `--start`, and ends at `--end`, or at `--start` plus
`--days`. Output times are local ISO 8601.

## Output contract

```jsonc
{ "ok": true, "data": <object> }                                        // scalar
{ "ok": true, "data": { "items": [...], "count": N, "hasMore": false } } // list
{ "ok": false, "error": { "code": "...", "message": "..." } }           // stderr, exit != 0
```

- `--json` (default) for code and `jq`. `--ndjson` prints one item per line. `--toon`
  encodes the whole envelope for an LLM prompt.
- Branch on `code`. `message` is for the user.
- Parse JSON from a file, not by splitting shell strings. Subjects and bodies carry
  quotes, newlines, and emoji.

```bash
graph-mail mail list --limit 20 --json > /tmp/inbox.json
python3 -c "import json; d=json.load(open('/tmp/inbox.json'))['data']; \
  print(*[f\"{m['id']}  {m['from']}  {(m['subject'] or '')[:60]}\" for m in d['items']], sep='\n')"
```

## Lightweight first, drill down second

`mail list` and `mail search` return `subject`, `from`, `to`, `cc`, `date`,
`preview`, `isUnread`, `isFlagged`, `hasAttachments`, `importance`, `mailbox`, and
`webLink`, with no body. Answer from this shape when the question is about who, when,
or what the subject says.

Call `mail read <id>` only when the user needs the body, the attachment list, or the
full recipient set. `cal list` returns times, location, organizer, attendees, and
`preview`. Call `cal get <id>` only for the body, which may be a long meeting note.

## Limits

- Graph rejects `$search` together with `$orderby`, so search results keep Graph's own
  order. Bound them with `--after` and `--before` rather than expecting date order.
- Message collections have no `$skip`. `--offset` works because the client walks
  `@odata.nextLink`, which costs one request per earlier page.
- `mail search --unread` filters the page after it arrives, so it can return fewer
  items than `--limit`. Ask for more than you need.
- One page holds at most 50 messages or 100 events. A larger `--limit` means more
  requests, not a bigger page.
- Attachments are listed, never downloaded.
- The CLI queries the `/me` endpoints, so it reaches the signed-in user's mailbox and
  calendars. Shared mailboxes and room calendars are out of reach.

## Errors

| Code | Meaning | Response |
|------|---------|----------|
| `AUTH_REQUIRED` | No token, or the refresh token is gone | Run `graph-mail auth login` once, then retry |
| `AUTH_FAILED` | Sign-in or token exchange failed | Read `message`. `invalid_grant` needs a fresh login. `AADSTS65001` needs admin consent |
| `NOT_FOUND` | Id or name matched nothing | Re-list with `mail mailboxes` or `cal calendars` |
| `VALIDATION_ERROR` | Bad flag or date | Re-read `--help`. `--end` must be later than `--start` |
| `GRAPH_ERROR` | Graph refused the request | `details.graphCode` names the reason. Throttling retries on its own |
| `NETWORK_ERROR` | Could not reach Graph | Check VPN, then retry once |
| `CONFIG_ERROR` | Keychain or config directory problem | Retry, or set `GRAPH_MAIL_NO_KEYCHAIN=1` to use the token file |

## When NOT to use

- Any write at all: send, reply, forward, move, delete, flag, mark read, accept an
  invite, or create an event. `graph-mail` is read-only. Use the `outlook-cli` (`olk`)
  skill.
- Local-only questions, or a fully synced mailbox where speed matters. `apple-mail`
  reads the local store without a network round trip.
- Tasks, notes, contacts, Teams messages, or files. These scopes do not cover them.
- Another tenant's mail. The app registration is single tenant.

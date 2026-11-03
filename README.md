# graph-mail

A read-only command-line client for Microsoft 365 mail and calendar, over Microsoft
Graph. Every command prints one JSON envelope, so it works the same way from a shell,
a script, or an agent.

There is no send, reply, move, delete, or create command. The CLI only issues GET
requests, and the app registration asks for read scopes only.

## Why it exists beside apple-mail

`apple-mail` reads the mail and calendar stores that macOS syncs to disk. That is fast
and works offline, but it only sees what the local client has downloaded. `graph-mail`
asks the service, so it sees a message as soon as Exchange has it, and it runs on Linux
with no synced mirror.

Use `graph-mail` when the answer depends on server state: today's unread count, a
thread that has not synced yet, or a calendar another person just changed.

## Why not the CLI for Microsoft 365

The community `m365` CLI (pnp/cli-microsoft365) talks to the same app registration and
covers a much wider surface. It was measured against this CLI and rejected for the read
path, for two reasons. It is not wired in as a fallback.

- `m365 outlook message list` has no limit, so it pages a whole folder. The 11,946
  message Inbox did not finish in two minutes. `graph-mail mail list --limit 20`
  returns in about a second.
- `m365` returns the raw Graph object with the full HTML body. 19 drafts measured
  132 KB from `m365` and 18 KB from `graph-mail`, a 7x reduction, and one draft body
  alone was 16 KB.

Writes stay out of scope. Use the `outlook-cli` (`olk`) skill for those.

## Access model

- Delegated permissions, signed in as the user, single tenant.
- Scopes: `offline_access`, `Mail.Read`, `Calendars.Read`, `User.Read`.
- Public client with authorization code + PKCE and a loopback redirect. No client
  secret is stored or sent.
- Refresh token in the macOS Keychain, so unattended runs need no prompt.

## Setup

### 1. App registration

In Entra ID, register an application for this CLI:

- Authentication, Add a platform, Mobile and desktop applications, redirect URI
  `http://localhost:8400/callback`. A different port must also change
  `GRAPH_MAIL_PORT` and be registered here.
- Authentication, Allow public client flows: Yes.
- API permissions, Microsoft Graph, Delegated: `Mail.Read`, `Calendars.Read`,
  `User.Read`, `offline_access`. Grant admin consent for the tenant.
- No client secret and no certificate.

### 2. Point the CLI at it

The defaults target the internal read-only app registration. Override them when you
register your own:

| Variable | Default | Purpose |
|----------|---------|---------|
| `GRAPH_MAIL_CLIENT_ID` | built in | Application (client) ID |
| `GRAPH_MAIL_TENANT` | built in | Tenant ID or domain |
| `GRAPH_MAIL_PORT` | `8400` | Loopback port, must match the registered redirect |
| `GRAPH_MAIL_CONFIG_DIR` | `~/.config/graph-mail` | Where the file token fallback lives |
| `GRAPH_MAIL_NO_KEYCHAIN` | unset | Set to `1` to skip the Keychain and use the file |

### 3. Sign in

```bash
graph-mail auth login
```

On a headless machine, run `GRAPH_MAIL_NO_KEYCHAIN=1 graph-mail auth login --device-code`.
It prints a code for `https://microsoft.com/devicelogin` and polls until you approve,
because there is no browser on that host to receive the loopback redirect.

This opens the Microsoft sign-in page. Complete it in the browser. The CLI listens on
`http://localhost:8400/callback` for the code, exchanges it, and stores the tokens.

Then check the setup:

```bash
graph-mail doctor --json
```

`doctor` exits with code 3 and an `AUTH_REQUIRED` envelope when no token is stored.
With a token it reports the account, the inbox counts, and the number of events on the
default calendar in the next seven days.

## Commands

| Command | What it returns |
|---------|-----------------|
| `graph-mail version` | Package name and version |
| `graph-mail doctor` | Version, Python, platform, auth state, account, mail and calendar health |
| `graph-mail auth login` | Runs the browser sign-in, then returns `authenticated` and the account |
| `graph-mail auth login --device-code` | Headless sign-in: prints a code to enter on another device |
| `graph-mail auth status` | Stored token state, backend, expiry, scope |
| `graph-mail auth logout` | Deletes the stored token |
| `graph-mail mail list` | Messages, newest first. Flags: `--mailbox`, `--after`, `--before`, `--unread`, `--limit`, `--offset` |
| `graph-mail mail search QUERY` | Messages matching a KQL query. Same flags as `list` |
| `graph-mail mail read ID` | One message with body and attachment list. Alias `mail get` |
| `graph-mail mail mailboxes` | Folders with total and unread counts |
| `graph-mail cal calendars` | Calendars with owner, color, and edit rights |
| `graph-mail cal list` | Events in a window, on the default calendar or on `--calendar`. Flags: `--start`, `--end`, `--days`, `--calendar`, `--limit` |
| `graph-mail cal get ID` | One event with body |

Every leaf command takes `--json` (default), `--ndjson`, or `--toon`.

Examples:

```bash
graph-mail mail list --mailbox inbox --unread --limit 20
graph-mail mail search "quarterly review" --after 2026-10-01 --limit 25
graph-mail cal list --days 2
graph-mail cal list --start 2026-11-04T00:00:00 --end 2026-11-04T23:59:59 --calendar Work
```

## Output contract

```jsonc
{ "ok": true, "data": <object> }                                     // scalar
{ "ok": true, "data": { "items": [...], "count": N, "hasMore": false } } // list
{ "ok": false, "error": { "code": "...", "message": "..." } }        // stderr, non-zero exit
```

`code` is stable and safe to branch on. `message` is for a human. Errors go to stderr
and never print a traceback.

`--json` is pretty on a TTY and compact when piped. `--ndjson` prints one JSON object
per line, and for lists it prints each item as its own line with no envelope. `--toon`
encodes the whole envelope in TOON for pasting into a model prompt.

Exit codes:

| Code | Meaning |
|------|---------|
| 0 | Success |
| 2 | Bad arguments |
| 3 | Not signed in (`AUTH_REQUIRED`) |
| 4 | Sign-in or token exchange failed |
| 5 | Id or name does not exist |
| 6 | Local config or Keychain problem |
| 7 | Graph returned an error |
| 8 | Could not reach Graph |

## Data shapes

Message items from `mail list` and `mail search` carry `id`, `messageId`,
`conversationId`, `subject`, `from`, `to`, `cc`, `date`, `sentDate`, `preview`,
`isUnread`, `isFlagged`, `hasAttachments`, `importance`, `mailbox`, `webLink`, and no
body. `mail read` adds `bcc`, `replyTo`, `categories`, `bodyContentType`, `body`,
`bodyHtml` for HTML messages, `bodyAvailable`, and `attachments`.

Event items carry `id`, `eventId`, `subject`, `start`, `end`, `allDay`, `calendar`,
`location`, `organizer`, `attendees`, `isCancelled`, `isOnlineMeeting`,
`onlineMeetingUrl`, `preview`, `webLink`, `responseStatus`, `seriesMasterId`. `cal get`
adds `bodyContentType`, `body`, `bodyAvailable`. Times are ISO 8601 in the machine's
local zone.

## Token storage

On macOS the token bundle is a generic password item with service `graph-mail` and
account `graph-mail`. Elsewhere, or with `GRAPH_MAIL_NO_KEYCHAIN=1`, it is
`~/.config/graph-mail/tokens.json` with mode `0600`. Rotated refresh tokens are written
back on every refresh.

`graph-mail auth logout` deletes both.

## Limits

- Graph rejects `$search` combined with `$orderby`, so `mail search` returns results in
  Graph's own order. Use `--after` and `--before` to bound the range.
- Message collections have no `$skip`. The client follows `@odata.nextLink` until it
  has enough items, so `--offset` still works, at the cost of earlier pages.
- `mail search --unread` filters the page after it arrives. Ask for more than you need
  when you filter this way.
- Calendar times come from Graph in the mailbox's own time zone. The CLI maps that zone
  to local time, including the Windows zone names Exchange uses.
- `--limit` caps one page of messages at 50 and one page of events at 100. Higher values
  mean more round trips, not a bigger Graph page.

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `AUTH_REQUIRED` | No stored token, or the refresh token was revoked | Run `graph-mail auth login` |
| `AUTH_FAILED` with `invalid_grant` | Refresh token expired or revoked, often after a password change | Run `graph-mail auth login` again |
| `AUTH_FAILED` with `AADSTS65001` | Consent was never granted | Have an admin grant consent for the four delegated scopes |
| `AUTH_FAILED` with `AADSTS700016` or `AADSTS90002` | Wrong tenant for this app registration | Set `GRAPH_MAIL_TENANT` to the tenant that owns the app |
| Sign-in page loops or times out | The redirect URI on the app does not match `localhost:8400/callback` | Register the exact URI, or set `GRAPH_MAIL_PORT` to the registered port |
| `NETWORK_ERROR` | No route to Graph, usually VPN or conditional access | Connect to the corporate network; check the device compliance state |
| `GRAPH_ERROR` with `TooManyRequests` | Throttled | The client retries with `Retry-After` up to three times. Wait and retry if it still fails |
| `GRAPH_ERROR` with `AccessDenied` | The signed-in user has no mailbox or calendar access | Confirm the mailbox is provisioned and licensed |
| `NOT_FOUND` on a mailbox or calendar name | The substring matched nothing | Run `mail mailboxes` or `cal calendars` for the real names |
| Keychain prompt on every run | `security` could not read the item without consent | Allow access once, or set `GRAPH_MAIL_NO_KEYCHAIN=1` |

## Security notes

- Read-only: the CLI issues GET requests only.
- No write scope. Three of the four delegated scopes are read-only, and
  `offline_access` exists only to refresh the token. The registered scopes cannot
  change mail.
- Body text is converted to plain text for HTML messages. `bodyHtml` keeps the original
  markup for messages you read in full.
- The token never goes to stdout. `auth status` reports expiry and scope, not the token.

## Development

```bash
PYTHONPATH=src python3 -m pytest -q
PYTHONPATH=src python3 -m graph_mail.cli --help
```

Standard library only, Python 3.11 or later.

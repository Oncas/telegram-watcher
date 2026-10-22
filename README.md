# Telegram watcher

Watches selected Telegram chats using TypeScript on Node.js 24.12 or newer.
The Telegram adapter uses teleproto; SQLite comes from Node's `node:sqlite`.
Python is not required.

Build and check through Docker:

```sh
make build
make check
```

For a new installation:

```sh
make init
make login
make pick-chats
make run
make listen
```

`run` polls once. `listen` polls immediately, after new-message or edit hints
from a watched chat, and at least every 15 minutes. Stop it with Ctrl-C.
The existing systemd service runs the same `telegram-watcher listen` command.

Existing `var/config.toml`, `credentials.toml`, `forward.toml`, the Telegram
string session, SQLite schema version 2, checkpoints, halt markers, pending
matches, and downloaded media are reused. Do not run `init` over existing
state. Python and Node processes share the same kernel lock, so they cannot
poll concurrently. Rebuild the container before starting the Node version.

For local development without Docker, install dependencies with `npm ci`,
run `npm run check`, and use `npm start -- <command>`. Linux installations
need `flock` from util-linux and `notify-send` from libnotify.

Configuration uses the existing TOML format:

```toml
[[chats]]
name = "Synthetic basic group"
chat_id = -42
download_photos = false
```

Channels and private chats also require their `access_hash`, supplied by
`pick-chats`. Access hashes and album IDs retain their full 64-bit precision.
Keep credentials, sessions, configuration, media, and databases under the
ignored `var/` directory with owner-only permissions.

The first poll starts at the current message without importing history. Each
later poll saves bounded pages and their checkpoints atomically, rechecks
stored posts from the last three days for edits and deletions, and stores
post metadata. Text is available to listeners but is not stored with posts.
Photo downloads remain opt-in for each chat and exclude outgoing posts.
Set `download_photos = "matched"` to download a photo only when a listener for
that chat yields a match for its post; `true` downloads every photo.

Long flood waits persist an account-wide retry deadline. Authorization errors
persist a halt until the underlying problem is fixed and `make clear-halt`
is run. Pending listener deliveries retry even while Telegram is unavailable
or halted. Delivery is at least once: a crash after a successful HTTP request
but before its local commit can repeat a request. Receivers should deduplicate
using the payload's stable `id`.

Copy `listeners/example.ts` to a new `.ts`, `.js`, or `.mjs` file to create a
listener. The example itself is skipped, and private listeners are ignored
by Git. Python listeners must be ported; they are not executed by Node.
Each module exports a marked numeric `CHAT_ID` and a synchronous `listen`
function that returns or yields JSON objects. Posts retain their existing
snake_case field names; timestamps are JavaScript `Date` values and
`album_id` is a `bigint` or `null`. Convert any large integer to a string
before including it in a JSON match. Listener failures are isolated and
logged without post text or exception messages.

Optional HTTP forwarding still reads `var/forward.toml` with exactly `url`
and `token` fields. Use owner-only permissions. Matches are sent as JSON
with a bearer token; redirects are refused and requests time out after ten
seconds. Unchanged matches retain their identity across edits, and stored
pending matches keep their original IDs.

The automated tests use synthetic messages, local HTTP servers, and temporary
databases under `.test-tmp/`. They do not authenticate to Telegram or read the
real runtime state. A live Telegram connection must be checked separately.

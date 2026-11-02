# Telegram watcher

Watches selected Telegram chats from your own account: groups, channels, and
private chats you pick. It keeps a local SQLite record of post metadata, edits
and deletions, lets small listener modules pick out the posts you care about,
and forwards what they find to an HTTP endpoint or a desktop notification.

It runs on Node.js 24.12 or newer, talks to Telegram through teleproto, and
stores data with Node's built-in `node:sqlite`.

## Requirements

- Docker with Compose, and `make`. Or Node.js 24.12+, `flock` from util-linux
  and, for desktop notifications, `notify-send` from libnotify.
- A Telegram `api_id` and `api_hash` from <https://my.telegram.org> under
  "API development tools".

## Setup

```sh
make build
make init         # create var/ with an empty database
make login        # save the API credentials and log in to Telegram
make pick-chats   # choose the chats to watch, once
make run          # poll the watched chats once
make listen       # keep polling as new posts arrive; stop with Ctrl-C
```

Everything the watcher writes lives under `var/`, which Git ignores. The
credentials, session, config, database and media are owner-only, and the
watcher refuses to read a secret file that other users can read. Do not run
`init` over an existing `var/`.

`listen` polls immediately, again whenever Telegram hints at a new or edited
message in a watched chat, and at least every 15 minutes.

Without Docker, run `npm ci`, then `npm start -- <command>` with the same
commands.

## Configuration

`pick-chats` writes `var/config.toml`. You can edit it afterwards:

```toml
[[chats]]
name = "Example basic group"
chat_id = -42
download_photos = false

[[chats]]
name = "Example channel"
chat_id = -1001234567890
access_hash = 1234567890123456789
download_photos = "matched"
```

- `chat_id` is the marked chat ID: negative for groups, `-100...` for channels
  and supergroups, positive for private chats.
- `access_hash` is required for channels and private chats and is filled in
  by `pick-chats`.
- `download_photos` is `false` (the default), `true` to save every incoming
  photo, or `"matched"` to save a photo only when a listener for that chat
  matches its post.

The first poll of a chat starts at its latest message and does not import
history. Later polls save new posts in bounded pages and recheck posts from
the last three days for edits and deletions. The post record leaves out
text; only what a listener returns is stored.

Downloaded photos land in `var/media/downloaded/`, named
`<chat_id>_<message_id>.<ext>`. Another program can move them to
`var/media/rejected/` or `var/media/archived/`; a photo in any of the three
folders is not downloaded again.

## Listeners

A listener is a `.ts`, `.js` or `.mjs` file in `listeners/` that handles one
chat. Copy `listeners/example.ts` to start one. Listener files other than the
example are ignored by Git, because they name your chats.

Each module exports:

- `CHAT_ID`, the marked chat ID it listens to
- `listen(post)`, a synchronous function that returns or yields JSON objects

A listener sees each new post and each changed edit, and sees it again when
a failed run is retried, so keep listeners free of state. Posts use
snake_case fields, timestamps are `Date` values and `album_id` is a `bigint`
or `null`, so convert large integers to strings before returning them. A
listener that throws is logged without the post text and does not stop the
others.

## Delivery

Every match is shown as a desktop notification. To also send matches to an
HTTP endpoint, create `var/forward.toml` with owner-only permissions:

```toml
url = "https://receiver.example/matches"
token = "a-long-random-token"
```

Each match is POSTed as JSON with the token as a bearer header. The URL must
use `https://`, except for `localhost`, `127.x.x.x` and `[::1]`. Redirects are
refused and requests time out after ten seconds. Delivery is at least once,
oldest first, and stops at the first failure until the next run, so a
receiver should drop repeats by the payload's stable `id`.

### Desktop notifications in Docker

The container needs the host's session D-Bus to show notifications. On a
desktop, copy the optional override so Compose picks it up:

```sh
cp compose.desktop.yaml compose.override.yaml
```

Without it, or on a headless machine, notifications fail with a logged
warning and everything else works.

## Failures

A flood wait from Telegram pauses all polling until its deadline. An ended
session, a deactivated account or a refused authorization halts the watcher
until you fix the cause and run `make clear-halt`. Pending deliveries are
retried on every run, even while Telegram is unreachable or halted.

## Running as a service

`systemd/telegram-watcher-listen.service` is a user unit that runs `listen`
in Docker from `~/telegram-watcher`. If your clone is elsewhere, change
`WorkingDirectory`. Then:

```sh
systemctl --user link "$PWD/systemd/telegram-watcher-listen.service"
systemctl --user enable --now telegram-watcher-listen.service
```

## Development

```sh
make check   # typecheck, prettier and tests in Docker
```

The tests use synthetic messages, local HTTP servers and temporary databases
under `.test-tmp/`. They never connect to Telegram or read `var/`.

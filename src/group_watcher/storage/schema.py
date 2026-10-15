SCHEMA_VERSION = 2

SCHEMA = f"""
BEGIN;

CREATE TABLE posts (
    chat_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    posted_at TEXT NOT NULL,
    album_id INTEGER,
    deleted_at TEXT,
    PRIMARY KEY (chat_id, message_id)
) STRICT;

CREATE TABLE post_versions (
    chat_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    version INTEGER NOT NULL,
    edited_at TEXT,
    media_kind TEXT,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (chat_id, message_id, version)
) STRICT;

CREATE TABLE checkpoints (
    chat_id INTEGER PRIMARY KEY,
    last_message_id INTEGER NOT NULL
) STRICT;

CREATE TABLE account_state (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    halt_reason TEXT,
    retry_at TEXT
) STRICT;

INSERT INTO account_state (singleton) VALUES (1);

CREATE TABLE runs (
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL,
    outcome TEXT NOT NULL,
    error_type TEXT
) STRICT;

CREATE TABLE chat_polls (
    chat_id INTEGER PRIMARY KEY,
    watched_since TEXT NOT NULL,
    last_success_at TEXT
) STRICT;

CREATE TABLE active_alerts (
    kind TEXT NOT NULL,
    subject TEXT NOT NULL,
    PRIMARY KEY (kind, subject)
) STRICT;

CREATE TABLE listener_matches (
    match_id TEXT PRIMARY KEY,
    listener TEXT NOT NULL,
    chat_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    posted_at TEXT NOT NULL,
    edited_at TEXT,
    data TEXT NOT NULL,
    found_at TEXT NOT NULL,
    notified_at TEXT,
    forwarded_at TEXT
) STRICT;

CREATE VIEW events AS
SELECT
    CASE WHEN version = 1 THEN 'new' ELSE 'edited' END AS kind,
    chat_id,
    message_id,
    posts.posted_at,
    post_versions.fetched_at,
    post_versions.edited_at,
    post_versions.media_kind,
    posts.album_id
FROM post_versions JOIN posts USING (chat_id, message_id)
UNION ALL
SELECT
    'deleted',
    chat_id,
    message_id,
    posted_at,
    deleted_at,
    NULL,
    NULL,
    album_id
FROM posts
WHERE deleted_at IS NOT NULL;

PRAGMA user_version = {SCHEMA_VERSION};

COMMIT;
"""

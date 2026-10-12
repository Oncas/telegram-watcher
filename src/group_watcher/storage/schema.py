SCHEMA_VERSION = 1

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

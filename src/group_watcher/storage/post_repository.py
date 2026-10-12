import sqlite3
from collections.abc import Iterable
from datetime import datetime

from group_watcher.posts import Post


class PostRepository:
    def __init__(self, connection: sqlite3.Connection):
        self._connection = connection

    def save(self, post: Post, fetched_at: datetime) -> None:
        self._connection.execute(
            "INSERT INTO posts (chat_id, message_id, posted_at, album_id)"
            " VALUES (?, ?, ?, ?) ON CONFLICT DO NOTHING",
            (post.chat_id, post.message_id, _iso(post.posted_at), post.album_id),
        )
        latest = self._connection.execute(
            "SELECT version, edited_at FROM post_versions"
            " WHERE chat_id = ? AND message_id = ? ORDER BY version DESC LIMIT 1",
            (post.chat_id, post.message_id),
        ).fetchone()
        if latest is None:
            self._add_version(post, 1, fetched_at)
            return
        latest_version, latest_edited_at = latest
        if latest_edited_at != _iso(post.edited_at):
            self._add_version(post, latest_version + 1, fetched_at)

    def mark_deleted(
        self, chat_id: int, message_ids: Iterable[int], noticed_at: datetime
    ) -> None:
        self._connection.executemany(
            "UPDATE posts SET deleted_at = ?"
            " WHERE chat_id = ? AND message_id = ? AND deleted_at IS NULL",
            [(_iso(noticed_at), chat_id, message_id) for message_id in message_ids],
        )

    def _add_version(self, post: Post, version: int, fetched_at: datetime) -> None:
        self._connection.execute(
            "INSERT INTO post_versions"
            " (chat_id, message_id, version, edited_at, media_kind, fetched_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (
                post.chat_id,
                post.message_id,
                version,
                _iso(post.edited_at),
                post.media_kind,
                _iso(fetched_at),
            ),
        )


def _iso(value: datetime | None) -> str | None:
    return value.isoformat(timespec="seconds") if value is not None else None

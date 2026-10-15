import sqlite3
from collections.abc import Iterable
from datetime import datetime

from group_watcher.posts import Post
from group_watcher.storage.timestamps import format_timestamp


class PostRepository:
    def __init__(self, connection: sqlite3.Connection):
        self._connection = connection

    def save(self, post: Post, fetched_at: datetime) -> bool:
        self._connection.execute(
            "INSERT INTO posts (chat_id, message_id, posted_at, album_id)"
            " VALUES (?, ?, ?, ?) ON CONFLICT DO NOTHING",
            (
                post.chat_id,
                post.message_id,
                format_timestamp(post.posted_at),
                post.album_id,
            ),
        )
        latest = self._connection.execute(
            "SELECT version, edited_at FROM post_versions"
            " WHERE chat_id = ? AND message_id = ? ORDER BY version DESC LIMIT 1",
            (post.chat_id, post.message_id),
        ).fetchone()
        if latest is None:
            self._add_version(post, 1, fetched_at)
            return True
        latest_version, latest_edited_at = latest
        if latest_edited_at == format_timestamp(post.edited_at):
            return False
        self._add_version(post, latest_version + 1, fetched_at)
        return True

    def recent_message_ids(self, chat_id: int, posted_since: datetime) -> list[int]:
        rows = self._connection.execute(
            "SELECT message_id FROM posts"
            " WHERE chat_id = ? AND posted_at >= ? AND deleted_at IS NULL"
            " ORDER BY message_id",
            (chat_id, format_timestamp(posted_since)),
        )
        return [message_id for (message_id,) in rows]

    def mark_deleted(
        self, chat_id: int, message_ids: Iterable[int], noticed_at: datetime
    ) -> None:
        self._connection.executemany(
            "UPDATE posts SET deleted_at = ?"
            " WHERE chat_id = ? AND message_id = ? AND deleted_at IS NULL",
            [
                (format_timestamp(noticed_at), chat_id, message_id)
                for message_id in message_ids
            ],
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
                format_timestamp(post.edited_at),
                post.media_kind,
                format_timestamp(fetched_at),
            ),
        )

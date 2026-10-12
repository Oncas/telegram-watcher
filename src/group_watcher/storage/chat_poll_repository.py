import sqlite3
from datetime import datetime

from group_watcher.storage.timestamps import format_timestamp


class ChatPollRepository:
    def __init__(self, connection: sqlite3.Connection):
        self._connection = connection

    def start_watching(self, chat_id: int, since: datetime) -> None:
        self._connection.execute(
            "INSERT INTO chat_polls (chat_id, watched_since) VALUES (?, ?)"
            " ON CONFLICT (chat_id) DO NOTHING",
            (chat_id, format_timestamp(since)),
        )

    def record_success(self, chat_id: int, polled_at: datetime) -> None:
        self._connection.execute(
            "UPDATE chat_polls SET last_success_at = ? WHERE chat_id = ?",
            (format_timestamp(polled_at), chat_id),
        )

    def stale_chat_ids(self, polled_before: datetime) -> list[int]:
        rows = self._connection.execute(
            "SELECT chat_id FROM chat_polls"
            " WHERE coalesce(last_success_at, watched_since) < ? ORDER BY chat_id",
            (format_timestamp(polled_before),),
        )
        return [chat_id for (chat_id,) in rows]

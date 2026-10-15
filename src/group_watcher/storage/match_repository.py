import json
import sqlite3
from datetime import datetime

from group_watcher.matches import Match
from group_watcher.storage.timestamps import format_timestamp, parse_timestamp


class MatchRepository:
    def __init__(self, connection: sqlite3.Connection):
        self._connection = connection

    def add(self, match: Match, found_at: datetime) -> None:
        self._connection.execute(
            "INSERT INTO listener_matches"
            " (match_id, listener, chat_id, message_id, posted_at, edited_at, data,"
            " found_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING",
            (
                match.match_id,
                match.listener,
                match.chat_id,
                match.message_id,
                format_timestamp(match.posted_at),
                format_timestamp(match.edited_at),
                json.dumps(match.data),
                format_timestamp(found_at),
            ),
        )

    def unforwarded(self) -> list[Match]:
        return self._select("forwarded_at IS NULL")

    def unnotified(self) -> list[Match]:
        return self._select("notified_at IS NULL")

    def mark_forwarded(self, match: Match, forwarded_at: datetime) -> None:
        self._mark("forwarded_at", match, forwarded_at)

    def mark_notified(self, match: Match, notified_at: datetime) -> None:
        self._mark("notified_at", match, notified_at)

    def _select(self, condition: str) -> list[Match]:
        rows = self._connection.execute(
            "SELECT listener, chat_id, message_id, posted_at, edited_at, data"
            f" FROM listener_matches WHERE {condition} ORDER BY rowid"
        )
        return [
            Match(
                listener=listener,
                chat_id=chat_id,
                message_id=message_id,
                posted_at=parse_timestamp(posted_at),
                edited_at=parse_timestamp(edited_at),
                data=json.loads(data),
            )
            for listener, chat_id, message_id, posted_at, edited_at, data in rows
        ]

    def _mark(self, column: str, match: Match, marked_at: datetime) -> None:
        self._connection.execute(
            f"UPDATE listener_matches SET {column} = ? WHERE match_id = ?",
            (format_timestamp(marked_at), match.match_id),
        )

import sqlite3
from datetime import datetime

from group_watcher.storage.timestamps import format_timestamp


class RunLogRepository:
    def __init__(self, connection: sqlite3.Connection):
        self._connection = connection

    def record(
        self,
        *,
        started_at: datetime,
        finished_at: datetime,
        outcome: str,
        error_type: str | None,
    ) -> None:
        self._connection.execute(
            "INSERT INTO runs (started_at, finished_at, outcome, error_type)"
            " VALUES (?, ?, ?, ?)",
            (
                format_timestamp(started_at),
                format_timestamp(finished_at),
                outcome,
                error_type,
            ),
        )

import sqlite3
from datetime import datetime

from telegram_watcher.client import HaltReason
from telegram_watcher.storage.timestamps import format_timestamp, parse_timestamp


class AccountStateRepository:
    def __init__(self, connection: sqlite3.Connection):
        self._connection = connection

    def halt_reason(self) -> HaltReason | None:
        (reason,) = self._connection.execute(
            "SELECT halt_reason FROM account_state"
        ).fetchone()
        return HaltReason(reason) if reason is not None else None

    def halt(self, reason: HaltReason) -> None:
        self._connection.execute("UPDATE account_state SET halt_reason = ?", (reason,))

    def clear_halt(self) -> None:
        self._connection.execute("UPDATE account_state SET halt_reason = NULL")

    def retry_at(self) -> datetime | None:
        (retry_at,) = self._connection.execute(
            "SELECT retry_at FROM account_state"
        ).fetchone()
        return parse_timestamp(retry_at)

    def defer_until(self, retry_at: datetime) -> None:
        self._connection.execute(
            "UPDATE account_state SET retry_at = ?", (format_timestamp(retry_at),)
        )

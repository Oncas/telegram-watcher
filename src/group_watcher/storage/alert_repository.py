import sqlite3


class AlertRepository:
    def __init__(self, connection: sqlite3.Connection):
        self._connection = connection

    def is_active(self, kind: str, subject: str) -> bool:
        row = self._connection.execute(
            "SELECT 1 FROM active_alerts WHERE kind = ? AND subject = ?",
            (kind, subject),
        ).fetchone()
        return row is not None

    def activate(self, kind: str, subject: str) -> None:
        self._connection.execute(
            "INSERT INTO active_alerts (kind, subject) VALUES (?, ?)", (kind, subject)
        )

    def deactivate(self, kind: str, subject: str) -> None:
        self._connection.execute(
            "DELETE FROM active_alerts WHERE kind = ? AND subject = ?", (kind, subject)
        )

    def deactivate_all(self, kind: str) -> None:
        self._connection.execute("DELETE FROM active_alerts WHERE kind = ?", (kind,))

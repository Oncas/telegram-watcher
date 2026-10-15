import sqlite3


class CheckpointRepository:
    def __init__(self, connection: sqlite3.Connection):
        self._connection = connection

    def last_message_id(self, chat_id: int) -> int | None:
        row = self._connection.execute(
            "SELECT last_message_id FROM checkpoints WHERE chat_id = ?", (chat_id,)
        ).fetchone()
        return row[0] if row else None

    def advance(self, chat_id: int, message_id: int) -> None:
        self._connection.execute(
            "INSERT INTO checkpoints (chat_id, last_message_id) VALUES (?, ?)"
            " ON CONFLICT (chat_id) DO UPDATE"
            " SET last_message_id = max(last_message_id, excluded.last_message_id)",
            (chat_id, message_id),
        )

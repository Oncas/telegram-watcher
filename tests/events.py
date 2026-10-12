import sqlite3
from contextlib import closing


def read_events(db_path, *columns):
    with closing(sqlite3.connect(db_path)) as connection:
        return connection.execute(
            f"SELECT {', '.join(columns)} FROM events"
            " ORDER BY fetched_at, chat_id, message_id, kind"
        ).fetchall()

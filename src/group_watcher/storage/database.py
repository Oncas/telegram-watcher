import os
import sqlite3
from contextlib import closing
from pathlib import Path

from group_watcher.storage.schema import SCHEMA, SCHEMA_VERSION


class StorageError(Exception):
    pass


def create_database(path: Path) -> None:
    try:
        os.close(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
    except OSError as error:
        raise StorageError(f"cannot create {path}: {error.strerror}") from error
    with closing(sqlite3.connect(path, autocommit=True)) as connection:
        connection.execute("PRAGMA journal_mode = WAL")
        connection.executescript(SCHEMA)


def open_database(path: Path) -> sqlite3.Connection:
    try:
        connection = sqlite3.connect(
            f"{path.resolve().as_uri()}?mode=rw", uri=True, autocommit=False
        )
    except sqlite3.Error as error:
        raise StorageError(f"cannot open {path}: {error}") from error
    try:
        schema_version = connection.execute("PRAGMA user_version").fetchone()[0]
    except sqlite3.DatabaseError as error:
        connection.close()
        raise StorageError(f"{path} is not a watcher database: {error}") from error
    if schema_version != SCHEMA_VERSION:
        connection.close()
        raise StorageError(
            f"{path} has schema version {schema_version}, expected {SCHEMA_VERSION}"
        )
    return connection

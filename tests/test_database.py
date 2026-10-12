import sqlite3
import stat
from contextlib import closing

import pytest
from samples import WATCHED_CHAT

from group_watcher.storage.checkpoint_repository import CheckpointRepository
from group_watcher.storage.database import (
    StorageError,
    create_database,
    open_database,
)


def test_create_refuses_to_overwrite_existing_file(tmp_path):
    path = tmp_path / "watcher.db"
    path.write_text("existing data")
    with pytest.raises(StorageError):
        create_database(path)
    assert path.read_text() == "existing data"


def test_open_refuses_missing_database_without_creating_it(tmp_path):
    path = tmp_path / "watcher.db"
    with pytest.raises(StorageError):
        open_database(path)
    assert not path.exists()


def test_open_refuses_file_that_is_not_a_database(tmp_path):
    path = tmp_path / "watcher.db"
    path.write_text("not a database")
    with pytest.raises(StorageError):
        open_database(path)


def test_open_refuses_unknown_schema_version(db_path):
    with closing(sqlite3.connect(db_path)) as connection:
        connection.execute("PRAGMA user_version = 99")
    with pytest.raises(StorageError):
        open_database(db_path)


def test_database_files_are_owner_only(db_path, connection):
    with connection:
        CheckpointRepository(connection).advance(WATCHED_CHAT, 101)
    database_files = sorted(db_path.parent.glob("watcher.db*"))
    assert [path.name for path in database_files] == [
        "watcher.db",
        "watcher.db-shm",
        "watcher.db-wal",
    ]
    for path in database_files:
        assert stat.S_IMODE(path.stat().st_mode) == 0o600

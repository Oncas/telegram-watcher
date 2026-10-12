import pytest

from group_watcher.storage.database import create_database, open_database


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "watcher.db"
    create_database(path)
    return path


@pytest.fixture
def connection(db_path):
    connection = open_database(db_path)
    yield connection
    connection.close()

import pytest
from fakes import FakeEndpoint, FakeNotifySend

from telegram_watcher.image_store import ImageStore
from telegram_watcher.storage.database import create_database, open_database


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


@pytest.fixture
def image_store(tmp_path):
    return ImageStore(tmp_path / "media")


@pytest.fixture
def notify_send(tmp_path_factory, monkeypatch):
    directory = tmp_path_factory.mktemp("bin")
    monkeypatch.setenv("PATH", str(directory))
    return FakeNotifySend(directory)


@pytest.fixture
def endpoint():
    endpoint = FakeEndpoint()
    yield endpoint
    endpoint.close()

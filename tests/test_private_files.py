import stat

import pytest

from telegram_watcher.credentials import (
    Credentials,
    CredentialsError,
    load_credentials,
    save_credentials,
)
from telegram_watcher.private_files import (
    PrivateFileError,
    read_private_file,
    write_private_file,
)

CREDENTIALS = Credentials(api_id=12345, api_hash="0123456789abcdef0123456789abcdef")


def test_private_file_is_written_owner_only(tmp_path):
    path = tmp_path / "secret"
    write_private_file(path, "first")
    assert read_private_file(path) == "first"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_rewriting_replaces_content_and_tightens_permissions(tmp_path):
    path = tmp_path / "secret"
    path.write_text("old")
    path.chmod(0o644)
    write_private_file(path, "new")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert path.read_text() == "new"
    assert [entry.name for entry in tmp_path.iterdir()] == ["secret"]


def test_temporary_file_left_by_an_interrupted_write_is_replaced(tmp_path):
    (tmp_path / ".secret.tmp").write_text("partial")
    write_private_file(tmp_path / "secret", "complete")
    assert [entry.name for entry in tmp_path.iterdir()] == ["secret"]
    assert read_private_file(tmp_path / "secret") == "complete"


def test_file_readable_by_others_is_refused(tmp_path):
    path = tmp_path / "secret"
    path.write_text("exposed")
    path.chmod(0o640)
    with pytest.raises(PrivateFileError, match="chmod 600"):
        read_private_file(path)


def test_missing_file_reads_as_none(tmp_path):
    assert read_private_file(tmp_path / "missing") is None


def test_credentials_round_trip(tmp_path):
    path = tmp_path / "credentials.toml"
    save_credentials(path, CREDENTIALS)
    assert load_credentials(path) == CREDENTIALS


def test_api_hash_is_kept_out_of_repr():
    assert CREDENTIALS.api_hash not in repr(CREDENTIALS)


@pytest.mark.parametrize(
    "text",
    [
        'api_id = "12345"\napi_hash = "0123456789abcdef0123456789abcdef"\n',
        'api_id = 12345\napi_hash = "not-a-hash"\n',
        'api_id = 12345\napi_hash = "0123456789abcdef0123456789abcdef0"\n',
        'api_id = 0\napi_hash = "0123456789abcdef0123456789abcdef"\n',
        "api_id = 12345\n",
        'api_id = 12345\napi_hash = "0123456789abcdef0123456789abcdef"\nphone = 1\n',
        "api_id = \n",
    ],
)
def test_malformed_credentials_are_refused_without_echoing_them(tmp_path, text):
    path = tmp_path / "credentials.toml"
    write_private_file(path, text)
    with pytest.raises(CredentialsError) as raised:
        load_credentials(path)
    assert "0123456789abcdef" not in str(raised.value)


def test_missing_credentials_say_how_to_create_them(tmp_path):
    with pytest.raises(CredentialsError, match="login"):
        load_credentials(tmp_path / "credentials.toml")

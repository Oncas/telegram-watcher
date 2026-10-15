import sqlite3
import stat
from contextlib import closing

import pytest
from samples import SECOND_CHANNEL, T0, WATCHED_CHANNEL, WATCHED_CHAT

from group_watcher.cli import main
from group_watcher.client import HaltReason
from group_watcher.config import Config, save_config
from group_watcher.credentials import Credentials, save_credentials
from group_watcher.matches import Match
from group_watcher.private_files import write_private_file
from group_watcher.run_lock import run_lock
from group_watcher.storage.account_state_repository import AccountStateRepository
from group_watcher.storage.database import open_database
from group_watcher.storage.match_repository import MatchRepository

SYNTHETIC_HASH = "0123456789abcdef0123456789abcdef"
SESSION_ENDED_NOTICE = (
    "Telegram watcher halted: the session ended. Log in again, then clear the halt."
)


@pytest.fixture(autouse=True)
def in_project_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)


@pytest.fixture
def var(tmp_path):
    assert main(["init"]) == 0
    return tmp_path / "var"


@pytest.fixture
def configured(var):
    save_config(var / "config.toml", Config(chats=(WATCHED_CHANNEL, SECOND_CHANNEL)))
    save_credentials(
        var / "credentials.toml", Credentials(api_id=12345, api_hash=SYNTHETIC_HASH)
    )
    return var


def halt_reason(var):
    with closing(open_database(var / "watcher.db")) as connection:
        return AccountStateRepository(connection).halt_reason()


def run_outcomes(var):
    with closing(sqlite3.connect(var / "watcher.db")) as connection:
        return [
            outcome
            for (outcome,) in connection.execute(
                "SELECT outcome FROM runs ORDER BY started_at"
            )
        ]


def test_init_creates_a_private_runtime_directory_with_a_database(var):
    assert stat.S_IMODE(var.stat().st_mode) == 0o700
    assert halt_reason(var) is None


def test_run_without_a_session_halts_and_says_how_to_recover(configured, notify_send):
    assert main(["run"]) == 0
    assert halt_reason(configured) == HaltReason.SESSION_ENDED
    assert run_outcomes(configured) == ["halted"]
    assert notify_send.messages == [SESSION_ENDED_NOTICE]


def test_pending_matches_are_forwarded_and_notified_even_while_halted(
    configured, notify_send, endpoint
):
    write_private_file(
        configured / "forward.toml", f'url = "{endpoint.url}"\ntoken = "synthetic"\n'
    )
    match = Match(
        listener="whole_text",
        chat_id=WATCHED_CHAT,
        message_id=101,
        posted_at=T0,
        edited_at=None,
        data={"text": "Synthetic text post"},
    )
    with closing(open_database(configured / "watcher.db")) as connection, connection:
        MatchRepository(connection).add(match, T0)
    assert main(["run"]) == 0
    assert [payload["data"] for payload in endpoint.payloads] == [match.data]
    assert notify_send.messages == [
        "whole_text\ntext: Synthetic text post",
        SESSION_ENDED_NOTICE,
    ]


def test_init_never_replaces_an_existing_database(configured, notify_send):
    main(["run"])
    assert main(["init"]) == 1
    assert halt_reason(configured) == HaltReason.SESSION_ENDED


def test_clearing_the_halt_resumes_runs(configured, notify_send):
    main(["run"])
    assert main(["clear-halt"]) == 0
    assert halt_reason(configured) is None


def test_invalid_config_stops_the_run_without_touching_the_database(
    configured, notify_send
):
    (configured / "config.toml").write_text("chats = []\n")
    assert main(["run"]) == 1
    assert run_outcomes(configured) == []
    assert halt_reason(configured) is None
    assert notify_send.messages == [
        "Telegram watcher cannot run: ConfigError. See the log."
    ]


def test_forward_settings_readable_by_others_stop_the_run(configured, notify_send):
    forward = configured / "forward.toml"
    forward.write_text('url = "http://127.0.0.1/"\ntoken = "synthetic"\n')
    forward.chmod(0o644)
    assert main(["run"]) == 1
    assert run_outcomes(configured) == []
    assert notify_send.messages == [
        "Telegram watcher cannot run: ConfigError. See the log."
    ]


def test_missing_credentials_stop_the_run(configured, notify_send):
    (configured / "credentials.toml").unlink()
    assert main(["run"]) == 1
    assert run_outcomes(configured) == []
    assert notify_send.messages == [
        "Telegram watcher cannot run: CredentialsError. See the log."
    ]


def test_run_before_init_stops_and_notifies(notify_send):
    assert main(["run"]) == 1
    assert len(notify_send.messages) == 1


def test_run_is_skipped_while_another_run_holds_the_lock(configured, notify_send):
    with run_lock(configured / "run.lock"):
        assert main(["run"]) == 0
    assert run_outcomes(configured) == []
    assert notify_send.messages == []


def test_session_readable_by_others_fails_the_run(configured, notify_send):
    session = configured / "telegram.session"
    session.write_text("synthetic")
    session.chmod(0o644)
    assert main(["run"]) == 1
    assert run_outcomes(configured) == ["failed"]
    assert notify_send.messages == ["Telegram watcher run failed: PrivateFileError."]


def test_pick_chats_never_overwrites_an_existing_config(configured, capsys):
    config = (configured / "config.toml").read_text()
    assert main(["pick-chats"]) == 1
    assert "config.toml exists" in capsys.readouterr().err
    assert (configured / "config.toml").read_text() == config


def test_pick_chats_says_when_there_are_no_chats_to_watch(var, monkeypatch, capsys):
    save_credentials(
        var / "credentials.toml", Credentials(api_id=12345, api_hash=SYNTHETIC_HASH)
    )
    write_private_file(var / "telegram.session", "synthetic")

    async def no_chats(credentials, session):
        return []

    monkeypatch.setattr("group_watcher.cli.list_chats", no_chats)
    assert main(["pick-chats"]) == 1
    assert "no chats that can be watched" in capsys.readouterr().err
    assert not (var / "config.toml").exists()


@pytest.mark.parametrize(
    ("api_id", "api_hash"), [("not a number", SYNTHETIC_HASH), ("12345", "short")]
)
def test_login_refuses_malformed_credentials_without_saving_or_echoing_them(
    var, monkeypatch, capsys, api_id, api_hash
):
    monkeypatch.setattr("builtins.input", lambda prompt: api_id)
    monkeypatch.setattr("group_watcher.cli.getpass", lambda prompt: api_hash)
    assert main(["login"]) == 1
    assert not (var / "credentials.toml").exists()
    output = capsys.readouterr()
    assert api_hash not in output.out + output.err

import logging
from contextlib import closing
from datetime import timedelta

import pytest
from builders import make_poll_run, make_recorder
from events import read_events
from fakes import FakeClock, FakeTelegram
from samples import (
    SECOND_CHANNEL,
    SECOND_CHAT,
    SECOND_CHAT_POST,
    T0,
    TEXT_POST,
    UNRELATED_POST,
    WATCHED_CHANNEL,
    WATCHED_CHAT,
)

from telegram_watcher.client import (
    AuthorizationFailed,
    ChatUnavailable,
    HaltReason,
    NetworkUnavailable,
    RateLimited,
)
from telegram_watcher.poll_run import RunOutcome
from telegram_watcher.storage.database import open_database

CHATS = (WATCHED_CHANNEL, SECOND_CHANNEL)

HOUR_1 = T0 + timedelta(hours=1)
HOUR_3 = T0 + timedelta(hours=3)


@pytest.fixture(autouse=True)
def watching_from_message_100(connection):
    recorder = make_recorder(connection)
    for chat in CHATS:
        recorder.record_starting_point(chat.chat_id, 100)


@pytest.fixture
def telegram():
    return FakeTelegram(TEXT_POST, SECOND_CHAT_POST, UNRELATED_POST)


@pytest.fixture
def clock():
    return FakeClock(HOUR_1)


@pytest.fixture
def run_once(db_path, telegram, clock):
    def run_once():
        with closing(open_database(db_path)) as connection:
            return make_poll_run(
                connection,
                chats=CHATS,
                telegram=telegram,
                clock=clock,
            ).run()

    return run_once


def test_run_saves_new_posts_from_every_configured_chat(db_path, run_once):
    assert run_once().outcome == RunOutcome.COMPLETED
    assert read_events(db_path, "kind", "chat_id", "message_id") == [
        ("new", SECOND_CHAT, 101),
        ("new", WATCHED_CHAT, 101),
    ]


def test_run_requests_only_configured_chats(telegram, run_once):
    run_once()
    assert set(telegram.requested_chat_ids) == {WATCHED_CHAT, SECOND_CHAT}


def test_unavailable_chat_does_not_stop_the_others(db_path, telegram, run_once):
    telegram.chat_failures[WATCHED_CHAT] = ChatUnavailable()
    report = run_once()
    assert report.outcome == RunOutcome.COMPLETED
    assert report.polled_chats == (SECOND_CHANNEL,)
    assert report.unavailable_chats == (WATCHED_CHANNEL,)
    assert read_events(db_path, "chat_id") == [(SECOND_CHAT,)]


def test_network_failure_while_connecting_ends_run(telegram, run_once):
    telegram.connect_failure = NetworkUnavailable()
    assert run_once().outcome == RunOutcome.NETWORK_UNAVAILABLE


def test_network_failure_mid_run_skips_remaining_chats(db_path, telegram, run_once):
    telegram.chat_failures[WATCHED_CHAT] = NetworkUnavailable()
    assert run_once().outcome == RunOutcome.NETWORK_UNAVAILABLE
    assert telegram.requested_chat_ids == [WATCHED_CHAT]
    assert read_events(db_path, "kind") == []


def test_rate_limit_ends_run_without_trying_other_chats(telegram, run_once):
    telegram.chat_failures[WATCHED_CHAT] = RateLimited(timedelta(hours=2))
    assert run_once().outcome == RunOutcome.RATE_LIMITED
    assert telegram.requested_chat_ids == [WATCHED_CHAT]


def test_rate_limit_deadline_holds_across_runs(telegram, clock, run_once):
    telegram.chat_failures[WATCHED_CHAT] = RateLimited(timedelta(hours=2))
    run_once()
    del telegram.chat_failures[WATCHED_CHAT]
    clock.now = HOUR_3 - timedelta(seconds=1)
    assert run_once().outcome == RunOutcome.RATE_LIMITED
    assert telegram.connections == 1
    clock.now = HOUR_3
    assert run_once().outcome == RunOutcome.COMPLETED
    assert telegram.connections == 2


@pytest.mark.parametrize("reason", list(HaltReason))
def test_authorization_failure_halts_every_later_run(telegram, clock, run_once, reason):
    telegram.connect_failure = AuthorizationFailed(reason)
    first_report = run_once()
    telegram.connect_failure = None
    clock.now = HOUR_1 + timedelta(days=30)
    later_report = run_once()
    assert telegram.connections == 0
    for report in [first_report, later_report]:
        assert (report.outcome, report.halt_reason) == (RunOutcome.HALTED, reason)


def test_unexpected_error_ends_run_and_logs_no_message_content(
    telegram, run_once, caplog
):
    caplog.set_level(logging.INFO)
    telegram.chat_failures[WATCHED_CHAT] = RuntimeError(TEXT_POST.text)
    assert run_once().outcome == RunOutcome.FAILED
    assert "RuntimeError" in caplog.text
    assert TEXT_POST.text not in caplog.text

import sqlite3
from contextlib import closing, contextmanager
from datetime import timedelta

import pytest
from builders import run_hourly
from fakes import FakeClock, FakeNotifier, FakeTelegram
from samples import SECOND_CHANNEL, T0, WATCHED_CHANNEL, WATCHED_CHAT

from group_watcher.client import AuthorizationFailed, ChatUnavailable, HaltReason
from group_watcher.poll_run import HALT_NOTICES

CHATS = (WATCHED_CHANNEL, SECOND_CHANNEL)

HOUR_1 = T0 + timedelta(hours=1)

UNAVAILABLE = "Telegram watcher could not poll Synthetic channel."
STALE = "Telegram watcher has not polled Synthetic channel for over 24 hours."


def hours_later(hours):
    return HOUR_1 + timedelta(hours=hours)


@pytest.fixture
def telegram():
    return FakeTelegram()


@pytest.fixture
def notifier():
    return FakeNotifier()


@pytest.fixture
def clock():
    return FakeClock(HOUR_1)


@pytest.fixture
def run_once(db_path, telegram, notifier, clock):
    def run_once():
        run_hourly(
            db_path, chats=CHATS, telegram=telegram, notifier=notifier, clock=clock
        )

    return run_once


def test_each_run_is_logged_with_its_times_and_outcome(
    db_path, telegram, clock, run_once
):
    instant_connect = telegram.connect

    @contextmanager
    def two_minute_connect():
        with instant_connect() as client:
            clock.now += timedelta(minutes=2)
            yield client

    telegram.connect = two_minute_connect
    run_once()
    clock.now = hours_later(1)
    telegram.chat_failures[WATCHED_CHAT] = RuntimeError()
    run_once()
    with closing(sqlite3.connect(db_path)) as connection:
        runs = connection.execute(
            "SELECT started_at, finished_at, outcome, error_type FROM runs"
            " ORDER BY started_at"
        ).fetchall()
    assert runs == [
        (
            "2026-01-05T10:00:00+00:00",
            "2026-01-05T10:02:00+00:00",
            "completed",
            None,
        ),
        (
            "2026-01-05T11:00:00+00:00",
            "2026-01-05T11:02:00+00:00",
            "failed",
            "RuntimeError",
        ),
    ]


def test_unavailable_chat_notifies_once_while_it_stays_unavailable(
    telegram, notifier, clock, run_once
):
    telegram.chat_failures[WATCHED_CHAT] = ChatUnavailable()
    run_once()
    clock.now = hours_later(1)
    run_once()
    assert notifier.messages == [UNAVAILABLE]


def test_chat_is_not_stale_until_a_full_day_has_passed(
    telegram, notifier, clock, run_once
):
    telegram.chat_failures[WATCHED_CHAT] = ChatUnavailable()
    run_once()
    clock.now = hours_later(24)
    run_once()
    assert notifier.messages == [UNAVAILABLE]


def test_chat_without_successful_poll_for_over_a_day_notifies_once(
    telegram, notifier, clock, run_once
):
    telegram.chat_failures[WATCHED_CHAT] = ChatUnavailable()
    for hours in [0, 25, 26]:
        clock.now = hours_later(hours)
        run_once()
    assert notifier.messages == [UNAVAILABLE, STALE]


def test_halted_runs_raise_no_stale_alerts(telegram, notifier, clock, run_once):
    telegram.connect_failure = AuthorizationFailed(HaltReason.SESSION_ENDED)
    run_once()
    clock.now = hours_later(24 * 30)
    run_once()
    assert notifier.messages == [HALT_NOTICES[HaltReason.SESSION_ENDED]]


def test_alerts_repeat_only_after_the_chat_recovers(
    telegram, notifier, clock, run_once
):
    telegram.chat_failures[WATCHED_CHAT] = ChatUnavailable()
    run_once()
    clock.now = hours_later(25)
    run_once()
    del telegram.chat_failures[WATCHED_CHAT]
    clock.now = hours_later(26)
    run_once()
    telegram.chat_failures[WATCHED_CHAT] = ChatUnavailable()
    clock.now = hours_later(27)
    run_once()
    clock.now = hours_later(51)
    run_once()
    assert notifier.messages == [UNAVAILABLE, STALE, UNAVAILABLE, STALE]


def test_repeated_identical_failures_notify_once(telegram, notifier, clock, run_once):
    def run_at(hours, error=None):
        clock.now = hours_later(hours)
        telegram.chat_failures = {WATCHED_CHAT: error} if error else {}
        run_once()

    run_at(0, RuntimeError())
    run_at(1, RuntimeError())
    run_at(2, ValueError())
    run_at(3)
    run_at(4, RuntimeError())
    assert notifier.messages == [
        "Telegram watcher run failed: RuntimeError.",
        "Telegram watcher run failed: ValueError.",
        "Telegram watcher run failed: RuntimeError.",
    ]

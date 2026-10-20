from contextlib import closing
from datetime import timedelta

import pytest
from builders import run_hourly
from fakes import FakeClock, FakeLiveTelegram, FakeNotifier, FakeTelegram
from samples import (
    CAPTION_POST,
    PHOTO_CHANNEL,
    PHOTO_POST,
    T0,
    TEXT_POST,
    WATCHED_CHANNEL,
    WATCHED_CHAT,
)

from telegram_watcher.client import HaltReason, NetworkUnavailable
from telegram_watcher.listeners import Listener
from telegram_watcher.poll_run import RunOutcome
from telegram_watcher.storage.account_state_repository import AccountStateRepository
from telegram_watcher.storage.database import open_database
from telegram_watcher.watcher import watch_live


def whole_text(post):
    yield {"text": post.text}


WHOLE_TEXT = Listener(name="whole_text", chat_id=WATCHED_CHAT, listen=whole_text)


@pytest.fixture
def telegram():
    return FakeTelegram()


@pytest.fixture
def notifier():
    return FakeNotifier()


@pytest.fixture
def watch(db_path, notifier, image_store):
    def watch(live):
        with closing(open_database(db_path)) as connection:
            return watch_live(
                connection,
                chats=(WATCHED_CHANNEL,),
                telegram=live,
                listeners=(WHOLE_TEXT,),
                notifier=notifier,
                forwarder=None,
                image_store=image_store,
                clock=FakeClock(T0),
            )

    return watch


def lose_connection(telegram):
    def lose():
        telegram.connect_failure = NetworkUnavailable()

    return lose


def test_listener_polls_at_start_and_after_every_update(watch, telegram, notifier):
    live = FakeLiveTelegram(
        telegram,
        lambda: telegram.publish(TEXT_POST),
        lambda: telegram.publish(CAPTION_POST),
        lose_connection(telegram),
    )
    assert watch(live) is RunOutcome.NETWORK_UNAVAILABLE
    assert notifier.messages == [
        "whole_text\ntext: Synthetic text post",
        "whole_text\ntext: Synthetic caption",
    ]


def test_listener_polls_at_least_every_15_minutes_without_updates(watch, telegram):
    live = FakeLiveTelegram(telegram, lambda: None, lose_connection(telegram))
    watch(live)
    assert live.waits == [timedelta(minutes=15)] * 2


def test_halted_listener_stops_without_connecting(watch, connection, telegram):
    with connection:
        AccountStateRepository(connection).halt(HaltReason.SESSION_ENDED)
    live = FakeLiveTelegram(telegram)
    assert watch(live) is RunOutcome.HALTED
    assert telegram.connections == 0
    assert live.waits == []


def test_each_run_clears_the_unfinished_files_of_an_interrupted_run(
    db_path, tmp_path, telegram, notifier, image_store
):
    leftover = tmp_path / "media" / f".{WATCHED_CHAT}_103.jpg.part"
    leftover.parent.mkdir()
    leftover.write_bytes(b"partial")
    run_hourly(
        db_path,
        chats=(WATCHED_CHANNEL,),
        telegram=telegram,
        notifier=notifier,
        clock=FakeClock(T0),
        image_store=image_store,
    )
    assert not leftover.exists()


def test_run_saves_the_images_of_a_photo_chat(
    db_path, tmp_path, telegram, notifier, image_store
):
    def run():
        run_hourly(
            db_path,
            chats=(PHOTO_CHANNEL,),
            telegram=telegram,
            notifier=notifier,
            clock=FakeClock(T0),
            image_store=image_store,
        )

    run()
    telegram.publish(PHOTO_POST)
    run()
    assert [path.name for path in (tmp_path / "media" / "downloaded").iterdir()] == [
        f"{WATCHED_CHAT}_103.jpg"
    ]

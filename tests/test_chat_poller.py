from dataclasses import replace
from datetime import timedelta

import pytest
from builders import make_chat_poller, make_recorder
from events import read_events
from fakes import FakeClock, FakeTelegram
from samples import (
    ALBUM_POSTS,
    CAPTION_POST,
    EDITED_TEXT_POST,
    PHOTO_POST,
    T0,
    TEXT_POST,
    WATCHED_CHANNEL,
    WATCHED_CHAT,
)

from telegram_watcher.client import NetworkUnavailable

HOUR_1 = T0 + timedelta(hours=1)
HOUR_2 = T0 + timedelta(hours=2)

FOUR_DAY_OLD_POST = replace(PHOTO_POST, posted_at=T0 - timedelta(days=4))


@pytest.fixture
def clock():
    return FakeClock(HOUR_1)


@pytest.fixture
def poller(connection, clock):
    return make_chat_poller(connection, clock)


def test_first_poll_starts_after_latest_post_without_importing_history(db_path, poller):
    telegram = FakeTelegram(TEXT_POST, CAPTION_POST, PHOTO_POST)
    with telegram.connect() as client:
        poller.poll(client, WATCHED_CHANNEL)
        assert read_events(db_path, "kind") == []
        telegram.publish(*ALBUM_POSTS)
        poller.poll(client, WATCHED_CHANNEL)
    assert read_events(db_path, "kind", "message_id") == [
        ("new", 104),
        ("new", 105),
        ("new", 106),
    ]


def test_first_poll_of_empty_chat_keeps_its_first_post(db_path, poller):
    telegram = FakeTelegram()
    with telegram.connect() as client:
        poller.poll(client, WATCHED_CHANNEL)
        telegram.publish(TEXT_POST)
        poller.poll(client, WATCHED_CHANNEL)
    assert read_events(db_path, "kind", "message_id") == [("new", 101)]


def test_catch_up_is_capped_per_poll_and_resumes_on_next_poll(
    db_path, connection, clock
):
    poller = make_chat_poller(connection, clock, page_size=2, max_pages=2)
    telegram = FakeTelegram()
    with telegram.connect() as client:
        poller.poll(client, WATCHED_CHANNEL)
        telegram.publish(TEXT_POST, CAPTION_POST, PHOTO_POST, *ALBUM_POSTS)
        poller.poll(client, WATCHED_CHANNEL)
        assert read_events(db_path, "message_id") == [(101,), (102,), (103,), (104,)]
        poller.poll(client, WATCHED_CHANNEL)
    assert read_events(db_path, "message_id") == [
        (101,),
        (102,),
        (103,),
        (104,),
        (105,),
        (106,),
    ]


def test_short_page_ends_fetching_without_another_request(connection, clock):
    poller = make_chat_poller(connection, clock, page_size=2)
    telegram = FakeTelegram()
    with telegram.connect() as client:
        poller.poll(client, WATCHED_CHANNEL)
        telegram.publish(TEXT_POST, CAPTION_POST, PHOTO_POST)
        poller.poll(client, WATCHED_CHANNEL)
    assert telegram.fetched_after_ids == [0, 102]


def test_service_messages_move_the_checkpoint_without_being_saved(
    db_path, connection, clock
):
    poller = make_chat_poller(connection, clock, page_size=2, max_pages=1)
    telegram = FakeTelegram()
    with telegram.connect() as client:
        poller.poll(client, WATCHED_CHANNEL)
        telegram.publish_service_messages(WATCHED_CHAT, 101, 102)
        telegram.publish(PHOTO_POST)
        poller.poll(client, WATCHED_CHANNEL)
        poller.poll(client, WATCHED_CHANNEL)
    assert telegram.fetched_after_ids == [0, 102]
    assert read_events(db_path, "kind", "message_id") == [("new", 103)]


def test_recheck_records_edits_and_deletions(db_path, connection, clock, poller):
    make_recorder(connection).record_new_posts(
        WATCHED_CHAT, [TEXT_POST, PHOTO_POST], HOUR_1
    )
    telegram = FakeTelegram(EDITED_TEXT_POST)
    clock.now = HOUR_2
    with telegram.connect() as client:
        poller.poll(client, WATCHED_CHANNEL)
    assert read_events(db_path, "kind", "message_id", "fetched_at") == [
        ("new", 101, "2026-01-05T10:00:00+00:00"),
        ("new", 103, "2026-01-05T10:00:00+00:00"),
        ("edited", 101, "2026-01-05T11:00:00+00:00"),
        ("deleted", 103, "2026-01-05T11:00:00+00:00"),
    ]


def test_recheck_skips_posts_older_than_the_window(db_path, connection, poller):
    make_recorder(connection).record_new_posts(
        WATCHED_CHAT, [TEXT_POST, FOUR_DAY_OLD_POST], HOUR_1
    )
    telegram = FakeTelegram()
    with telegram.connect() as client:
        poller.poll(client, WATCHED_CHANNEL)
    assert read_events(db_path, "kind", "message_id") == [
        ("deleted", 101),
        ("new", 101),
        ("new", 103),
    ]


def test_recheck_skips_posts_already_marked_deleted(connection, clock, poller):
    make_recorder(connection).record_new_posts(
        WATCHED_CHAT, [TEXT_POST, PHOTO_POST], HOUR_1
    )
    telegram = FakeTelegram(TEXT_POST)
    with telegram.connect() as client:
        poller.poll(client, WATCHED_CHANNEL)
        clock.now = HOUR_2
        poller.poll(client, WATCHED_CHANNEL)
    assert telegram.rechecked_message_ids == [[101, 103], [101]]


def test_poll_with_no_recent_posts_sends_no_recheck(poller):
    telegram = FakeTelegram()
    with telegram.connect() as client:
        poller.poll(client, WATCHED_CHANNEL)
        poller.poll(client, WATCHED_CHANNEL)
    assert telegram.rechecked_message_ids == []


def test_failed_recheck_marks_nothing_deleted(db_path, connection, poller):
    make_recorder(connection).record_new_posts(WATCHED_CHAT, [TEXT_POST], HOUR_1)
    telegram = FakeTelegram()
    telegram.recheck_failures[WATCHED_CHAT] = NetworkUnavailable()
    with telegram.connect() as client, pytest.raises(NetworkUnavailable):
        poller.poll(client, WATCHED_CHANNEL)
    assert read_events(db_path, "kind", "message_id") == [("new", 101)]

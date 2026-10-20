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
    IMAGE_FILE_POST,
    OUTGOING_PHOTO_POST,
    PHOTO_CHANNEL,
    PHOTO_POST,
    T0,
    TEXT_POST,
    UNRELATED_CHAT,
    VIDEO_POST,
    WATCHED_CHANNEL,
    WATCHED_CHAT,
)

from telegram_watcher.allowlist import PostFromOtherChatError
from telegram_watcher.client import NetworkUnavailable, Page

HOUR_1 = T0 + timedelta(hours=1)
HOUR_2 = T0 + timedelta(hours=2)

FOUR_DAY_OLD_POST = replace(PHOTO_POST, posted_at=T0 - timedelta(days=4))


@pytest.fixture
def clock():
    return FakeClock(HOUR_1)


@pytest.fixture
def poller(connection, clock, image_store):
    return make_chat_poller(connection, clock, image_store)


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
    db_path, connection, clock, image_store
):
    poller = make_chat_poller(connection, clock, image_store, page_size=2, max_pages=2)
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


def test_short_page_ends_fetching_without_another_request(
    connection, clock, image_store
):
    poller = make_chat_poller(connection, clock, image_store, page_size=2)
    telegram = FakeTelegram()
    with telegram.connect() as client:
        poller.poll(client, WATCHED_CHANNEL)
        telegram.publish(TEXT_POST, CAPTION_POST, PHOTO_POST)
        poller.poll(client, WATCHED_CHANNEL)
    assert telegram.fetched_after_ids == [0, 102]


def test_service_messages_move_the_checkpoint_without_being_saved(
    db_path, connection, clock, image_store
):
    poller = make_chat_poller(connection, clock, image_store, page_size=2, max_pages=1)
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


@pytest.fixture
def media(tmp_path):
    return tmp_path / "media"


def stored_names(directory):
    return sorted(entry.name for entry in directory.iterdir())


def poll_new_posts(poller, telegram, chat, *posts):
    with telegram.connect() as client:
        poller.poll(client, chat)
        telegram.publish(*posts)
        poller.poll(client, chat)


def test_photo_chat_saves_every_image_of_an_album_as_its_own_file(media, poller):
    poll_new_posts(poller, FakeTelegram(), PHOTO_CHANNEL, *ALBUM_POSTS)
    assert stored_names(media / "downloaded") == [
        f"{WATCHED_CHAT}_104.jpg",
        f"{WATCHED_CHAT}_105.jpg",
        f"{WATCHED_CHAT}_106.jpg",
    ]
    saved = media / "downloaded" / f"{WATCHED_CHAT}_105.jpg"
    assert saved.read_bytes() == b"image 105"


def test_photo_chat_saves_photos_and_image_files_but_nothing_else(media, poller):
    telegram = FakeTelegram()
    poll_new_posts(
        poller,
        telegram,
        PHOTO_CHANNEL,
        TEXT_POST,
        CAPTION_POST,
        IMAGE_FILE_POST,
        VIDEO_POST,
    )
    assert telegram.requested_image_ids == [102, 107]
    assert stored_names(media / "downloaded") == [
        f"{WATCHED_CHAT}_102.jpg",
        f"{WATCHED_CHAT}_107.jpg",
    ]


def test_photo_chat_skips_posts_sent_by_the_account_itself(media, poller):
    telegram = FakeTelegram()
    poll_new_posts(poller, telegram, PHOTO_CHANNEL, OUTGOING_PHOTO_POST)
    assert telegram.requested_image_ids == []
    assert not media.exists()


def test_chat_without_photo_downloads_never_asks_for_images(media, poller):
    telegram = FakeTelegram()
    poll_new_posts(poller, telegram, WATCHED_CHANNEL, PHOTO_POST, IMAGE_FILE_POST)
    assert telegram.requested_image_ids == []
    assert not media.exists()


def test_first_poll_of_a_photo_chat_downloads_no_history(media, poller):
    telegram = FakeTelegram(PHOTO_POST)
    with telegram.connect() as client:
        poller.poll(client, PHOTO_CHANNEL)
    assert telegram.requested_image_ids == []
    assert not media.exists()


def test_recheck_of_recent_posts_downloads_nothing(connection, media, poller):
    make_recorder(connection).record_new_posts(WATCHED_CHAT, [PHOTO_POST], HOUR_1)
    telegram = FakeTelegram(PHOTO_POST)
    with telegram.connect() as client:
        poller.poll(client, PHOTO_CHANNEL)
    assert telegram.rechecked_message_ids == [[103]]
    assert telegram.requested_image_ids == []
    assert not media.exists()


def test_failed_download_keeps_the_checkpoint_so_the_next_poll_retries(
    db_path, media, poller
):
    telegram = FakeTelegram()
    with telegram.connect() as client:
        poller.poll(client, PHOTO_CHANNEL)
        telegram.publish(*ALBUM_POSTS)
        telegram.image_failures[105] = NetworkUnavailable()
        with pytest.raises(NetworkUnavailable):
            poller.poll(client, PHOTO_CHANNEL)
        assert read_events(db_path, "kind") == []
        del telegram.image_failures[105]
        poller.poll(client, PHOTO_CHANNEL)
    assert telegram.requested_image_ids == [104, 105, 105, 106]
    assert stored_names(media / "downloaded") == [
        f"{WATCHED_CHAT}_104.jpg",
        f"{WATCHED_CHAT}_105.jpg",
        f"{WATCHED_CHAT}_106.jpg",
    ]
    assert read_events(db_path, "kind", "message_id") == [
        ("new", 104),
        ("new", 105),
        ("new", 106),
    ]


@pytest.mark.parametrize("folder", ["downloaded", "rejected", "archived"])
def test_image_already_in_any_folder_is_not_downloaded_again(media, poller, folder):
    (media / folder).mkdir(parents=True)
    (media / folder / f"{WATCHED_CHAT}_105.png").write_bytes(b"moved earlier")
    telegram = FakeTelegram()
    poll_new_posts(poller, telegram, PHOTO_CHANNEL, *ALBUM_POSTS)
    assert telegram.requested_image_ids == [104, 106]
    assert (media / folder / f"{WATCHED_CHAT}_105.png").read_bytes() == b"moved earlier"


def test_post_without_a_downloadable_image_is_recorded_without_a_file(
    db_path, media, poller
):
    telegram = FakeTelegram()
    telegram.imageless_message_ids.add(107)
    poll_new_posts(poller, telegram, PHOTO_CHANNEL, IMAGE_FILE_POST)
    assert not media.exists()
    assert read_events(db_path, "kind", "message_id") == [("new", 107)]


def test_post_from_another_chat_is_rejected_before_any_image_is_saved(
    db_path, media, poller
):
    telegram = FakeTelegram()
    with telegram.connect() as client:
        poller.poll(client, PHOTO_CHANNEL)
        stray = replace(PHOTO_POST, chat_id=UNRELATED_CHAT, message_id=110)
        telegram.fetch_after = lambda chat, message_id, limit: Page(
            posts=[PHOTO_POST, stray], last_message_id=110, reached_end=True
        )
        with pytest.raises(PostFromOtherChatError):
            poller.poll(client, PHOTO_CHANNEL)
    assert telegram.requested_image_ids == []
    assert not media.exists()
    assert read_events(db_path, "kind") == []

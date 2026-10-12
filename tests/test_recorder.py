from contextlib import closing
from dataclasses import replace
from datetime import timedelta

import pytest
from builders import make_recorder
from events import read_events
from samples import (
    ALBUM_POSTS,
    CAPTION_POST,
    EDITED_TEXT_POST,
    PHOTO_POST,
    T0,
    TEXT_POST,
    UNRELATED_CHAT,
    UNRELATED_POST,
    WATCHED_CHAT,
)

from group_watcher.allowlist import PostFromOtherChatError
from group_watcher.storage.checkpoint_repository import CheckpointRepository
from group_watcher.storage.database import open_database

HOUR_1 = T0 + timedelta(hours=1)
HOUR_2 = T0 + timedelta(hours=2)
HOUR_3 = T0 + timedelta(hours=3)

UNSTORABLE_POST = replace(PHOTO_POST, message_id=2**63)


@pytest.fixture
def recorder(connection):
    return make_recorder(connection)


@pytest.fixture
def checkpoints(connection):
    return CheckpointRepository(connection)


def test_new_post_event_records_post_metadata(db_path, recorder):
    recorder.record_new_posts(WATCHED_CHAT, [ALBUM_POSTS[0]], HOUR_1)
    assert read_events(
        db_path,
        "kind",
        "chat_id",
        "message_id",
        "posted_at",
        "fetched_at",
        "edited_at",
        "media_kind",
        "album_id",
    ) == [
        (
            "new",
            -1000000000001,
            104,
            "2026-01-05T09:15:00+00:00",
            "2026-01-05T10:00:00+00:00",
            None,
            "photo",
            9001,
        )
    ]


def test_fetching_same_posts_again_adds_no_events(db_path, recorder):
    recorder.record_new_posts(WATCHED_CHAT, [TEXT_POST, PHOTO_POST], HOUR_1)
    recorder.record_new_posts(WATCHED_CHAT, [TEXT_POST, PHOTO_POST], HOUR_2)
    assert read_events(db_path, "kind", "message_id", "fetched_at") == [
        ("new", 101, "2026-01-05T10:00:00+00:00"),
        ("new", 103, "2026-01-05T10:00:00+00:00"),
    ]


def test_same_message_id_in_two_chats_is_two_posts(db_path, recorder):
    recorder.record_new_posts(WATCHED_CHAT, [TEXT_POST], HOUR_1)
    recorder.record_new_posts(UNRELATED_CHAT, [UNRELATED_POST], HOUR_1)
    assert read_events(db_path, "kind", "chat_id", "message_id") == [
        ("new", -1000000000002, 101),
        ("new", -1000000000001, 101),
    ]


def test_checkpoint_is_highest_message_id_saved_per_chat(recorder, checkpoints):
    recorder.record_new_posts(WATCHED_CHAT, list(ALBUM_POSTS), HOUR_1)
    recorder.record_new_posts(WATCHED_CHAT, [TEXT_POST], HOUR_2)
    recorder.record_new_posts(WATCHED_CHAT, [], HOUR_3)
    assert checkpoints.last_message_id(WATCHED_CHAT) == 106
    assert checkpoints.last_message_id(UNRELATED_CHAT) is None


def test_page_with_post_from_other_chat_stores_nothing(db_path, recorder, checkpoints):
    with pytest.raises(PostFromOtherChatError):
        recorder.record_new_posts(WATCHED_CHAT, [TEXT_POST, UNRELATED_POST], HOUR_1)
    assert read_events(db_path, "kind") == []
    assert checkpoints.last_message_id(WATCHED_CHAT) is None


def test_recheck_with_post_from_other_chat_stores_nothing(db_path, recorder):
    recorder.record_new_posts(WATCHED_CHAT, [TEXT_POST], HOUR_1)
    with pytest.raises(PostFromOtherChatError):
        recorder.record_recheck(WATCHED_CHAT, [101], [UNRELATED_POST], HOUR_2)
    assert read_events(db_path, "kind", "message_id") == [("new", 101)]


def test_page_that_fails_partway_leaves_earlier_pages_intact(db_path):
    with closing(open_database(db_path)) as connection:
        recorder = make_recorder(connection)
        recorder.record_new_posts(WATCHED_CHAT, [TEXT_POST], HOUR_1)
        with pytest.raises(OverflowError):
            recorder.record_new_posts(
                WATCHED_CHAT, [CAPTION_POST, UNSTORABLE_POST], HOUR_2
            )
    with closing(open_database(db_path)) as connection:
        assert CheckpointRepository(connection).last_message_id(WATCHED_CHAT) == 101
    assert read_events(db_path, "kind", "message_id") == [("new", 101)]


def test_text_and_captions_are_never_stored(db_path, recorder):
    recorder.record_new_posts(
        WATCHED_CHAT, [TEXT_POST, CAPTION_POST, *ALBUM_POSTS], HOUR_1
    )
    recorder.record_recheck(WATCHED_CHAT, [101], [EDITED_TEXT_POST], HOUR_2)
    stored = b"".join(path.read_bytes() for path in db_path.parent.glob("watcher.db*"))
    for text in [
        TEXT_POST.text,
        CAPTION_POST.text,
        ALBUM_POSTS[0].text,
        EDITED_TEXT_POST.text,
    ]:
        assert text.encode() not in stored


def test_each_new_edit_time_adds_one_version(db_path, recorder):
    recorder.record_new_posts(WATCHED_CHAT, [TEXT_POST], HOUR_1)
    recorder.record_recheck(WATCHED_CHAT, [101], [EDITED_TEXT_POST], HOUR_2)
    recorder.record_recheck(WATCHED_CHAT, [101], [EDITED_TEXT_POST], HOUR_3)
    assert read_events(db_path, "kind", "message_id", "fetched_at", "edited_at") == [
        ("new", 101, "2026-01-05T10:00:00+00:00", None),
        ("edited", 101, "2026-01-05T11:00:00+00:00", "2026-01-05T09:30:00+00:00"),
    ]


def test_recheck_marks_only_requested_posts_that_did_not_come_back(db_path, recorder):
    recorder.record_new_posts(
        WATCHED_CHAT, [TEXT_POST, CAPTION_POST, PHOTO_POST], HOUR_1
    )
    recorder.record_recheck(WATCHED_CHAT, [101, 103], [TEXT_POST], HOUR_2)
    assert read_events(db_path, "kind", "message_id", "fetched_at") == [
        ("new", 101, "2026-01-05T10:00:00+00:00"),
        ("new", 102, "2026-01-05T10:00:00+00:00"),
        ("new", 103, "2026-01-05T10:00:00+00:00"),
        ("deleted", 103, "2026-01-05T11:00:00+00:00"),
    ]


def test_deletion_keeps_the_time_it_was_first_noticed(db_path, recorder):
    recorder.record_new_posts(WATCHED_CHAT, [PHOTO_POST], HOUR_1)
    recorder.record_recheck(WATCHED_CHAT, [103], [], HOUR_2)
    recorder.record_recheck(WATCHED_CHAT, [103], [], HOUR_3)
    assert read_events(db_path, "kind", "fetched_at") == [
        ("new", "2026-01-05T10:00:00+00:00"),
        ("deleted", "2026-01-05T11:00:00+00:00"),
    ]

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest
from samples import (
    ALBUM_POSTS,
    PHOTO_POST,
    TEXT_POST,
    UNRELATED_CHAT,
    UNRELATED_POST,
    WATCHED_CHAT,
)

from group_watcher.posts import PostFromOtherChatError, reject_posts_from_other_chats

NAIVE = datetime(2026, 1, 5, 9, 0)
UTC_PLUS_3 = datetime(2026, 1, 5, 12, 0, tzinfo=timezone(timedelta(hours=3)))


@pytest.mark.parametrize(
    "chat_id", [-1000000000001, -42], ids=["channel", "basic-group"]
)
def test_accepts_marked_group_and_channel_ids(chat_id):
    replace(TEXT_POST, chat_id=chat_id)


@pytest.mark.parametrize("chat_id", [1234567890, 0], ids=["unmarked-or-user", "zero"])
def test_rejects_chat_ids_that_are_not_marked_groups_or_channels(chat_id):
    with pytest.raises(ValueError):
        replace(TEXT_POST, chat_id=chat_id)


@pytest.mark.parametrize("field", ["posted_at", "edited_at"])
@pytest.mark.parametrize("value", [NAIVE, UTC_PLUS_3], ids=["naive", "utc+3"])
def test_rejects_timestamps_not_in_utc(field, value):
    with pytest.raises(ValueError):
        replace(TEXT_POST, **{field: value})


def test_accepts_page_from_requested_chat():
    reject_posts_from_other_chats(WATCHED_CHAT, [TEXT_POST, PHOTO_POST, *ALBUM_POSTS])


def test_rejects_page_containing_post_from_other_chat():
    with pytest.raises(PostFromOtherChatError):
        reject_posts_from_other_chats(WATCHED_CHAT, [TEXT_POST, UNRELATED_POST])


def test_rejection_does_not_reveal_the_other_chat():
    with pytest.raises(PostFromOtherChatError) as error:
        reject_posts_from_other_chats(WATCHED_CHAT, [UNRELATED_POST])
    assert str(UNRELATED_CHAT) not in str(error.value)
    assert UNRELATED_POST.text not in str(error.value)

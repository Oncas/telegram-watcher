import pytest
from samples import (
    ALBUM_POSTS,
    PHOTO_POST,
    TEXT_POST,
    UNRELATED_CHAT,
    UNRELATED_POST,
    WATCHED_CHAT,
)

from group_watcher.allowlist import (
    PostFromOtherChatError,
    reject_posts_from_other_chats,
)


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

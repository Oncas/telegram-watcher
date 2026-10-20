from dataclasses import replace
from datetime import UTC, datetime, timedelta

from telegram_watcher.config import WatchedChat
from telegram_watcher.posts import MediaKind, Post

WATCHED_CHAT = -1000000000001
UNRELATED_CHAT = -1000000000002
SECOND_CHAT = -1000000000003

WATCHED_CHANNEL = WatchedChat(
    name="Synthetic channel", chat_id=WATCHED_CHAT, access_hash=1111
)
PHOTO_CHANNEL = WatchedChat(
    name="Synthetic channel",
    chat_id=WATCHED_CHAT,
    access_hash=1111,
    download_photos=True,
)
SECOND_CHANNEL = WatchedChat(
    name="Second synthetic channel", chat_id=SECOND_CHAT, access_hash=2222
)

T0 = datetime(2026, 1, 5, 9, 0, tzinfo=UTC)

TEXT_POST = Post(
    chat_id=WATCHED_CHAT,
    message_id=101,
    posted_at=T0,
    text="Synthetic text post",
)

CAPTION_POST = Post(
    chat_id=WATCHED_CHAT,
    message_id=102,
    posted_at=T0 + timedelta(minutes=5),
    media_kind=MediaKind.PHOTO,
    text="Synthetic caption",
)

PHOTO_POST = Post(
    chat_id=WATCHED_CHAT,
    message_id=103,
    posted_at=T0 + timedelta(minutes=10),
    media_kind=MediaKind.PHOTO,
)

ALBUM_POSTS = tuple(
    Post(
        chat_id=WATCHED_CHAT,
        message_id=message_id,
        posted_at=T0 + timedelta(minutes=15),
        media_kind=MediaKind.PHOTO,
        album_id=9001,
        text=text,
    )
    for message_id, text in [(104, "Synthetic album caption"), (105, None), (106, None)]
)

IMAGE_FILE_POST = Post(
    chat_id=WATCHED_CHAT,
    message_id=107,
    posted_at=T0 + timedelta(minutes=20),
    media_kind=MediaKind.DOCUMENT,
)

VIDEO_POST = Post(
    chat_id=WATCHED_CHAT,
    message_id=108,
    posted_at=T0 + timedelta(minutes=25),
    media_kind=MediaKind.VIDEO,
)

EDITED_TEXT_POST = replace(
    TEXT_POST,
    edited_at=T0 + timedelta(minutes=30),
    text="Synthetic text post, edited",
)

OUTGOING_PHOTO_POST = replace(PHOTO_POST, message_id=109, outgoing=True)

UNRELATED_POST = replace(
    TEXT_POST,
    chat_id=UNRELATED_CHAT,
    text="Synthetic unrelated post",
)

SECOND_CHAT_POST = replace(
    TEXT_POST,
    chat_id=SECOND_CHAT,
    text="Synthetic post in the second channel",
)

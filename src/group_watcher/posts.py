from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from group_watcher.chat_ids import is_marked_chat_id


class MediaKind(StrEnum):
    PHOTO = "photo"
    VIDEO = "video"
    AUDIO = "audio"
    DOCUMENT = "document"
    OTHER = "other"


@dataclass(frozen=True, slots=True, kw_only=True)
class Post:
    chat_id: int
    message_id: int
    posted_at: datetime
    edited_at: datetime | None = None
    media_kind: MediaKind | None = None
    album_id: int | None = None
    text: str | None = None
    outgoing: bool = False

    def __post_init__(self):
        if not is_marked_chat_id(self.chat_id):
            raise ValueError(f"chat_id {self.chat_id} is not a marked chat ID")
        if not _is_utc(self.posted_at):
            raise ValueError("posted_at must be a UTC datetime")
        if self.edited_at is not None and not _is_utc(self.edited_at):
            raise ValueError("edited_at must be a UTC datetime")


def _is_utc(value: datetime) -> bool:
    return value.utcoffset() == timedelta(0)

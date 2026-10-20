from collections.abc import Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum
from typing import Protocol

from telegram_watcher.config import WatchedChat
from telegram_watcher.posts import Post


class HaltReason(StrEnum):
    SESSION_ENDED = "session_ended"
    ACCOUNT_DEACTIVATED = "account_deactivated"
    AUTHORIZATION_REFUSED = "authorization_refused"


class NetworkUnavailable(Exception):
    pass


class RateLimited(Exception):
    def __init__(self, wait: timedelta):
        super().__init__(f"rate limited for {wait}")
        self.wait = wait


class ChatUnavailable(Exception):
    pass


class AuthorizationFailed(Exception):
    def __init__(self, reason: HaltReason):
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True, slots=True, kw_only=True)
class Page:
    posts: Sequence[Post]
    last_message_id: int | None
    reached_end: bool


class ChatClient(Protocol):
    def latest_message_id(self, chat: WatchedChat) -> int | None: ...

    def fetch_after(self, chat: WatchedChat, message_id: int, limit: int) -> Page: ...

    def fetch_by_ids(
        self, chat: WatchedChat, message_ids: Sequence[int]
    ) -> Sequence[Post]: ...


class LiveTelegram(Protocol):
    def connect(self) -> AbstractContextManager[ChatClient]: ...

    def wait_for_update(self, timeout: timedelta) -> None: ...

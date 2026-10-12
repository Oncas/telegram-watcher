from collections.abc import Sequence
from contextlib import contextmanager
from datetime import datetime

from group_watcher.client import Page
from group_watcher.config import WatchedChat
from group_watcher.notifier import NotificationFailed
from group_watcher.posts import Post


class FakeTelegram:
    def __init__(self, *posts: Post):
        self._posts: dict[tuple[int, int], Post] = {}
        self._service_messages: set[tuple[int, int]] = set()
        self.publish(*posts)
        self.connect_failure: Exception | None = None
        self.chat_failures: dict[int, Exception] = {}
        self.recheck_failures: dict[int, Exception] = {}
        self.connections = 0
        self.requested_chat_ids: list[int] = []
        self.rechecked_message_ids: list[list[int]] = []
        self.fetched_after_ids: list[int] = []

    def publish(self, *posts: Post) -> None:
        for post in posts:
            self._posts[post.chat_id, post.message_id] = post

    def publish_service_messages(self, chat_id: int, *message_ids: int) -> None:
        self._service_messages.update(
            (chat_id, message_id) for message_id in message_ids
        )

    def delete(self, chat_id: int, *message_ids: int) -> None:
        for message_id in message_ids:
            del self._posts[chat_id, message_id]

    @contextmanager
    def connect(self):
        if self.connect_failure is not None:
            raise self.connect_failure
        self.connections += 1
        yield self

    def latest_message_id(self, chat: WatchedChat) -> int | None:
        return max(self._read_history(chat), default=None)

    def fetch_after(self, chat: WatchedChat, message_id: int, limit: int) -> Page:
        self.fetched_after_ids.append(message_id)
        history = self._read_history(chat)
        newer_ids = sorted(newer for newer in history if newer > message_id)[:limit]
        return Page(
            posts=[history[newer] for newer in newer_ids if history[newer]],
            last_message_id=newer_ids[-1] if newer_ids else None,
            reached_end=len(newer_ids) < limit,
        )

    def fetch_by_ids(
        self, chat: WatchedChat, message_ids: Sequence[int]
    ) -> Sequence[Post]:
        history = self._read_history(chat)
        self.rechecked_message_ids.append(list(message_ids))
        if chat.chat_id in self.recheck_failures:
            raise self.recheck_failures[chat.chat_id]
        return [history[found] for found in message_ids if history.get(found)]

    def _read_history(self, chat: WatchedChat) -> dict[int, Post | None]:
        self.requested_chat_ids.append(chat.chat_id)
        if chat.chat_id in self.chat_failures:
            raise self.chat_failures[chat.chat_id]
        history: dict[int, Post | None] = {
            message_id: None
            for chat_id, message_id in self._service_messages
            if chat_id == chat.chat_id
        }
        history.update(
            (post.message_id, post)
            for post in self._posts.values()
            if post.chat_id == chat.chat_id
        )
        return history


class FakeNotifier:
    def __init__(self):
        self.messages: list[str] = []
        self.available = True

    def notify(self, message: str) -> None:
        if not self.available:
            raise NotificationFailed()
        self.messages.append(message)


class FakeClock:
    def __init__(self, now: datetime):
        self.now = now

    def __call__(self) -> datetime:
        return self.now


class FakeNotifySend:
    def __init__(self, directory):
        self._script = directory / "notify-send"
        self._log = directory / "notify-send.log"
        self.answer_with(0)

    def answer_with(self, exit_code):
        self._script.write_text(
            f"#!/bin/sh\nIFS='\t'\nprintf '%s\\n' \"$*\" >> '{self._log}'\nexit {exit_code}\n"
        )
        self._script.chmod(0o700)

    @property
    def calls(self):
        if not self._log.exists():
            return []
        return [line.split("\t") for line in self._log.read_text().splitlines()]

    @property
    def messages(self):
        return [arguments[-1] for arguments in self.calls]

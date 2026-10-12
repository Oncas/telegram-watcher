from collections.abc import Sequence
from contextlib import contextmanager
from datetime import datetime

from group_watcher.config import WatchedChat
from group_watcher.posts import Post


class FakeTelegram:
    def __init__(self, *posts: Post):
        self._posts: dict[tuple[int, int], Post] = {}
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
        return max((post.message_id for post in self._request(chat)), default=None)

    def fetch_after(
        self, chat: WatchedChat, message_id: int, limit: int
    ) -> Sequence[Post]:
        self.fetched_after_ids.append(message_id)
        newer = [post for post in self._request(chat) if post.message_id > message_id]
        return newer[:limit]

    def fetch_by_ids(
        self, chat: WatchedChat, message_ids: Sequence[int]
    ) -> Sequence[Post]:
        posts = self._request(chat)
        self.rechecked_message_ids.append(list(message_ids))
        if chat.chat_id in self.recheck_failures:
            raise self.recheck_failures[chat.chat_id]
        return [post for post in posts if post.message_id in message_ids]

    def _request(self, chat: WatchedChat) -> list[Post]:
        self.requested_chat_ids.append(chat.chat_id)
        if chat.chat_id in self.chat_failures:
            raise self.chat_failures[chat.chat_id]
        return sorted(
            (post for post in self._posts.values() if post.chat_id == chat.chat_id),
            key=lambda post: post.message_id,
        )


class FakeNotifier:
    def __init__(self):
        self.messages: list[str] = []

    def notify(self, message: str) -> None:
        self.messages.append(message)


class FakeClock:
    def __init__(self, now: datetime):
        self.now = now

    def __call__(self) -> datetime:
        return self.now

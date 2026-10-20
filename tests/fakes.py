import json
import threading
from collections.abc import Callable, Sequence
from contextlib import contextmanager
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from telegram_watcher.client import Image, Page
from telegram_watcher.config import WatchedChat
from telegram_watcher.forwarder import ForwardFailed
from telegram_watcher.notifier import NotificationFailed
from telegram_watcher.posts import Post


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
        self.image_failures: dict[int, Exception] = {}
        self.imageless_message_ids: set[int] = set()
        self.requested_image_ids: list[int] = []

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

    def fetch_image(self, chat: WatchedChat, message_id: int) -> Image | None:
        self.requested_image_ids.append(message_id)
        if message_id in self.image_failures:
            raise self.image_failures[message_id]
        if message_id in self.imageless_message_ids:
            return None
        return Image(data=f"image {message_id}".encode(), extension=".jpg")

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


class FakeLiveTelegram:
    def __init__(self, telegram: FakeTelegram, *updates: Callable[[], None]):
        self.connect = telegram.connect
        self._updates = list(updates)
        self.waits: list[timedelta] = []

    def wait_for_update(self, timeout: timedelta) -> None:
        self.waits.append(timeout)
        self._updates.pop(0)()


class FakeNotifier:
    def __init__(self):
        self.messages: list[str] = []
        self.available = True

    def notify(self, message: str) -> None:
        if not self.available:
            raise NotificationFailed()
        self.messages.append(message)


class FakeForwarder:
    def __init__(self):
        self.payloads: list[dict] = []
        self.failure: str | None = None
        self.attempts = 0

    def forward(self, payload: dict) -> None:
        self.attempts += 1
        if self.failure is not None:
            raise ForwardFailed(self.failure)
        self.payloads.append(payload)


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
            f"#!/bin/sh\nIFS='\t'\nprintf '%s\\036' \"$*\" >> '{self._log}'\nexit {exit_code}\n"
        )
        self._script.chmod(0o700)

    @property
    def calls(self):
        if not self._log.exists():
            return []
        records = self._log.read_text().split("\x1e")[:-1]
        return [record.split("\t") for record in records]

    @property
    def messages(self):
        return [arguments[-1] for arguments in self.calls]


class FakeEndpoint:
    def __init__(self):
        self.requests: list[tuple] = []
        self.status = 200
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _EndpointHandler)
        self._server.endpoint = self
        host, port = self._server.server_address
        self.url = f"http://{host}:{port}/matches"
        self._thread = threading.Thread(
            target=self._server.serve_forever, kwargs={"poll_interval": 0.01}
        )
        self._thread.start()

    @property
    def payloads(self):
        return [json.loads(body) for _, _, _, body in self.requests]

    def close(self):
        self._server.shutdown()
        self._server.server_close()
        self._thread.join()


class _EndpointHandler(BaseHTTPRequestHandler):
    def answer(self):
        endpoint = self.server.endpoint
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        endpoint.requests.append((self.command, self.path, self.headers, body))
        self.send_response(endpoint.status)
        self.send_header("Location", "/elsewhere")
        self.send_header("Content-Length", "0")
        self.end_headers()

    do_GET = do_POST = answer

    def log_message(self, *arguments):
        pass

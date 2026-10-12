from collections.abc import Callable
from datetime import datetime, timedelta

from group_watcher.client import ChatClient
from group_watcher.config import WatchedChat
from group_watcher.recorder import PostRecorder
from group_watcher.storage.checkpoint_repository import CheckpointRepository
from group_watcher.storage.post_repository import PostRepository


class ChatPoller:
    def __init__(
        self,
        *,
        recorder: PostRecorder,
        post_repository: PostRepository,
        checkpoint_repository: CheckpointRepository,
        clock: Callable[[], datetime],
        page_size: int = 100,
        max_pages: int = 20,
        recheck_window: timedelta = timedelta(days=3),
    ):
        self._recorder = recorder
        self._posts = post_repository
        self._checkpoints = checkpoint_repository
        self._clock = clock
        self._page_size = page_size
        self._max_pages = max_pages
        self._recheck_window = recheck_window

    def poll(self, client: ChatClient, chat: WatchedChat) -> None:
        last_message_id = self._checkpoints.last_message_id(chat.chat_id)
        if last_message_id is None:
            self._recorder.record_starting_point(
                chat.chat_id, client.latest_message_id(chat) or 0
            )
            return
        self._recheck_recent_posts(client, chat)
        self._fetch_new_posts(client, chat, last_message_id)

    def _recheck_recent_posts(self, client: ChatClient, chat: WatchedChat) -> None:
        message_ids = self._posts.recent_message_ids(
            chat.chat_id, posted_since=self._clock() - self._recheck_window
        )
        if not message_ids:
            return
        posts = client.fetch_by_ids(chat, message_ids)
        self._recorder.record_recheck(chat.chat_id, message_ids, posts, self._clock())

    def _fetch_new_posts(
        self, client: ChatClient, chat: WatchedChat, after_message_id: int
    ) -> None:
        for _ in range(self._max_pages):
            page = client.fetch_after(chat, after_message_id, self._page_size)
            if page:
                self._recorder.record_new_posts(chat.chat_id, page, self._clock())
                after_message_id = max(post.message_id for post in page)
            if len(page) < self._page_size:
                return

from collections.abc import Callable, Sequence
from datetime import datetime, timedelta

from telegram_watcher.allowlist import reject_posts_from_other_chats
from telegram_watcher.client import ChatClient
from telegram_watcher.config import WatchedChat
from telegram_watcher.image_store import ImageStore
from telegram_watcher.posts import MediaKind, Post
from telegram_watcher.recorder import PostRecorder
from telegram_watcher.storage.checkpoint_repository import CheckpointRepository
from telegram_watcher.storage.post_repository import PostRepository


class ChatPoller:
    def __init__(
        self,
        *,
        recorder: PostRecorder,
        image_store: ImageStore,
        post_repository: PostRepository,
        checkpoint_repository: CheckpointRepository,
        clock: Callable[[], datetime],
        page_size: int = 100,
        max_pages: int = 20,
        recheck_window: timedelta = timedelta(days=3),
    ):
        self._recorder = recorder
        self._image_store = image_store
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
            reject_posts_from_other_chats(chat.chat_id, page.posts)
            if chat.download_photos:
                self._save_images(client, chat, page.posts)
            if page.last_message_id is not None:
                self._recorder.record_new_posts(
                    chat.chat_id,
                    page.posts,
                    self._clock(),
                    read_through=page.last_message_id,
                )
                after_message_id = page.last_message_id
            if page.reached_end:
                return

    def _save_images(
        self, client: ChatClient, chat: WatchedChat, posts: Sequence[Post]
    ) -> None:
        for post in posts:
            if not _may_hold_image(post) or self._image_store.contains(
                chat.chat_id, post.message_id
            ):
                continue
            image = client.fetch_image(chat, post.message_id)
            if image is not None:
                self._image_store.add(chat.chat_id, post.message_id, image)


def _may_hold_image(post: Post) -> bool:
    return not post.outgoing and post.media_kind in (
        MediaKind.PHOTO,
        MediaKind.DOCUMENT,
    )

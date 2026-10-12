from collections.abc import Iterable, Sequence
from contextlib import AbstractContextManager
from datetime import datetime

from group_watcher.allowlist import reject_posts_from_other_chats
from group_watcher.posts import Post
from group_watcher.storage.checkpoint_repository import CheckpointRepository
from group_watcher.storage.post_repository import PostRepository


class PostRecorder:
    def __init__(
        self,
        *,
        transaction: AbstractContextManager,
        post_repository: PostRepository,
        checkpoint_repository: CheckpointRepository,
    ):
        self._transaction = transaction
        self._posts = post_repository
        self._checkpoints = checkpoint_repository

    def record_new_posts(
        self, chat_id: int, posts: Sequence[Post], fetched_at: datetime
    ) -> None:
        reject_posts_from_other_chats(chat_id, posts)
        with self._transaction:
            for post in posts:
                self._posts.save(post, fetched_at)
            if posts:
                self._checkpoints.advance(
                    chat_id, max(post.message_id for post in posts)
                )

    def record_recheck(
        self,
        chat_id: int,
        requested_ids: Iterable[int],
        posts: Sequence[Post],
        fetched_at: datetime,
    ) -> None:
        reject_posts_from_other_chats(chat_id, posts)
        missing_ids = set(requested_ids) - {post.message_id for post in posts}
        with self._transaction:
            for post in posts:
                self._posts.save(post, fetched_at)
            self._posts.mark_deleted(chat_id, missing_ids, fetched_at)

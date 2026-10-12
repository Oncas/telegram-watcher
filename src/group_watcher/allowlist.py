from collections.abc import Iterable

from group_watcher.posts import Post


class PostFromOtherChatError(Exception):
    pass


def reject_posts_from_other_chats(chat_id: int, posts: Iterable[Post]) -> None:
    if any(post.chat_id != chat_id for post in posts):
        raise PostFromOtherChatError(
            f"the client returned a post that is not from chat {chat_id}"
        )

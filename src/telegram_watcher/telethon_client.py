import asyncio
import logging
from collections.abc import Callable, Coroutine, Iterator, Sequence
from contextlib import contextmanager
from datetime import timedelta
from typing import Any

from telethon import TelegramClient, errors, types, utils

from telegram_watcher.client import (
    AuthorizationFailed,
    ChatUnavailable,
    HaltReason,
    NetworkUnavailable,
    Page,
    RateLimited,
)
from telegram_watcher.config import WatchedChat
from telegram_watcher.posts import MediaKind, Post

logger = logging.getLogger(__name__)

SESSION_ENDED_ERRORS = (
    errors.AuthKeyUnregisteredError,
    errors.SessionRevokedError,
    errors.SessionExpiredError,
    errors.AuthKeyDuplicatedError,
)
ACCOUNT_DEACTIVATED_ERRORS = (
    errors.UserDeactivatedError,
    errors.UserDeactivatedBanError,
)
CHAT_UNAVAILABLE_ERRORS = (
    errors.ChannelPrivateError,
    errors.ChannelInvalidError,
    errors.ChatIdInvalidError,
    errors.PeerIdInvalidError,
    errors.ForbiddenError,
)
NETWORK_ERRORS = (OSError, errors.ServerError, errors.TimedOutError)

MEDIA_KINDS_BY_MIME_TYPE = {"video": MediaKind.VIDEO, "audio": MediaKind.AUDIO}


class TelethonChatClient:
    def __init__(
        self,
        client: TelegramClient,
        run: Callable[[Coroutine[Any, Any, Any]], Any],
    ):
        self._client = client
        self._run = run

    def latest_message_id(self, chat: WatchedChat) -> int | None:
        messages = self._request(self._client.get_messages(input_peer(chat), limit=1))
        return messages[0].id if messages else None

    def fetch_after(self, chat: WatchedChat, message_id: int, limit: int) -> Page:
        messages = self._request(
            self._client.get_messages(
                input_peer(chat), limit=limit, min_id=message_id, reverse=True
            )
        )
        return Page(
            posts=_posts(messages),
            last_message_id=max((message.id for message in messages), default=None),
            reached_end=len(messages) < limit,
        )

    def fetch_by_ids(
        self, chat: WatchedChat, message_ids: Sequence[int]
    ) -> Sequence[Post]:
        messages = self._request(
            self._client.get_messages(input_peer(chat), ids=list(message_ids))
        )
        return _posts(messages)

    def _request(self, request: Coroutine[Any, Any, Any]) -> Any:
        with translated_errors():
            return self._run(request)


@contextmanager
def connect_telethon(
    create_client: Callable[[], TelegramClient],
) -> Iterator[TelethonChatClient]:
    with asyncio.Runner() as runner:
        client = runner.run(_create_in_loop(create_client))
        try:
            with translated_errors():
                runner.run(client.connect())
            yield TelethonChatClient(client, runner.run)
        finally:
            runner.run(_disconnect_in_loop(client))


@contextmanager
def translated_errors() -> Iterator[None]:
    try:
        yield
    except Exception as error:
        translated = _translate(error)
        if translated is None:
            raise
        code = error.message if isinstance(error, errors.RPCError) else ""
        logger.warning("telegram error: %s %s", type(error).__name__, code)
        raise translated from error


def input_peer(chat: WatchedChat) -> types.TypeInputPeer:
    peer_id, peer_type = utils.resolve_id(chat.chat_id)
    if peer_type is types.PeerChannel:
        return types.InputPeerChannel(channel_id=peer_id, access_hash=chat.access_hash)
    if peer_type is types.PeerUser:
        return types.InputPeerUser(user_id=peer_id, access_hash=chat.access_hash)
    return types.InputPeerChat(chat_id=peer_id)


def to_post(message: types.Message) -> Post:
    return Post(
        chat_id=utils.get_peer_id(message.peer_id),
        message_id=message.id,
        posted_at=message.date,
        edited_at=message.edit_date,
        media_kind=media_kind(message.media),
        album_id=message.grouped_id,
        text=message.message or None,
        outgoing=bool(message.out),
    )


def media_kind(media: types.TypeMessageMedia | None) -> MediaKind | None:
    match media:
        case None | types.MessageMediaEmpty() | types.MessageMediaWebPage():
            return None
        case types.MessageMediaPhoto():
            return MediaKind.PHOTO
        case types.MessageMediaDocument(document=types.Document(mime_type=mime_type)):
            major_type = mime_type.partition("/")[0]
            return MEDIA_KINDS_BY_MIME_TYPE.get(major_type, MediaKind.DOCUMENT)
        case types.MessageMediaDocument():
            return MediaKind.DOCUMENT
        case _:
            return MediaKind.OTHER


def _posts(messages: Sequence[Any]) -> list[Post]:
    return [
        to_post(message) for message in messages if isinstance(message, types.Message)
    ]


def _translate(error: Exception) -> Exception | None:
    if isinstance(error, errors.FloodWaitError):
        return RateLimited(timedelta(seconds=error.seconds))
    if isinstance(error, SESSION_ENDED_ERRORS):
        return AuthorizationFailed(HaltReason.SESSION_ENDED)
    if isinstance(error, ACCOUNT_DEACTIVATED_ERRORS):
        return AuthorizationFailed(HaltReason.ACCOUNT_DEACTIVATED)
    if isinstance(error, errors.UnauthorizedError):
        return AuthorizationFailed(HaltReason.AUTHORIZATION_REFUSED)
    if isinstance(error, CHAT_UNAVAILABLE_ERRORS):
        return ChatUnavailable()
    if isinstance(error, NETWORK_ERRORS):
        return NetworkUnavailable()
    return None


async def _create_in_loop(
    create_client: Callable[[], TelegramClient],
) -> TelegramClient:
    return create_client()


async def _disconnect_in_loop(client: TelegramClient) -> None:
    await client.disconnect()

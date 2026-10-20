import asyncio
import logging
import threading
from collections.abc import Callable, Coroutine, Iterator, Sequence
from contextlib import contextmanager
from datetime import timedelta
from typing import Any

from telethon import TelegramClient, errors, events, types, utils

from telegram_watcher.client import (
    AuthorizationFailed,
    ChatUnavailable,
    HaltReason,
    Image,
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

REQUEST_TIMEOUT = timedelta(minutes=1)
MAX_IMAGE_FILE_SIZE = 20 * 1024 * 1024


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

    def fetch_image(self, chat: WatchedChat, message_id: int) -> Image | None:
        (message,) = self._request(
            self._client.get_messages(input_peer(chat), ids=[message_id])
        )
        if not isinstance(message, types.Message) or not _is_image(message.media):
            return None
        if _file_size(message.media) > MAX_IMAGE_FILE_SIZE:
            logger.warning(
                "image too large to download: %s message %s", chat.name, message_id
            )
            return None
        data = self._request(self._client.download_media(message, file=bytes))
        if data is None:
            return None
        return Image(data=data, extension=utils.get_extension(message.media))

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


class LiveTelethon:
    def __init__(
        self,
        create_client: Callable[[], TelegramClient],
        chats: Sequence[WatchedChat],
        request_timeout: timedelta,
    ):
        self._create_client = create_client
        self._chat_ids = frozenset(chat.chat_id for chat in chats)
        self._request_timeout = request_timeout
        self._client: TelegramClient | None = None
        self._update = threading.Event()
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()

    @contextmanager
    def connect(self) -> Iterator[TelethonChatClient]:
        if self._client is None:
            self._client = self._run(_create_in_loop(self._create_client))
            with translated_errors():
                self._run(self._listen(self._client))
        yield TelethonChatClient(self._client, self._run)

    def wait_for_update(self, timeout: timedelta) -> None:
        self._update.wait(timeout.total_seconds())
        self._update.clear()

    def close(self) -> None:
        if self._client is not None:
            self._run(_disconnect_in_loop(self._client))
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join()
        self._loop.close()

    def _run(self, coroutine: Coroutine[Any, Any, Any]) -> Any:
        future = asyncio.run_coroutine_threadsafe(coroutine, self._loop)
        try:
            return future.result(self._request_timeout.total_seconds())
        finally:
            future.cancel()

    async def _listen(self, client: TelegramClient) -> None:
        client.add_event_handler(self._on_message, events.NewMessage())
        client.add_event_handler(self._on_message, events.MessageEdited())
        await client.connect()
        client.disconnected.add_done_callback(self._on_disconnect)

    async def _on_message(self, event: events.NewMessage.Event) -> None:
        if event.chat_id in self._chat_ids:
            self._update.set()

    def _on_disconnect(self, disconnected: asyncio.Future) -> None:
        error = None if disconnected.cancelled() else disconnected.exception()
        if error is not None:
            logger.warning("telegram connection lost: %s", type(error).__name__)
        self._update.set()


@contextmanager
def listen_telethon(
    create_client: Callable[[], TelegramClient],
    chats: Sequence[WatchedChat],
    *,
    request_timeout: timedelta = REQUEST_TIMEOUT,
) -> Iterator[LiveTelethon]:
    live = LiveTelethon(create_client, chats, request_timeout)
    try:
        yield live
    finally:
        live.close()


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


def _is_image(media: types.TypeMessageMedia | None) -> bool:
    match media:
        case types.MessageMediaPhoto(photo=types.Photo()):
            return True
        case types.MessageMediaDocument(
            document=types.Document(mime_type=mime_type, attributes=attributes)
        ):
            return mime_type.startswith("image/") and not any(
                isinstance(attribute, types.DocumentAttributeSticker)
                for attribute in attributes
            )
        case _:
            return False


def _file_size(media: types.TypeMessageMedia) -> int:
    match media:
        case types.MessageMediaDocument(document=types.Document(size=size)):
            return size
        case _:
            return 0


def _posts(messages: Sequence[Any]) -> list[Post]:
    return [
        to_post(message) for message in messages if isinstance(message, types.Message)
    ]


def _translate(error: Exception) -> Exception | None:
    if isinstance(error, errors.FloodWaitError | errors.FloodPremiumWaitError):
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

import asyncio
import time
from datetime import timedelta
from types import SimpleNamespace

import pytest
from samples import T0, UNRELATED_CHAT, WATCHED_CHANNEL, WATCHED_CHAT
from telethon import errors, events, types

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
from telegram_watcher.telethon_client import connect_telethon, listen_telethon

WATCHED_PEER = types.PeerChannel(channel_id=1)
BASIC_GROUP = WatchedChat(name="Synthetic group", chat_id=-4001, access_hash=None)
PRIVATE_CHAT = WatchedChat(name="Synthetic person", chat_id=7, access_hash=7777)
SHORT_WAIT = timedelta(milliseconds=50)
LONG_WAIT = timedelta(seconds=10)


def message(message_id, peer=WATCHED_PEER, text="", **fields):
    return types.Message(id=message_id, peer_id=peer, date=T0, message=text, **fields)


def service_message(message_id):
    return types.MessageService(
        id=message_id,
        peer_id=WATCHED_PEER,
        date=T0,
        action=types.MessageActionPinMessage(),
    )


def document(mime_type):
    return types.MessageMediaDocument(
        document=types.Document(
            id=1,
            access_hash=1,
            file_reference=b"",
            date=T0,
            mime_type=mime_type,
            size=1,
            dc_id=1,
            attributes=[],
        )
    )


class FakeTelethon:
    def __init__(self, *messages):
        self.messages = {message.id: message for message in messages}
        self.connect_failure = None
        self.request_failure = None
        self.unanswered = False
        self.requests = []
        self.connections = 0
        self.closed = False
        self.event_handlers = []
        self._connection = None

    async def connect(self):
        if self.connect_failure is not None:
            raise self.connect_failure
        self.connections += 1
        self.loop = asyncio.get_running_loop()
        self._connection = self.loop.create_future()

    def disconnect(self):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            self.closed = True
            return None
        return self._disconnect_in_running_loop()

    async def _disconnect_in_running_loop(self):
        self.closed = True
        if self._connection is not None and not self._connection.done():
            self._connection.set_result(None)

    @property
    def disconnected(self):
        return self._connection

    def drop_connection(self):
        self.loop.call_soon_threadsafe(
            self._connection.set_exception, ConnectionError()
        )

    def add_event_handler(self, callback, event):
        self.event_handlers.append((callback, type(event)))

    def deliver(self, event_type, chat_id):
        asyncio.run_coroutine_threadsafe(
            self._dispatch(event_type, SimpleNamespace(chat_id=chat_id)), self.loop
        ).result()

    async def _dispatch(self, event_type, event):
        for callback, handled_type in self.event_handlers:
            if handled_type is event_type:
                await callback(event)

    async def get_messages(
        self, entity, limit=None, *, min_id=0, reverse=False, ids=None
    ):
        self.requests.append(
            {
                "entity": entity,
                "limit": limit,
                "min_id": min_id,
                "reverse": reverse,
                "ids": ids,
            }
        )
        if self.request_failure is not None:
            raise self.request_failure
        if self.unanswered:
            await asyncio.get_running_loop().create_future()
        if ids is not None:
            return [self.messages.get(message_id) for message_id in ids]
        if reverse:
            newer = sorted(
                message_id for message_id in self.messages if message_id > min_id
            )
            return [self.messages[message_id] for message_id in newer[:limit]]
        newest_first = sorted(self.messages, reverse=True)
        return [self.messages[message_id] for message_id in newest_first[:limit]]


@pytest.fixture
def telethon():
    return FakeTelethon()


@pytest.fixture
def client(telethon):
    with connect_telethon(lambda: telethon) as client:
        yield client


def test_new_posts_are_read_oldest_first_after_the_checkpoint(telethon, client):
    telethon.messages = {
        message_id: message(message_id) for message_id in [101, 103, 104]
    }
    page = client.fetch_after(WATCHED_CHANNEL, 101, 2)
    assert [post.message_id for post in page.posts] == [103, 104]
    assert telethon.requests == [
        {
            "entity": types.InputPeerChannel(channel_id=1, access_hash=1111),
            "limit": 2,
            "min_id": 101,
            "reverse": True,
            "ids": None,
        }
    ]


def test_service_messages_are_read_past_but_not_returned(telethon, client):
    telethon.messages = {102: service_message(102), 103: message(103)}
    assert client.fetch_after(WATCHED_CHANNEL, 101, 2) == Page(
        posts=[Post(chat_id=WATCHED_CHAT, message_id=103, posted_at=T0)],
        last_message_id=103,
        reached_end=False,
    )


def test_page_shorter_than_requested_reaches_the_end(telethon, client):
    telethon.messages = {102: service_message(102)}
    assert client.fetch_after(WATCHED_CHANNEL, 101, 2) == Page(
        posts=[], last_message_id=102, reached_end=True
    )
    assert client.fetch_after(WATCHED_CHANNEL, 102, 2) == Page(
        posts=[], last_message_id=None, reached_end=True
    )


def test_post_keeps_telegram_metadata(telethon, client):
    edited_at = T0 + timedelta(minutes=5)
    telethon.messages = {
        102: message(
            102,
            text="Synthetic caption",
            media=types.MessageMediaPhoto(photo=types.PhotoEmpty(id=1)),
            edit_date=edited_at,
            grouped_id=7001,
        )
    }
    assert client.fetch_by_ids(WATCHED_CHANNEL, [102]) == [
        Post(
            chat_id=WATCHED_CHAT,
            message_id=102,
            posted_at=T0,
            edited_at=edited_at,
            media_kind=MediaKind.PHOTO,
            album_id=7001,
            text="Synthetic caption",
        )
    ]


@pytest.mark.parametrize(
    ("media", "media_kind"),
    [
        (None, None),
        (types.MessageMediaPhoto(photo=types.PhotoEmpty(id=1)), MediaKind.PHOTO),
        (document("video/mp4"), MediaKind.VIDEO),
        (document("audio/ogg"), MediaKind.AUDIO),
        (document("application/pdf"), MediaKind.DOCUMENT),
        (types.MessageMediaWebPage(webpage=types.WebPageEmpty(id=1)), None),
        (types.MessageMediaGeo(geo=types.GeoPointEmpty()), MediaKind.OTHER),
    ],
)
def test_media_is_reduced_to_its_kind(telethon, client, media, media_kind):
    telethon.messages = {101: message(101, media=media)}
    (post,) = client.fetch_by_ids(WATCHED_CHANNEL, [101])
    assert post.media_kind == media_kind


def test_recheck_returns_only_messages_that_still_exist(telethon, client):
    telethon.messages = {101: message(101)}
    posts = client.fetch_by_ids(WATCHED_CHANNEL, [101, 105])
    assert [post.message_id for post in posts] == [101]
    assert telethon.requests[0]["ids"] == [101, 105]


def test_latest_message_id_is_the_newest_message(telethon, client):
    assert client.latest_message_id(WATCHED_CHANNEL) is None
    telethon.messages = {101: message(101), 102: service_message(102)}
    assert client.latest_message_id(WATCHED_CHANNEL) == 102


def test_basic_group_is_addressed_without_access_hash(telethon, client):
    telethon.messages = {101: message(101, peer=types.PeerChat(chat_id=4001))}
    (post,) = client.fetch_by_ids(BASIC_GROUP, [101])
    assert post.chat_id == -4001
    assert telethon.requests[0]["entity"] == types.InputPeerChat(chat_id=4001)


def test_private_chat_is_addressed_with_its_access_hash(telethon, client):
    telethon.messages = {101: message(101, peer=types.PeerUser(user_id=7))}
    (post,) = client.fetch_by_ids(PRIVATE_CHAT, [101])
    assert post.chat_id == 7
    assert telethon.requests[0]["entity"] == types.InputPeerUser(
        user_id=7, access_hash=7777
    )


@pytest.mark.parametrize("out", [True, False])
def test_post_says_whether_the_account_sent_it(telethon, client, out):
    telethon.messages = {101: message(101, out=out)}
    (post,) = client.fetch_by_ids(WATCHED_CHANNEL, [101])
    assert post.outgoing is out


def test_rate_limit_keeps_the_wait_telegram_asked_for(telethon, client):
    telethon.request_failure = errors.FloodWaitError(request=None, capture=3600)
    with pytest.raises(RateLimited) as raised:
        client.latest_message_id(WATCHED_CHANNEL)
    assert raised.value.wait == timedelta(hours=1)


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (errors.AuthKeyUnregisteredError(None), HaltReason.SESSION_ENDED),
        (errors.SessionRevokedError(None), HaltReason.SESSION_ENDED),
        (errors.SessionExpiredError(None), HaltReason.SESSION_ENDED),
        (errors.AuthKeyDuplicatedError(None), HaltReason.SESSION_ENDED),
        (errors.UserDeactivatedError(None), HaltReason.ACCOUNT_DEACTIVATED),
        (errors.UserDeactivatedBanError(None), HaltReason.ACCOUNT_DEACTIVATED),
        (
            errors.UnauthorizedError(None, "ACCOUNT_RESTRICTED"),
            HaltReason.AUTHORIZATION_REFUSED,
        ),
    ],
)
def test_authorization_errors_name_the_action_they_need(
    telethon, client, error, reason
):
    telethon.request_failure = error
    with pytest.raises(AuthorizationFailed) as raised:
        client.latest_message_id(WATCHED_CHANNEL)
    assert raised.value.reason == reason


@pytest.mark.parametrize(
    "error",
    [
        errors.ChannelPrivateError(None),
        errors.ChannelInvalidError(None),
        errors.ChatIdInvalidError(None),
        errors.PeerIdInvalidError(None),
        errors.ChatForbiddenError(None),
    ],
)
def test_chat_errors_make_only_that_chat_unavailable(telethon, client, error):
    telethon.request_failure = error
    with pytest.raises(ChatUnavailable):
        client.latest_message_id(WATCHED_CHANNEL)


@pytest.mark.parametrize(
    "error",
    [ConnectionError(), TimeoutError(), errors.ServerError(None, "INTERNAL")],
)
def test_network_and_server_errors_end_the_run_quietly(telethon, client, error):
    telethon.request_failure = error
    with pytest.raises(NetworkUnavailable):
        client.latest_message_id(WATCHED_CHANNEL)


def test_other_telegram_errors_pass_through_unchanged(telethon, client):
    telethon.request_failure = errors.MsgIdInvalidError(None)
    with pytest.raises(errors.MsgIdInvalidError):
        client.latest_message_id(WATCHED_CHANNEL)


def test_failure_to_connect_is_a_network_failure(telethon):
    telethon.connect_failure = ConnectionError()
    with pytest.raises(NetworkUnavailable), connect_telethon(lambda: telethon):
        pass


def test_client_disconnects_even_when_the_run_fails(telethon):
    with pytest.raises(RuntimeError), connect_telethon(lambda: telethon):
        raise RuntimeError
    assert telethon.closed


def test_client_is_created_inside_the_event_loop(telethon):
    def create_client():
        asyncio.get_running_loop()
        return telethon

    with connect_telethon(create_client) as client:
        assert client.latest_message_id(WATCHED_CHANNEL) is None


@pytest.fixture
def live(telethon):
    with listen_telethon(lambda: telethon, [WATCHED_CHANNEL]) as live:
        with live.connect():
            pass
        yield live


def waited(live, timeout):
    started = time.monotonic()
    live.wait_for_update(timeout)
    return time.monotonic() - started


def test_live_connection_is_opened_once_and_kept_between_runs(telethon, live):
    for _ in range(2):
        with live.connect() as client:
            assert client.latest_message_id(WATCHED_CHANNEL) is None
    assert telethon.connections == 1
    assert not telethon.closed


def test_live_connection_is_closed_on_exit(telethon):
    with listen_telethon(lambda: telethon, [WATCHED_CHANNEL]) as live, live.connect():
        pass
    assert telethon.closed


def test_live_connection_is_not_opened_until_a_run_needs_it(telethon):
    with listen_telethon(lambda: telethon, [WATCHED_CHANNEL]):
        pass
    assert telethon.connections == 0


@pytest.mark.parametrize("event_type", [events.NewMessage, events.MessageEdited])
def test_new_or_edited_post_in_a_watched_chat_ends_one_wait(telethon, live, event_type):
    telethon.deliver(event_type, WATCHED_CHAT)
    assert waited(live, LONG_WAIT) < LONG_WAIT.total_seconds() / 2
    assert waited(live, SHORT_WAIT) >= SHORT_WAIT.total_seconds()


def test_posts_in_other_chats_do_not_end_the_wait(telethon, live):
    telethon.deliver(events.NewMessage, UNRELATED_CHAT)
    assert waited(live, SHORT_WAIT) >= SHORT_WAIT.total_seconds()


def test_lost_connection_ends_the_wait(telethon, live):
    telethon.drop_connection()
    assert waited(live, LONG_WAIT) < LONG_WAIT.total_seconds() / 2


def test_unanswered_request_is_a_network_failure(telethon):
    telethon.unanswered = True
    with (
        listen_telethon(
            lambda: telethon, [WATCHED_CHANNEL], request_timeout=SHORT_WAIT
        ) as live,
        live.connect() as client,
        pytest.raises(NetworkUnavailable),
    ):
        client.latest_message_id(WATCHED_CHANNEL)


def test_failure_to_open_the_live_connection_is_a_network_failure(telethon):
    telethon.connect_failure = ConnectionError()
    with (
        listen_telethon(lambda: telethon, [WATCHED_CHANNEL]) as live,
        pytest.raises(NetworkUnavailable),
        live.connect(),
    ):
        pass

from collections.abc import Sequence
from contextlib import AbstractContextManager
from functools import partial
from getpass import getpass
from pathlib import Path

from telethon import TelegramClient
from telethon.sessions import StringSession

from telegram_watcher.chat_picker import watched_chats
from telegram_watcher.client import AuthorizationFailed, HaltReason
from telegram_watcher.config import WatchedChat
from telegram_watcher.credentials import Credentials
from telegram_watcher.private_files import read_private_file
from telegram_watcher.telethon_client import (
    LiveTelethon,
    TelethonChatClient,
    connect_telethon,
    listen_telethon,
)

CLIENT_OPTIONS = {
    "device_model": "Telegram watcher",
    "system_version": "Linux",
    "app_version": "0.1.0",
    "connection_retries": 1,
    "request_retries": 2,
    "retry_delay": 1,
    "timeout": 10,
    "auto_reconnect": False,
}


def new_client(
    credentials: Credentials, session: str, *, receive_updates: bool = False
) -> TelegramClient:
    return TelegramClient(
        StringSession(session),
        credentials.api_id,
        credentials.api_hash,
        receive_updates=receive_updates,
        **CLIENT_OPTIONS,
    )


def open_telegram(
    credentials: Credentials, session_path: Path
) -> AbstractContextManager[TelethonChatClient]:
    session = read_private_file(session_path)
    if session is None:
        raise AuthorizationFailed(HaltReason.SESSION_ENDED)
    return connect_telethon(partial(new_client, credentials, session))


def listen_to_telegram(
    credentials: Credentials, session_path: Path, chats: Sequence[WatchedChat]
) -> AbstractContextManager[LiveTelethon]:
    return listen_telethon(partial(_new_live_client, credentials, session_path), chats)


def _new_live_client(credentials: Credentials, session_path: Path) -> TelegramClient:
    session = read_private_file(session_path)
    if session is None:
        raise AuthorizationFailed(HaltReason.SESSION_ENDED)
    return new_client(credentials, session, receive_updates=True)


async def sign_in(credentials: Credentials) -> str:
    client = new_client(credentials, "")
    try:
        await client.start(
            phone=lambda: input("Phone number, with country code: "),
            code_callback=lambda: getpass("Login code sent by Telegram: "),
            password=lambda: getpass("Two-step verification password: "),
        )
        return client.session.save()
    finally:
        await client.disconnect()


async def list_chats(credentials: Credentials, session: str) -> list[WatchedChat]:
    client = new_client(credentials, session)
    await client.connect()
    try:
        return watched_chats([dialog.entity async for dialog in client.iter_dialogs()])
    finally:
        await client.disconnect()

from contextlib import AbstractContextManager
from functools import partial
from getpass import getpass
from pathlib import Path

from telethon import TelegramClient
from telethon.sessions import StringSession

from group_watcher.chat_picker import watched_chats
from group_watcher.client import AuthorizationFailed, HaltReason
from group_watcher.config import WatchedChat
from group_watcher.credentials import Credentials
from group_watcher.private_files import read_private_file
from group_watcher.telethon_client import TelethonChatClient, connect_telethon

CLIENT_OPTIONS = {
    "receive_updates": False,
    "device_model": "Group watcher",
    "system_version": "Linux",
    "app_version": "0.1.0",
    "connection_retries": 1,
    "request_retries": 2,
    "retry_delay": 1,
    "timeout": 10,
    "auto_reconnect": False,
}


def new_client(credentials: Credentials, session: str) -> TelegramClient:
    return TelegramClient(
        StringSession(session),
        credentials.api_id,
        credentials.api_hash,
        **CLIENT_OPTIONS,
    )


def open_telegram(
    credentials: Credentials, session_path: Path
) -> AbstractContextManager[TelethonChatClient]:
    session = read_private_file(session_path)
    if session is None:
        raise AuthorizationFailed(HaltReason.SESSION_ENDED)
    return connect_telethon(partial(new_client, credentials, session))


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

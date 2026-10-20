import argparse
import asyncio
import logging
import sqlite3
import sys
from collections.abc import Sequence
from contextlib import closing
from datetime import UTC, datetime
from functools import partial
from getpass import getpass
from pathlib import Path

from telethon import errors

from telegram_watcher.chat_picker import select_chats, terminal_safe
from telegram_watcher.config import (
    Config,
    ConfigError,
    WatchedChat,
    load_config,
    save_config,
)
from telegram_watcher.credentials import (
    Credentials,
    CredentialsError,
    load_credentials,
    save_credentials,
)
from telegram_watcher.desktop_notifier import DesktopNotifier
from telegram_watcher.forward_target import load_forward_target
from telegram_watcher.http_forwarder import HttpForwarder
from telegram_watcher.listeners import load_listeners
from telegram_watcher.notifier import NotificationFailed, Notifier
from telegram_watcher.poll_run import RunOutcome
from telegram_watcher.private_files import (
    PrivateFileError,
    read_private_file,
    write_private_file,
)
from telegram_watcher.run_lock import AlreadyRunning, run_lock
from telegram_watcher.storage.account_state_repository import AccountStateRepository
from telegram_watcher.storage.database import (
    StorageError,
    create_database,
    open_database,
)
from telegram_watcher.telegram_account import (
    list_chats,
    listen_to_telegram,
    open_telegram,
    sign_in,
)
from telegram_watcher.watcher import run_watcher, watch_live

VAR_DIR = Path("var")
DATABASE_PATH = VAR_DIR / "watcher.db"
CONFIG_PATH = VAR_DIR / "config.toml"
CREDENTIALS_PATH = VAR_DIR / "credentials.toml"
SESSION_PATH = VAR_DIR / "telegram.session"
LOCK_PATH = VAR_DIR / "run.lock"
FORWARD_PATH = VAR_DIR / "forward.toml"
LISTENERS_DIR = Path("listeners")
SETUP_ERRORS = (OSError, sqlite3.Error, ConfigError, CredentialsError, StorageError)

logger = logging.getLogger(__name__)


def main(argv: Sequence[str] | None = None) -> int:
    commands = {
        "init": (initialize, "create var/ with an empty database"),
        "login": (log_in, "save the API credentials and log in to Telegram"),
        "pick-chats": (pick_chats, "choose the chats to watch, once"),
        "run": (run, "poll the watched chats once"),
        "listen": (listen, "poll the watched chats whenever a new post arrives"),
        "clear-halt": (clear_halt, "resume runs after fixing what halted them"),
    }
    parser = argparse.ArgumentParser(prog="telegram-watcher")
    subcommands = parser.add_subparsers(required=True)
    for name, (command, help_text) in commands.items():
        subcommands.add_parser(name, help=help_text).set_defaults(command=command)
    arguments = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )
    logging.getLogger("telethon").setLevel(logging.WARNING)
    return arguments.command()


def initialize() -> int:
    VAR_DIR.mkdir(mode=0o700, exist_ok=True)
    try:
        create_database(DATABASE_PATH)
    except StorageError as error:
        return _fail(error)
    print(f"Created {DATABASE_PATH}.")
    return 0


def log_in() -> int:
    if not VAR_DIR.is_dir():
        return _fail("run telegram-watcher init first")
    try:
        if CREDENTIALS_PATH.exists():
            credentials = load_credentials(CREDENTIALS_PATH)
        else:
            credentials = _ask_for_credentials()
        session = asyncio.run(sign_in(credentials))
    except CredentialsError as error:
        return _fail(error)
    except (OSError, errors.RPCError) as error:
        return _fail(f"login failed: {type(error).__name__}")
    replaced_session = SESSION_PATH.exists()
    save_credentials(CREDENTIALS_PATH, credentials)
    write_private_file(SESSION_PATH, session)
    print(f"Logged in and saved {SESSION_PATH}.")
    if replaced_session:
        print(
            "The previous session was replaced. If it is still listed in Telegram"
            " under Settings -> Devices, terminate it there."
        )
    print("If runs were halted, run telegram-watcher clear-halt.")
    return 0


def pick_chats() -> int:
    if CONFIG_PATH.exists():
        return _fail(f"{CONFIG_PATH} exists; edit it, or delete it to pick again")
    try:
        credentials = load_credentials(CREDENTIALS_PATH)
        session = read_private_file(SESSION_PATH)
    except (CredentialsError, PrivateFileError) as error:
        return _fail(error)
    if session is None:
        return _fail("not logged in; run telegram-watcher login")
    try:
        chats = asyncio.run(list_chats(credentials, session))
    except (OSError, errors.RPCError) as error:
        return _fail(f"cannot list chats: {type(error).__name__}")
    if not chats:
        return _fail("the account has no chats that can be watched")
    for number, chat in enumerate(chats, start=1):
        print(f"{number:4}. {terminal_safe(chat.name)}")
    selected = _ask_for_selection(chats)
    save_config(CONFIG_PATH, Config(chats=selected))
    print(f"Saved {len(selected)} chats to {CONFIG_PATH}.")
    return 0


def run() -> int:
    notifier = DesktopNotifier()
    try:
        with run_lock(LOCK_PATH):
            config = load_config(CONFIG_PATH)
            credentials = load_credentials(CREDENTIALS_PATH)
            forward_target = load_forward_target(FORWARD_PATH)
            with closing(open_database(DATABASE_PATH)) as connection:
                report = run_watcher(
                    connection,
                    chats=config.chats,
                    connect=partial(open_telegram, credentials, SESSION_PATH),
                    listeners=load_listeners(LISTENERS_DIR),
                    notifier=notifier,
                    forwarder=HttpForwarder(forward_target) if forward_target else None,
                    clock=partial(datetime.now, UTC),
                )
    except AlreadyRunning:
        logger.info("run skipped: another run is still active")
        return 0
    except SETUP_ERRORS as error:
        logger.error("cannot run: %s", error)
        _notify_if_possible(
            notifier,
            f"Telegram watcher cannot run: {type(error).__name__}. See the log.",
        )
        return 1
    logger.info("run finished: %s", report.outcome)
    return 1 if report.outcome is RunOutcome.FAILED else 0


def listen() -> int:
    notifier = DesktopNotifier()
    try:
        with run_lock(LOCK_PATH):
            config = load_config(CONFIG_PATH)
            credentials = load_credentials(CREDENTIALS_PATH)
            forward_target = load_forward_target(FORWARD_PATH)
            with (
                closing(open_database(DATABASE_PATH)) as connection,
                listen_to_telegram(credentials, SESSION_PATH, config.chats) as telegram,
            ):
                outcome = watch_live(
                    connection,
                    chats=config.chats,
                    telegram=telegram,
                    listeners=load_listeners(LISTENERS_DIR),
                    notifier=notifier,
                    forwarder=HttpForwarder(forward_target) if forward_target else None,
                    clock=partial(datetime.now, UTC),
                )
    except KeyboardInterrupt:
        logger.info("listener stopped")
        return 0
    except AlreadyRunning:
        logger.error("cannot listen: another run or listener is active")
        return 1
    except SETUP_ERRORS as error:
        logger.error("cannot listen: %s", error)
        _notify_if_possible(
            notifier,
            f"Telegram watcher cannot listen: {type(error).__name__}. See the log.",
        )
        return 1
    logger.info("listener stopped: %s", outcome)
    return 0 if outcome is RunOutcome.HALTED else 1


def clear_halt() -> int:
    try:
        connection = open_database(DATABASE_PATH)
    except StorageError as error:
        return _fail(error)
    with closing(connection), connection:
        account_state = AccountStateRepository(connection)
        halt_reason = account_state.halt_reason()
        account_state.clear_halt()
    print(
        f"Cleared the halt: {halt_reason}." if halt_reason else "Runs were not halted."
    )
    return 0


def _ask_for_credentials() -> Credentials:
    print("Enter the app credentials from my.telegram.org -> API development tools.")
    api_id = input("api_id: ").strip()
    api_hash = getpass("api_hash (input hidden): ").strip()
    if not api_id.isdecimal():
        raise CredentialsError("api_id must be a positive whole number")
    return Credentials(api_id=int(api_id), api_hash=api_hash)


def _ask_for_selection(chats: Sequence[WatchedChat]) -> tuple[WatchedChat, ...]:
    while True:
        try:
            return select_chats(chats, input("Numbers of the chats to watch: "))
        except ValueError as error:
            print(error)


def _notify_if_possible(notifier: Notifier, message: str) -> None:
    try:
        notifier.notify(message)
    except NotificationFailed:
        logger.warning("notification not delivered")


def _fail(error: object) -> int:
    print(f"error: {error}", file=sys.stderr)
    return 1

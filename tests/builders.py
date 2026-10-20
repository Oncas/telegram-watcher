from contextlib import closing

from telegram_watcher.chat_poller import ChatPoller
from telegram_watcher.listeners import Listeners
from telegram_watcher.poll_run import PollRun
from telegram_watcher.recorder import PostRecorder
from telegram_watcher.storage.account_state_repository import AccountStateRepository
from telegram_watcher.storage.checkpoint_repository import CheckpointRepository
from telegram_watcher.storage.database import open_database
from telegram_watcher.storage.match_repository import MatchRepository
from telegram_watcher.storage.post_repository import PostRepository
from telegram_watcher.watcher import run_watcher


def make_recorder(connection):
    return PostRecorder(
        transaction=connection,
        post_repository=PostRepository(connection),
        checkpoint_repository=CheckpointRepository(connection),
        listeners=Listeners(()),
        match_repository=MatchRepository(connection),
    )


def make_chat_poller(connection, clock, image_store, **limits):
    return ChatPoller(
        recorder=make_recorder(connection),
        image_store=image_store,
        post_repository=PostRepository(connection),
        checkpoint_repository=CheckpointRepository(connection),
        clock=clock,
        **limits,
    )


def make_poll_run(connection, *, chats, telegram, clock, image_store):
    return PollRun(
        chats=chats,
        connect=telegram.connect,
        chat_poller=make_chat_poller(connection, clock, image_store),
        transaction=connection,
        account_state_repository=AccountStateRepository(connection),
        clock=clock,
    )


def run_hourly(
    db_path,
    *,
    chats,
    telegram,
    notifier,
    clock,
    image_store,
    listeners=(),
    forwarder=None,
):
    with closing(open_database(db_path)) as connection:
        return run_watcher(
            connection,
            chats=chats,
            connect=telegram.connect,
            listeners=listeners,
            notifier=notifier,
            forwarder=forwarder,
            image_store=image_store,
            clock=clock,
        )

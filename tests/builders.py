from contextlib import closing

from group_watcher.chat_poller import ChatPoller
from group_watcher.poll_run import PollRun
from group_watcher.recorder import PostRecorder
from group_watcher.storage.account_state_repository import AccountStateRepository
from group_watcher.storage.checkpoint_repository import CheckpointRepository
from group_watcher.storage.database import open_database
from group_watcher.storage.post_repository import PostRepository
from group_watcher.watcher import run_watcher


def make_recorder(connection):
    return PostRecorder(
        transaction=connection,
        post_repository=PostRepository(connection),
        checkpoint_repository=CheckpointRepository(connection),
    )


def make_chat_poller(connection, clock, **limits):
    return ChatPoller(
        recorder=make_recorder(connection),
        post_repository=PostRepository(connection),
        checkpoint_repository=CheckpointRepository(connection),
        clock=clock,
        **limits,
    )


def make_poll_run(connection, *, chats, telegram, clock):
    return PollRun(
        chats=chats,
        connect=telegram.connect,
        chat_poller=make_chat_poller(connection, clock),
        transaction=connection,
        account_state_repository=AccountStateRepository(connection),
        clock=clock,
    )


def run_hourly(db_path, *, chats, telegram, notifier, clock):
    with closing(open_database(db_path)) as connection:
        return run_watcher(
            connection,
            chats=chats,
            connect=telegram.connect,
            notifier=notifier,
            clock=clock,
        )

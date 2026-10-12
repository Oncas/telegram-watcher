from contextlib import closing

from group_watcher.alerts import Alerts
from group_watcher.chat_poller import ChatPoller
from group_watcher.poll_run import PollRun
from group_watcher.recorder import PostRecorder
from group_watcher.run_monitor import RunMonitor
from group_watcher.storage.account_state_repository import AccountStateRepository
from group_watcher.storage.alert_repository import AlertRepository
from group_watcher.storage.chat_poll_repository import ChatPollRepository
from group_watcher.storage.checkpoint_repository import CheckpointRepository
from group_watcher.storage.database import open_database
from group_watcher.storage.post_repository import PostRepository
from group_watcher.storage.run_log_repository import RunLogRepository


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


def make_poll_run(connection, *, chats, telegram, notifier, clock):
    return PollRun(
        chats=chats,
        connect=telegram.connect,
        chat_poller=make_chat_poller(connection, clock),
        transaction=connection,
        account_state_repository=AccountStateRepository(connection),
        notifier=notifier,
        clock=clock,
    )


def make_run_monitor(connection, *, chats, notifier):
    return RunMonitor(
        chats=chats,
        transaction=connection,
        run_log_repository=RunLogRepository(connection),
        chat_poll_repository=ChatPollRepository(connection),
        alerts=Alerts(
            transaction=connection,
            alert_repository=AlertRepository(connection),
            notifier=notifier,
        ),
    )


def run_hourly(db_path, *, chats, telegram, notifier, clock):
    with closing(open_database(db_path)) as connection:
        report = make_poll_run(
            connection, chats=chats, telegram=telegram, notifier=notifier, clock=clock
        ).run()
        make_run_monitor(connection, chats=chats, notifier=notifier).record(report)
    return report

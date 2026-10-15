import sqlite3
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from datetime import datetime

from telegram_watcher.alerts import Alerts
from telegram_watcher.chat_poller import ChatPoller
from telegram_watcher.client import ChatClient
from telegram_watcher.config import WatchedChat
from telegram_watcher.forwarder import Forwarder
from telegram_watcher.listeners import Listener, Listeners
from telegram_watcher.match_delivery import MatchDelivery
from telegram_watcher.notifier import Notifier
from telegram_watcher.poll_run import PollRun, RunReport
from telegram_watcher.recorder import PostRecorder
from telegram_watcher.run_monitor import RunMonitor
from telegram_watcher.storage.account_state_repository import AccountStateRepository
from telegram_watcher.storage.alert_repository import AlertRepository
from telegram_watcher.storage.chat_poll_repository import ChatPollRepository
from telegram_watcher.storage.checkpoint_repository import CheckpointRepository
from telegram_watcher.storage.match_repository import MatchRepository
from telegram_watcher.storage.post_repository import PostRepository
from telegram_watcher.storage.run_log_repository import RunLogRepository


def run_watcher(
    connection: sqlite3.Connection,
    *,
    chats: Sequence[WatchedChat],
    connect: Callable[[], AbstractContextManager[ChatClient]],
    listeners: Sequence[Listener],
    notifier: Notifier,
    forwarder: Forwarder | None,
    clock: Callable[[], datetime],
) -> RunReport:
    post_repository = PostRepository(connection)
    checkpoint_repository = CheckpointRepository(connection)
    match_repository = MatchRepository(connection)
    alerts = Alerts(
        transaction=connection,
        alert_repository=AlertRepository(connection),
        notifier=notifier,
    )
    poll_run = PollRun(
        chats=chats,
        connect=connect,
        chat_poller=ChatPoller(
            recorder=PostRecorder(
                transaction=connection,
                post_repository=post_repository,
                checkpoint_repository=checkpoint_repository,
                listeners=Listeners(listeners),
                match_repository=match_repository,
            ),
            post_repository=post_repository,
            checkpoint_repository=checkpoint_repository,
            clock=clock,
        ),
        transaction=connection,
        account_state_repository=AccountStateRepository(connection),
        clock=clock,
    )
    match_delivery = MatchDelivery(
        transaction=connection,
        match_repository=match_repository,
        notifier=notifier,
        forwarder=forwarder,
        alerts=alerts,
        clock=clock,
    )
    run_monitor = RunMonitor(
        chats=chats,
        transaction=connection,
        run_log_repository=RunLogRepository(connection),
        chat_poll_repository=ChatPollRepository(connection),
        alerts=alerts,
    )
    report = poll_run.run()
    match_delivery.deliver()
    run_monitor.record(report)
    return report

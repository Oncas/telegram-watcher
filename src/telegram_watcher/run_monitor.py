from collections.abc import Sequence
from contextlib import AbstractContextManager
from datetime import timedelta

from telegram_watcher.alerts import AlertKind, Alerts
from telegram_watcher.client import HaltReason
from telegram_watcher.config import WatchedChat
from telegram_watcher.poll_run import RunOutcome, RunReport
from telegram_watcher.storage.chat_poll_repository import ChatPollRepository
from telegram_watcher.storage.run_log_repository import RunLogRepository

STALE_AFTER = timedelta(hours=24)

HALT_NOTICES = {
    HaltReason.SESSION_ENDED: (
        "Telegram watcher halted: the session ended. Log in again, then clear the halt."
    ),
    HaltReason.ACCOUNT_DEACTIVATED: (
        "Telegram watcher halted: the account is deactivated or banned."
        " Logging in again will not help."
    ),
    HaltReason.AUTHORIZATION_REFUSED: (
        "Telegram watcher halted: authorization was refused."
        " Review the reason in the log before clearing the halt."
    ),
}


class RunMonitor:
    def __init__(
        self,
        *,
        chats: Sequence[WatchedChat],
        transaction: AbstractContextManager,
        run_log_repository: RunLogRepository,
        chat_poll_repository: ChatPollRepository,
        alerts: Alerts,
    ):
        self._chats = chats
        self._transaction = transaction
        self._run_log = run_log_repository
        self._chat_polls = chat_poll_repository
        self._alerts = alerts

    def record(self, report: RunReport) -> None:
        with self._transaction:
            self._run_log.record(
                started_at=report.started_at,
                finished_at=report.finished_at,
                outcome=report.outcome,
                error_type=report.error_type,
            )
            for chat in self._chats:
                self._chat_polls.start_watching(chat.chat_id, report.started_at)
            for chat in report.polled_chats:
                self._chat_polls.record_success(chat.chat_id, report.finished_at)
        self._alert_on_halt(report)
        self._alert_on_chats(report)
        self._alert_on_failure(report)

    def _alert_on_halt(self, report: RunReport) -> None:
        if report.halt_reason is None:
            self._alerts.resolve_all(AlertKind.HALTED)
            return
        self._alerts.raise_alert(
            AlertKind.HALTED, report.halt_reason, HALT_NOTICES[report.halt_reason]
        )

    def _alert_on_chats(self, report: RunReport) -> None:
        for chat in report.polled_chats:
            self._alerts.resolve(AlertKind.CHAT_UNAVAILABLE, str(chat.chat_id))
            self._alerts.resolve(AlertKind.CHAT_STALE, str(chat.chat_id))
        for chat in report.unavailable_chats:
            self._alerts.raise_alert(
                AlertKind.CHAT_UNAVAILABLE,
                str(chat.chat_id),
                f"Telegram watcher could not poll {chat.name}.",
            )
        if report.outcome is RunOutcome.HALTED:
            return
        stale_chat_ids = set(
            self._chat_polls.stale_chat_ids(report.finished_at - STALE_AFTER)
        )
        for chat in self._chats:
            if chat.chat_id in stale_chat_ids:
                self._alerts.raise_alert(
                    AlertKind.CHAT_STALE,
                    str(chat.chat_id),
                    f"Telegram watcher has not polled {chat.name}"
                    f" for over {STALE_AFTER // timedelta(hours=1)} hours.",
                )

    def _alert_on_failure(self, report: RunReport) -> None:
        if report.outcome is RunOutcome.FAILED:
            self._alerts.raise_alert(
                AlertKind.RUN_FAILED,
                report.error_type,
                f"Telegram watcher run failed: {report.error_type}.",
            )
        elif report.outcome is RunOutcome.COMPLETED:
            self._alerts.resolve_all(AlertKind.RUN_FAILED)

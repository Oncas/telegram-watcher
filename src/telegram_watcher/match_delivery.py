import logging
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import datetime
from typing import Any

from telegram_watcher.alerts import AlertKind, Alerts
from telegram_watcher.forwarder import Forwarder, ForwardFailed
from telegram_watcher.matches import Match
from telegram_watcher.notifier import NotificationFailed, Notifier
from telegram_watcher.storage.match_repository import MatchRepository
from telegram_watcher.storage.timestamps import format_timestamp

logger = logging.getLogger(__name__)


class MatchDelivery:
    def __init__(
        self,
        *,
        transaction: AbstractContextManager,
        match_repository: MatchRepository,
        notifier: Notifier,
        forwarder: Forwarder | None,
        alerts: Alerts,
        clock: Callable[[], datetime],
    ):
        self._transaction = transaction
        self._matches = match_repository
        self._notifier = notifier
        self._forwarder = forwarder
        self._alerts = alerts
        self._clock = clock

    def deliver(self) -> None:
        if self._forwarder is not None:
            self._forward_pending(self._forwarder)
        self._notify_pending()

    def _forward_pending(self, forwarder: Forwarder) -> None:
        for match in self._matches.unforwarded():
            try:
                forwarder.forward(forward_payload(match))
            except ForwardFailed as error:
                logger.warning("match not forwarded, retrying next run: %s", error)
                self._alerts.raise_alert(
                    AlertKind.FORWARD_FAILED,
                    str(error),
                    f"Telegram watcher could not forward listener matches: {error}.",
                )
                return
            with self._transaction:
                self._matches.mark_forwarded(match, self._clock())
            self._alerts.resolve_all(AlertKind.FORWARD_FAILED)

    def _notify_pending(self) -> None:
        for match in self._matches.unnotified():
            try:
                self._notifier.notify(notification_text(match))
            except NotificationFailed:
                logger.warning(
                    "match notification not delivered, retrying next run: %s",
                    match.listener,
                )
                return
            with self._transaction:
                self._matches.mark_notified(match, self._clock())


def forward_payload(match: Match) -> dict[str, Any]:
    return {
        "id": match.match_id,
        "listener": match.listener,
        "message_id": match.message_id,
        "posted_at": format_timestamp(match.posted_at),
        "edited_at": format_timestamp(match.edited_at),
        "data": match.data,
    }


def notification_text(match: Match) -> str:
    return "\n".join(
        [match.listener, *(f"{key}: {value}" for key, value in match.data.items())]
    )

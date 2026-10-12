import logging
from contextlib import AbstractContextManager
from enum import StrEnum

from group_watcher.notifier import NotificationFailed, Notifier
from group_watcher.storage.alert_repository import AlertRepository

logger = logging.getLogger(__name__)


class AlertKind(StrEnum):
    CHAT_UNAVAILABLE = "chat_unavailable"
    CHAT_STALE = "chat_stale"
    RUN_FAILED = "run_failed"
    HALTED = "halted"


class Alerts:
    def __init__(
        self,
        *,
        transaction: AbstractContextManager,
        alert_repository: AlertRepository,
        notifier: Notifier,
    ):
        self._transaction = transaction
        self._alerts = alert_repository
        self._notifier = notifier

    def raise_alert(self, kind: AlertKind, subject: str, message: str) -> None:
        if self._alerts.is_active(kind, subject):
            return
        try:
            self._notifier.notify(message)
        except NotificationFailed:
            logger.warning("notification not delivered, retrying next run: %s", kind)
            return
        with self._transaction:
            self._alerts.activate(kind, subject)

    def resolve(self, kind: AlertKind, subject: str) -> None:
        with self._transaction:
            self._alerts.deactivate(kind, subject)

    def resolve_all(self, kind: AlertKind) -> None:
        with self._transaction:
            self._alerts.deactivate_all(kind)

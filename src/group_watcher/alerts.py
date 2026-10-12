from contextlib import AbstractContextManager
from enum import StrEnum

from group_watcher.notifier import Notifier
from group_watcher.storage.alert_repository import AlertRepository


class AlertKind(StrEnum):
    CHAT_UNAVAILABLE = "chat_unavailable"
    CHAT_STALE = "chat_stale"
    RUN_FAILED = "run_failed"


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
        self._notifier.notify(message)
        with self._transaction:
            self._alerts.activate(kind, subject)

    def resolve(self, kind: AlertKind, subject: str) -> None:
        with self._transaction:
            self._alerts.deactivate(kind, subject)

    def resolve_all(self, kind: AlertKind) -> None:
        with self._transaction:
            self._alerts.deactivate_all(kind)

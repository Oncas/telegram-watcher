from typing import Protocol


class NotificationFailed(Exception):
    pass


class Notifier(Protocol):
    def notify(self, message: str) -> None: ...

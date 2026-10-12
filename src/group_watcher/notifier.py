from typing import Protocol


class Notifier(Protocol):
    def notify(self, message: str) -> None: ...

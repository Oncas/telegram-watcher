from collections.abc import Mapping
from typing import Any, Protocol


class ForwardFailed(Exception):
    pass


class Forwarder(Protocol):
    def forward(self, payload: Mapping[str, Any]) -> None: ...

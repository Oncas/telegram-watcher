import importlib.util
import json
import logging
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

from group_watcher.matches import Match
from group_watcher.posts import Post

TEMPLATE_NAME = "example"

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True, kw_only=True)
class Listener:
    name: str
    chat_id: int
    listen: Callable[[Post], Iterable[dict[str, Any]]]


def load_listeners(directory: Path) -> tuple[Listener, ...]:
    listeners = []
    for path in sorted(directory.glob("*.py")):
        if path.stem == TEMPLATE_NAME:
            continue
        try:
            module = _import(path)
            listeners.append(
                Listener(name=path.stem, chat_id=module.CHAT_ID, listen=module.listen)
            )
        except Exception as error:  # noqa: BLE001
            logger.error("listener not loaded: %s, %r", path.stem, error)
    return tuple(listeners)


def _import(path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"listeners.{path.stem}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Listeners:
    def __init__(self, listeners: Sequence[Listener]):
        self._listeners = listeners

    def hear(self, post: Post) -> list[Match]:
        return [
            match
            for listener in self._listeners
            if listener.chat_id == post.chat_id
            for match in self._hear(listener, post)
        ]

    def _hear(self, listener: Listener, post: Post) -> list[Match]:
        try:
            found = [_as_json_object(data) for data in listener.listen(post)]
        except Exception as error:  # noqa: BLE001
            logger.error("listener failed: %s, %s", listener.name, type(error).__name__)
            return []
        return [
            Match(
                listener=listener.name,
                chat_id=post.chat_id,
                message_id=post.message_id,
                posted_at=post.posted_at,
                edited_at=post.edited_at,
                data=data,
            )
            for data in found
        ]


def _as_json_object(data: object) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise TypeError(f"listeners yield dicts, not {type(data).__name__}")
    return json.loads(json.dumps(data, allow_nan=False))

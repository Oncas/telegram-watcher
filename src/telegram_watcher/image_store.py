import os
from pathlib import Path

from telegram_watcher.client import Image

DOWNLOADED = "downloaded"
FOLDERS = (DOWNLOADED, "rejected", "archived")


class ImageStore:
    def __init__(self, root: Path):
        self._root = root

    def clear_unfinished(self) -> None:
        for unfinished in self._root.glob(".*.part"):
            unfinished.unlink()

    def contains(self, chat_id: int, message_id: int) -> bool:
        name = _name(chat_id, message_id)
        return any(
            entry.name == name or entry.name.startswith(f"{name}.")
            for folder in FOLDERS
            for entry in (self._root / folder).glob(f"{name}*")
        )

    def add(self, chat_id: int, message_id: int, image: Image) -> None:
        self._root.mkdir(mode=0o700, parents=True, exist_ok=True)
        for folder in FOLDERS:
            (self._root / folder).mkdir(mode=0o700, exist_ok=True)
        name = f"{_name(chat_id, message_id)}{image.extension}"
        temporary = self._root / f".{name}.part"
        temporary.unlink(missing_ok=True)
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as file:
            file.write(image.data)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, self._root / DOWNLOADED / name)
        directory = os.open(self._root / DOWNLOADED, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)


def _name(chat_id: int, message_id: int) -> str:
    return f"{chat_id}_{message_id}"

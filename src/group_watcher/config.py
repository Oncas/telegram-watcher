import tomllib
from dataclasses import dataclass
from pathlib import Path

from group_watcher.chat_ids import is_channel_id, is_marked_group_or_channel_id

CONFIG_KEYS = {"chats"}
CHAT_KEYS = {"name", "chat_id", "access_hash"}


class ConfigError(Exception):
    pass


@dataclass(frozen=True, slots=True, kw_only=True)
class WatchedChat:
    name: str
    chat_id: int
    access_hash: int | None


@dataclass(frozen=True, slots=True)
class Config:
    chats: tuple[WatchedChat, ...]


def load_config(path: Path) -> Config:
    data = _read_toml(path)
    _reject_unknown_keys(data, CONFIG_KEYS, "config")
    entries = data.get("chats")
    if not entries:
        raise ConfigError("config lists no chats")
    if not isinstance(entries, list) or not all(
        isinstance(entry, dict) for entry in entries
    ):
        raise ConfigError("chats must be written as [[chats]] tables")
    chats = tuple(
        _parse_chat(entry, f"chat #{number}")
        for number, entry in enumerate(entries, start=1)
    )
    _reject_duplicate_chat_ids(chats)
    return Config(chats=chats)


def _read_toml(path: Path) -> dict:
    try:
        with path.open("rb") as file:
            return tomllib.load(file)
    except OSError as error:
        raise ConfigError(f"cannot read {path}: {error.strerror}") from error
    except tomllib.TOMLDecodeError as error:
        raise ConfigError(f"{path} is not valid TOML: {error}") from error


def _parse_chat(entry: dict, label: str) -> WatchedChat:
    _reject_unknown_keys(entry, CHAT_KEYS, label)
    name = _required(entry, "name", str, label)
    chat_id = _required(entry, "chat_id", int, label)
    access_hash = _optional(entry, "access_hash", int, label)
    if not is_marked_group_or_channel_id(chat_id):
        raise ConfigError(f"{label}: chat_id must be a marked group or channel ID")
    if is_channel_id(chat_id) and access_hash is None:
        raise ConfigError(f"{label}: a channel needs an access_hash")
    if not is_channel_id(chat_id) and access_hash is not None:
        raise ConfigError(
            f"{label}: a basic group has no access_hash;"
            " is the -100 prefix missing from chat_id?"
        )
    return WatchedChat(name=name, chat_id=chat_id, access_hash=access_hash)


def _required(entry: dict, key: str, value_type: type, label: str):
    if key not in entry:
        raise ConfigError(f"{label}: {key} is missing")
    return _optional(entry, key, value_type, label)


def _optional(entry: dict, key: str, value_type: type, label: str):
    value = entry.get(key)
    if value is not None and type(value) is not value_type:
        raise ConfigError(
            f"{label}: {key} has the wrong type, expected {value_type.__name__}"
        )
    return value


def _reject_unknown_keys(table: dict, known_keys: set[str], label: str) -> None:
    unknown_keys = sorted(table.keys() - known_keys)
    if unknown_keys:
        raise ConfigError(f"{label}: unknown keys {', '.join(unknown_keys)}")


def _reject_duplicate_chat_ids(chats: tuple[WatchedChat, ...]) -> None:
    seen = set()
    for chat in chats:
        if chat.chat_id in seen:
            raise ConfigError(f"chat_id {chat.chat_id} is listed more than once")
        seen.add(chat.chat_id)

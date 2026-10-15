import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from group_watcher.config import ConfigError
from group_watcher.private_files import PrivateFileError, read_private_file

FORWARD_KEYS = {"url", "token"}
TOKEN_PATTERN = re.compile(r"[A-Za-z0-9._~+/=-]+")


@dataclass(frozen=True, slots=True)
class ForwardTarget:
    url: str
    token: str = field(repr=False)


def load_forward_target(path: Path) -> ForwardTarget | None:
    try:
        text = read_private_file(path)
    except PrivateFileError as error:
        raise ConfigError(str(error)) from None
    if text is None:
        return None
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        raise ConfigError(f"{path} is not valid TOML") from None
    if data.keys() != FORWARD_KEYS or not all(
        isinstance(value, str) for value in data.values()
    ):
        raise ConfigError(f"{path} must contain exactly a url and a token, as text")
    if not _is_web_address(data["url"]):
        raise ConfigError(f"{path}: url must be an http:// or https:// address")
    if not TOKEN_PATTERN.fullmatch(data["token"]):
        raise ConfigError(f"{path}: token must be letters, digits and ._~+/=-")
    return ForwardTarget(url=data["url"], token=data["token"])


def _is_web_address(url: str) -> bool:
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return False
    return parts.scheme in {"http", "https"} and bool(parts.hostname) and port != 0

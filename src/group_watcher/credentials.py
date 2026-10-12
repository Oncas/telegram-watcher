import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from group_watcher.private_files import (
    PrivateFileError,
    read_private_file,
    write_private_file,
)

API_HASH_PATTERN = re.compile(r"[0-9a-f]{32}")
CREDENTIAL_KEYS = {"api_id", "api_hash"}


class CredentialsError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class Credentials:
    api_id: int
    api_hash: str = field(repr=False)

    def __post_init__(self):
        if type(self.api_id) is not int or self.api_id <= 0:
            raise CredentialsError("api_id must be a positive whole number")
        if type(self.api_hash) is not str or not API_HASH_PATTERN.fullmatch(
            self.api_hash
        ):
            raise CredentialsError("api_hash must be 32 hexadecimal characters")


def load_credentials(path: Path) -> Credentials:
    try:
        text = read_private_file(path)
    except PrivateFileError as error:
        raise CredentialsError(str(error)) from None
    if text is None:
        raise CredentialsError(f"{path} is missing; run group-watcher login")
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        raise CredentialsError(f"{path} is not valid TOML") from None
    if data.keys() != CREDENTIAL_KEYS:
        raise CredentialsError(f"{path} must contain exactly api_id and api_hash")
    return Credentials(api_id=data["api_id"], api_hash=data["api_hash"])


def save_credentials(path: Path, credentials: Credentials) -> None:
    write_private_file(
        path,
        f'api_id = {credentials.api_id}\napi_hash = "{credentials.api_hash}"\n',
    )

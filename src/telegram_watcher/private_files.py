import os
import stat
from pathlib import Path


class PrivateFileError(Exception):
    pass


def read_private_file(path: Path) -> str | None:
    try:
        with path.open(encoding="utf-8") as file:
            if stat.S_IMODE(os.fstat(file.fileno()).st_mode) & 0o077:
                raise PrivateFileError(
                    f"{path} is readable by other users; run chmod 600 {path}"
                )
            return file.read()
    except FileNotFoundError:
        return None
    except OSError as error:
        raise PrivateFileError(f"cannot read {path}: {error.strerror}") from error


def write_private_file(path: Path, text: str) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.unlink(missing_ok=True)
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as file:
        file.write(text)
        file.flush()
        os.fsync(file.fileno())
    os.replace(temporary, path)

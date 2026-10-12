from datetime import datetime


def format_timestamp(value: datetime | None) -> str | None:
    return value.isoformat(timespec="seconds") if value is not None else None


def parse_timestamp(text: str | None) -> datetime | None:
    return datetime.fromisoformat(text) if text is not None else None

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest
from samples import TEXT_POST

NAIVE = datetime(2026, 1, 5, 9, 0)
UTC_PLUS_3 = datetime(2026, 1, 5, 12, 0, tzinfo=timezone(timedelta(hours=3)))


@pytest.mark.parametrize(
    "chat_id",
    [-1000000000001, -42, 1234567890],
    ids=["channel", "basic-group", "private-chat"],
)
def test_accepts_marked_chat_ids(chat_id):
    replace(TEXT_POST, chat_id=chat_id)


def test_rejects_chat_id_zero():
    with pytest.raises(ValueError):
        replace(TEXT_POST, chat_id=0)


@pytest.mark.parametrize("field", ["posted_at", "edited_at"])
@pytest.mark.parametrize("value", [NAIVE, UTC_PLUS_3], ids=["naive", "utc+3"])
def test_rejects_timestamps_not_in_utc(field, value):
    with pytest.raises(ValueError):
        replace(TEXT_POST, **{field: value})

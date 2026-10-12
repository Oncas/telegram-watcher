import asyncio

import pytest

from group_watcher.client import AuthorizationFailed, HaltReason
from group_watcher.credentials import Credentials
from group_watcher.telegram_account import new_client, open_telegram

CREDENTIALS = Credentials(api_id=12345, api_hash="0123456789abcdef0123456789abcdef")


def test_missing_session_halts_before_connecting(tmp_path):
    with pytest.raises(AuthorizationFailed) as raised:
        open_telegram(CREDENTIALS, tmp_path / "telegram.session")
    assert raised.value.reason == HaltReason.SESSION_ENDED


def test_client_asks_for_no_updates_and_describes_no_real_device():
    async def create_client():
        return new_client(CREDENTIALS, "")

    client = asyncio.run(create_client())
    assert client._no_updates
    assert (
        client._init_request.device_model,
        client._init_request.system_version,
        client._init_request.app_version,
    ) == ("Group watcher", "Linux", "0.1.0")

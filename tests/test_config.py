import stat

import pytest

from group_watcher.config import (
    Config,
    ConfigError,
    WatchedChat,
    load_config,
    save_config,
)

VALID_CONFIG = """
[[chats]]
name = "Synthetic channel"
chat_id = -1000000000001
access_hash = 1234567890

[[chats]]
name = "Synthetic basic group"
chat_id = -42

[[chats]]
name = "Synthetic person"
chat_id = 1234567890
access_hash = 9876543210
"""


@pytest.fixture
def write_config(tmp_path):
    def write(text):
        path = tmp_path / "config.toml"
        path.write_text(text)
        return path

    return write


def test_loads_channels_basic_groups_and_private_chats(write_config):
    assert load_config(write_config(VALID_CONFIG)) == Config(
        chats=(
            WatchedChat(
                name="Synthetic channel",
                chat_id=-1000000000001,
                access_hash=1234567890,
            ),
            WatchedChat(
                name="Synthetic basic group",
                chat_id=-42,
                access_hash=None,
            ),
            WatchedChat(
                name="Synthetic person",
                chat_id=1234567890,
                access_hash=9876543210,
            ),
        )
    )


def test_rejects_missing_file(tmp_path):
    with pytest.raises(ConfigError):
        load_config(tmp_path / "config.toml")


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("[[chats]\n", id="invalid-toml"),
        pytest.param("", id="empty"),
        pytest.param("chats = []\n", id="no-chats"),
        pytest.param('chats = "Synthetic channel"\n', id="chats-not-tables"),
        pytest.param(
            """
            poll_interval = 3600
            [[chats]]
            name = "Synthetic basic group"
            chat_id = -42
            """,
            id="unknown-top-level-key",
        ),
        pytest.param(
            """
            [[chats]]
            name = "Synthetic basic group"
            chat_id = -42
            acess_hash = 1234567890
            """,
            id="unknown-chat-key",
        ),
        pytest.param(
            """
            [[chats]]
            chat_id = -42
            """,
            id="missing-name",
        ),
        pytest.param(
            """
            [[chats]]
            name = 42
            chat_id = -42
            """,
            id="numeric-name",
        ),
        pytest.param(
            """
            [[chats]]
            name = "Synthetic basic group"
            """,
            id="missing-chat-id",
        ),
        pytest.param(
            """
            [[chats]]
            name = "Synthetic basic group"
            chat_id = "-42"
            """,
            id="quoted-chat-id",
        ),
        pytest.param(
            """
            [[chats]]
            name = "Synthetic person"
            chat_id = 1234567890
            """,
            id="private-chat-without-access-hash",
        ),
        pytest.param(
            """
            [[chats]]
            name = "Synthetic chat"
            chat_id = 0
            """,
            id="zero-chat-id",
        ),
        pytest.param(
            """
            [[chats]]
            name = "Synthetic channel"
            chat_id = -1000000000001
            """,
            id="channel-without-access-hash",
        ),
        pytest.param(
            """
            [[chats]]
            name = "Synthetic channel"
            chat_id = -1000000000001
            access_hash = "1234567890"
            """,
            id="quoted-access-hash",
        ),
        pytest.param(
            """
            [[chats]]
            name = "Synthetic channel missing its -100 prefix"
            chat_id = -1234567890
            access_hash = 1234567890
            """,
            id="basic-group-with-access-hash",
        ),
        pytest.param(
            """
            [[chats]]
            name = "Synthetic basic group"
            chat_id = -42

            [[chats]]
            name = "Same group again"
            chat_id = -42
            """,
            id="duplicate-chat-id",
        ),
    ],
)
def test_rejects_invalid_config(write_config, text):
    with pytest.raises(ConfigError):
        load_config(write_config(text))


def test_saved_config_loads_back_unchanged(tmp_path):
    config = Config(
        chats=(
            WatchedChat(
                name='Quote " backslash \\ tab \t delete \x7f emoji \U0001f600',
                chat_id=-1000000000001,
                access_hash=-1234567890,
            ),
            WatchedChat(name="Synthetic basic group", chat_id=-42, access_hash=None),
        )
    )
    path = tmp_path / "config.toml"
    save_config(path, config)
    assert load_config(path) == config
    assert stat.S_IMODE(path.stat().st_mode) == 0o600

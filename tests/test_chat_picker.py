import pytest
from samples import SECOND_CHANNEL, T0, WATCHED_CHANNEL
from telethon import types

from telegram_watcher.chat_picker import select_chats, terminal_safe, watched_chats
from telegram_watcher.config import WatchedChat

BASIC_GROUP = WatchedChat(name="Synthetic basic group", chat_id=-42, access_hash=None)
PERSON = WatchedChat(name="Synthetic person", chat_id=7, access_hash=7777)
LISTED = (WATCHED_CHANNEL, SECOND_CHANNEL, BASIC_GROUP)


def channel(**flags):
    return types.Channel(
        id=1,
        title="Synthetic channel",
        photo=types.ChatPhotoEmpty(),
        date=T0,
        access_hash=1111,
        **flags,
    )


@pytest.mark.parametrize("flags", [{"broadcast": True}, {"megagroup": True}])
def test_channels_and_supergroups_keep_their_access_hash(flags):
    assert watched_chats([channel(**flags)]) == [WATCHED_CHANNEL]


def test_basic_group_has_no_access_hash():
    group = types.Chat(
        id=42,
        title="Synthetic basic group",
        photo=types.ChatPhotoEmpty(),
        participants_count=3,
        date=T0,
        version=1,
    )
    assert watched_chats([group]) == [BASIC_GROUP]


@pytest.mark.parametrize("flags", [{}, {"bot": True}])
def test_people_and_bots_keep_their_access_hash(flags):
    person = types.User(
        id=7, access_hash=7777, first_name="Synthetic", last_name="person", **flags
    )
    assert watched_chats([person]) == [PERSON]


def test_banned_removed_and_deleted_chats_are_not_offered():
    banned = types.ChannelForbidden(id=2, access_hash=2222, title="Synthetic ban")
    removed = types.ChatForbidden(id=43, title="Synthetic removal")
    deleted = types.User(id=8, access_hash=8888, deleted=True)
    assert watched_chats([banned, removed, deleted, channel(broadcast=True)]) == [
        WATCHED_CHANNEL
    ]


def test_names_are_made_safe_to_print_in_a_terminal():
    assert (
        terminal_safe("Synthetic\x1b[2J\nname \U0001f600")
        == "Synthetic?[2J?name \U0001f600"
    )


def test_selection_accepts_commas_and_spaces_in_any_order():
    assert select_chats(LISTED, "3, 1 2") == (
        BASIC_GROUP,
        WATCHED_CHANNEL,
        SECOND_CHANNEL,
    )


def test_selection_ignores_repeats():
    assert select_chats(LISTED, "2,2") == (SECOND_CHANNEL,)


@pytest.mark.parametrize("answer", ["", "0", "4", "one", "1-2", "-1"])
def test_selection_outside_the_list_is_refused(answer):
    with pytest.raises(ValueError):
        select_chats(LISTED, answer)

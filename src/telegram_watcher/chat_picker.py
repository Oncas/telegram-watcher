from collections.abc import Iterable, Sequence

from telethon import types, utils

from telegram_watcher.config import WatchedChat


def watched_chats(entities: Iterable[object]) -> list[WatchedChat]:
    return [
        _watched_chat(entity)
        for entity in entities
        if isinstance(entity, (types.Channel, types.Chat))
        or (isinstance(entity, types.User) and not entity.deleted)
    ]


def select_chats(chats: Sequence[WatchedChat], answer: str) -> tuple[WatchedChat, ...]:
    numbers = answer.replace(",", " ").split()
    if not numbers:
        raise ValueError("no chats selected")
    for number in numbers:
        if not (
            number.isascii() and number.isdigit() and 1 <= int(number) <= len(chats)
        ):
            raise ValueError(f"choose numbers from 1 to {len(chats)}")
    return tuple(chats[number - 1] for number in dict.fromkeys(map(int, numbers)))


def terminal_safe(text: str) -> str:
    return "".join(char if char.isprintable() else "?" for char in text)


def _watched_chat(entity: types.Channel | types.Chat | types.User) -> WatchedChat:
    return WatchedChat(
        name=utils.get_display_name(entity),
        chat_id=utils.get_peer_id(entity),
        access_hash=None if isinstance(entity, types.Chat) else entity.access_hash,
    )

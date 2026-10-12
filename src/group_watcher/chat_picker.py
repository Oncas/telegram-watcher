from collections.abc import Iterable, Sequence

from telethon import types, utils

from group_watcher.config import WatchedChat


def watched_chats(entities: Iterable[object]) -> list[WatchedChat]:
    return [
        _watched_chat(entity)
        for entity in entities
        if isinstance(entity, (types.Channel, types.Chat))
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


def _watched_chat(entity: types.Channel | types.Chat) -> WatchedChat:
    return WatchedChat(
        name=entity.title,
        chat_id=utils.get_peer_id(entity),
        access_hash=entity.access_hash if isinstance(entity, types.Channel) else None,
    )

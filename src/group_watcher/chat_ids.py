def is_marked_chat_id(chat_id: int) -> bool:
    return chat_id != 0


def is_private_chat_id(chat_id: int) -> bool:
    return chat_id > 0


def is_basic_group_id(chat_id: int) -> bool:
    return -1_000_000_000_000 <= chat_id < 0


def is_channel_id(chat_id: int) -> bool:
    return chat_id < -1_000_000_000_000

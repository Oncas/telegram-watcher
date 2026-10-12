def is_marked_group_or_channel_id(chat_id: int) -> bool:
    return chat_id < 0


def is_channel_id(chat_id: int) -> bool:
    return chat_id < -1_000_000_000_000

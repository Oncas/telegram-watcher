import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True, slots=True, kw_only=True)
class Match:
    listener: str
    chat_id: int
    message_id: int
    posted_at: datetime
    edited_at: datetime | None
    data: dict[str, Any]

    @property
    def match_id(self) -> str:
        identity = json.dumps(
            [self.listener, self.chat_id, self.message_id, self.data], sort_keys=True
        )
        return hashlib.sha256(identity.encode()).hexdigest()

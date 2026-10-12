import logging
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from group_watcher.chat_poller import ChatPoller
from group_watcher.client import (
    AuthorizationFailed,
    ChatClient,
    ChatUnavailable,
    HaltReason,
    NetworkUnavailable,
    RateLimited,
)
from group_watcher.config import WatchedChat
from group_watcher.storage.account_state_repository import AccountStateRepository

logger = logging.getLogger(__name__)

HALT_NOTICES = {
    HaltReason.SESSION_ENDED: (
        "Telegram watcher halted: the session ended. Log in again, then clear the halt."
    ),
    HaltReason.ACCOUNT_DEACTIVATED: (
        "Telegram watcher halted: the account is deactivated or banned."
        " Logging in again will not help."
    ),
    HaltReason.AUTHORIZATION_REFUSED: (
        "Telegram watcher halted: authorization was refused."
        " Review the reason in the log before clearing the halt."
    ),
}


class RunOutcome(StrEnum):
    COMPLETED = "completed"
    HALTED = "halted"
    RATE_LIMITED = "rate_limited"
    NETWORK_UNAVAILABLE = "network_unavailable"
    FAILED = "failed"


class Notifier(Protocol):
    def notify(self, message: str) -> None: ...


class PollRun:
    def __init__(
        self,
        *,
        chats: Sequence[WatchedChat],
        connect: Callable[[], AbstractContextManager[ChatClient]],
        chat_poller: ChatPoller,
        transaction: AbstractContextManager,
        account_state_repository: AccountStateRepository,
        notifier: Notifier,
        clock: Callable[[], datetime],
    ):
        self._chats = chats
        self._connect = connect
        self._chat_poller = chat_poller
        self._transaction = transaction
        self._account_state = account_state_repository
        self._notifier = notifier
        self._clock = clock

    def run(self) -> RunOutcome:
        blocked = self._blocked_outcome()
        if blocked is not None:
            return blocked
        try:
            with self._connect() as client:
                self._poll_chats(client)
        except NetworkUnavailable:
            logger.warning("run ended: network unavailable")
            return RunOutcome.NETWORK_UNAVAILABLE
        except RateLimited as error:
            retry_at = self._clock() + error.wait
            with self._transaction:
                self._account_state.defer_until(retry_at)
            logger.warning("run ended: rate limited until %s", retry_at.isoformat())
            return RunOutcome.RATE_LIMITED
        except AuthorizationFailed as error:
            with self._transaction:
                self._account_state.halt(error.reason)
            logger.error("run halted: %s", error.reason)
            self._notifier.notify(HALT_NOTICES[error.reason])
            return RunOutcome.HALTED
        except Exception as error:  # noqa: BLE001
            logger.error("run failed: %s", type(error).__name__)
            return RunOutcome.FAILED
        return RunOutcome.COMPLETED

    def _blocked_outcome(self) -> RunOutcome | None:
        halt_reason = self._account_state.halt_reason()
        if halt_reason is not None:
            logger.info("run skipped: halted, %s", halt_reason)
            return RunOutcome.HALTED
        retry_at = self._account_state.retry_at()
        if retry_at is not None and self._clock() < retry_at:
            logger.info("run skipped: rate limited until %s", retry_at.isoformat())
            return RunOutcome.RATE_LIMITED
        return None

    def _poll_chats(self, client: ChatClient) -> None:
        for chat in self._chats:
            try:
                self._chat_poller.poll(client, chat)
            except ChatUnavailable:
                logger.warning("chat unavailable: %s", chat.name)
                self._notifier.notify(f"Telegram watcher could not poll {chat.name}.")

import logging
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

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


class RunOutcome(StrEnum):
    COMPLETED = "completed"
    HALTED = "halted"
    RATE_LIMITED = "rate_limited"
    NETWORK_UNAVAILABLE = "network_unavailable"
    FAILED = "failed"


@dataclass(frozen=True, slots=True, kw_only=True)
class RunReport:
    started_at: datetime
    finished_at: datetime
    outcome: RunOutcome
    error_type: str | None = None
    halt_reason: HaltReason | None = None
    polled_chats: tuple[WatchedChat, ...] = ()
    unavailable_chats: tuple[WatchedChat, ...] = ()


class PollRun:
    def __init__(
        self,
        *,
        chats: Sequence[WatchedChat],
        connect: Callable[[], AbstractContextManager[ChatClient]],
        chat_poller: ChatPoller,
        transaction: AbstractContextManager,
        account_state_repository: AccountStateRepository,
        clock: Callable[[], datetime],
    ):
        self._chats = chats
        self._connect = connect
        self._chat_poller = chat_poller
        self._transaction = transaction
        self._account_state = account_state_repository
        self._clock = clock

    def run(self) -> RunReport:
        started_at = self._clock()
        polled_chats: list[WatchedChat] = []
        unavailable_chats: list[WatchedChat] = []
        outcome, error_type = self._run(polled_chats, unavailable_chats)
        return RunReport(
            started_at=started_at,
            finished_at=self._clock(),
            outcome=outcome,
            error_type=error_type,
            halt_reason=self._account_state.halt_reason(),
            polled_chats=tuple(polled_chats),
            unavailable_chats=tuple(unavailable_chats),
        )

    def _run(
        self,
        polled_chats: list[WatchedChat],
        unavailable_chats: list[WatchedChat],
    ) -> tuple[RunOutcome, str | None]:
        blocked = self._blocked_outcome()
        if blocked is not None:
            return blocked, None
        try:
            with self._connect() as client:
                self._poll_chats(client, polled_chats, unavailable_chats)
        except NetworkUnavailable:
            logger.warning("run ended: network unavailable")
            return RunOutcome.NETWORK_UNAVAILABLE, None
        except RateLimited as error:
            retry_at = self._clock() + error.wait
            with self._transaction:
                self._account_state.defer_until(retry_at)
            logger.warning("run ended: rate limited until %s", retry_at.isoformat())
            return RunOutcome.RATE_LIMITED, None
        except AuthorizationFailed as error:
            with self._transaction:
                self._account_state.halt(error.reason)
            logger.error("run halted: %s", error.reason)
            return RunOutcome.HALTED, None
        except Exception as error:  # noqa: BLE001
            error_type = type(error).__name__
            logger.error("run failed: %s", error_type)
            return RunOutcome.FAILED, error_type
        return RunOutcome.COMPLETED, None

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

    def _poll_chats(
        self,
        client: ChatClient,
        polled_chats: list[WatchedChat],
        unavailable_chats: list[WatchedChat],
    ) -> None:
        for chat in self._chats:
            try:
                self._chat_poller.poll(client, chat)
            except ChatUnavailable:
                logger.warning("chat unavailable: %s", chat.name)
                unavailable_chats.append(chat)
            else:
                polled_chats.append(chat)

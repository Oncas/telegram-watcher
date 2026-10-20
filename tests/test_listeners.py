import re
import shutil
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest
from builders import run_hourly
from fakes import FakeClock, FakeForwarder, FakeNotifier, FakeTelegram
from samples import (
    ALBUM_POSTS,
    CAPTION_POST,
    EDITED_TEXT_POST,
    SECOND_CHANNEL,
    SECOND_CHAT,
    SECOND_CHAT_POST,
    T0,
    TEXT_POST,
    WATCHED_CHANNEL,
    WATCHED_CHAT,
)

from telegram_watcher.client import NetworkUnavailable
from telegram_watcher.listeners import Listener, load_listeners

EXAMPLE = Path(__file__).parents[1] / "listeners" / "example.py"
LISTENER_SOURCE = (
    "CHAT_ID = {chat_id}\n\n\ndef listen(post):\n    yield {{'text': post.text}}\n"
)
FORWARD_FAILED = (
    "Telegram watcher could not forward listener matches: ConnectionRefusedError."
)


def whole_text(post):
    yield {"text": post.text}


def first_word(post):
    yield {"word": post.text.split()[0]}


def broken(post):
    raise ValueError(post.text)


WHOLE_TEXT = Listener(name="whole_text", chat_id=WATCHED_CHAT, listen=whole_text)
FIRST_WORD = Listener(name="first_word", chat_id=WATCHED_CHAT, listen=first_word)
BROKEN = Listener(name="broken", chat_id=WATCHED_CHAT, listen=broken)


def heard(text):
    return f"whole_text\ntext: {text}"


def forwarded_data(forwarder):
    return [payload["data"] for payload in forwarder.payloads]


@pytest.fixture
def telegram():
    return FakeTelegram()


@pytest.fixture
def notifier():
    return FakeNotifier()


@pytest.fixture
def forwarder():
    return FakeForwarder()


@pytest.fixture
def run_with(db_path, telegram, notifier, forwarder, image_store):
    clock = FakeClock(T0 + timedelta(hours=1))

    def run_with(*listeners, forward_with=forwarder):
        run_hourly(
            db_path,
            chats=(WATCHED_CHANNEL, SECOND_CHANNEL),
            telegram=telegram,
            notifier=notifier,
            clock=clock,
            image_store=image_store,
            listeners=listeners,
            forwarder=forward_with,
        )
        clock.now += timedelta(minutes=15)

    return run_with


def test_new_posts_in_the_listeners_chat_are_notified_and_forwarded(
    run_with, telegram, notifier, forwarder
):
    run_with(WHOLE_TEXT)
    telegram.publish(TEXT_POST, CAPTION_POST, SECOND_CHAT_POST)
    run_with(WHOLE_TEXT)
    assert notifier.messages == [
        heard("Synthetic text post"),
        heard("Synthetic caption"),
    ]
    assert forwarded_data(forwarder) == [
        {"text": "Synthetic text post"},
        {"text": "Synthetic caption"},
    ]


def test_forwarded_match_names_its_listener_and_post(run_with, telegram, forwarder):
    run_with(WHOLE_TEXT)
    telegram.publish(TEXT_POST)
    run_with(WHOLE_TEXT)
    [payload] = forwarder.payloads
    assert re.fullmatch(r"[0-9a-f]{64}", payload.pop("id"))
    assert payload == {
        "listener": "whole_text",
        "message_id": 101,
        "posted_at": "2026-01-05T09:00:00+00:00",
        "edited_at": None,
        "data": {"text": "Synthetic text post"},
    }


def test_posts_from_before_the_first_poll_are_not_heard(
    run_with, telegram, notifier, forwarder
):
    telegram.publish(TEXT_POST)
    run_with(WHOLE_TEXT)
    run_with(WHOLE_TEXT)
    assert notifier.messages == []
    assert forwarder.payloads == []


def test_edit_that_changes_a_match_is_heard_again(run_with, telegram, forwarder):
    run_with(WHOLE_TEXT)
    telegram.publish(TEXT_POST)
    run_with(WHOLE_TEXT)
    telegram.publish(EDITED_TEXT_POST)
    run_with(WHOLE_TEXT)
    run_with(WHOLE_TEXT)
    assert [
        (payload["data"], payload["edited_at"]) for payload in forwarder.payloads
    ] == [
        ({"text": "Synthetic text post"}, None),
        ({"text": "Synthetic text post, edited"}, "2026-01-05T09:30:00+00:00"),
    ]


def test_edit_that_leaves_the_match_unchanged_is_not_repeated(
    run_with, telegram, notifier, forwarder
):
    run_with(FIRST_WORD)
    telegram.publish(TEXT_POST)
    run_with(FIRST_WORD)
    telegram.publish(EDITED_TEXT_POST)
    run_with(FIRST_WORD)
    assert notifier.messages == ["first_word\nword: Synthetic"]
    assert forwarded_data(forwarder) == [{"word": "Synthetic"}]


def test_same_match_in_two_posts_is_delivered_for_each(run_with, telegram, forwarder):
    run_with(FIRST_WORD)
    telegram.publish(TEXT_POST, CAPTION_POST)
    run_with(FIRST_WORD)
    assert [payload["message_id"] for payload in forwarder.payloads] == [101, 102]


def test_listener_added_later_does_not_hear_posts_already_recorded(
    run_with, telegram, forwarder
):
    run_with(WHOLE_TEXT)
    telegram.publish(TEXT_POST)
    run_with(WHOLE_TEXT)
    run_with(WHOLE_TEXT, FIRST_WORD)
    assert forwarded_data(forwarder) == [{"text": "Synthetic text post"}]


def test_failing_listener_is_logged_without_post_text_and_others_still_hear(
    run_with, telegram, notifier, caplog
):
    run_with(BROKEN, WHOLE_TEXT)
    telegram.publish(TEXT_POST)
    run_with(BROKEN, WHOLE_TEXT)
    assert notifier.messages == [heard("Synthetic text post")]
    assert "listener failed: broken, ValueError" in caplog.text
    assert TEXT_POST.text not in caplog.text


@pytest.mark.parametrize(
    ("found", "error"),
    [
        ("Synthetic text", "TypeError"),
        ({"when": T0}, "TypeError"),
        ({"ratio": float("nan")}, "ValueError"),
    ],
)
def test_listener_must_yield_json_objects(
    run_with, telegram, notifier, forwarder, caplog, found, error
):
    listener = Listener(name="odd", chat_id=WATCHED_CHAT, listen=lambda post: [found])
    run_with(listener)
    telegram.publish(TEXT_POST)
    run_with(listener)
    assert notifier.messages == []
    assert forwarder.payloads == []
    assert f"listener failed: odd, {error}" in caplog.text


def test_mixed_match_keys_are_forwarded_as_text_once(run_with, telegram, forwarder):
    listener = Listener(
        name="numbered",
        chat_id=WATCHED_CHAT,
        listen=lambda post: [{1: (2, 3), "four": "4"}],
    )
    run_with(listener)
    telegram.publish(TEXT_POST)
    run_with(listener)
    run_with(listener)
    assert forwarded_data(forwarder) == [{"1": [2, 3], "four": "4"}]


def test_edit_that_reorders_the_same_fields_is_not_repeated(
    run_with, telegram, forwarder
):
    def fields(post):
        pairs = [("first", "1"), ("second", "2")]
        yield dict(reversed(pairs) if post.edited_at else pairs)

    listener = Listener(name="fields", chat_id=WATCHED_CHAT, listen=fields)
    run_with(listener)
    telegram.publish(TEXT_POST)
    run_with(listener)
    telegram.publish(EDITED_TEXT_POST)
    run_with(listener)
    assert len(forwarder.payloads) == 1


def test_undelivered_notification_is_sent_on_a_later_run_and_forwarded_once(
    run_with, telegram, notifier, forwarder
):
    run_with(WHOLE_TEXT)
    telegram.publish(TEXT_POST)
    notifier.available = False
    run_with(WHOLE_TEXT)
    notifier.available = True
    run_with(WHOLE_TEXT)
    run_with(WHOLE_TEXT)
    assert notifier.messages == [heard("Synthetic text post")]
    assert len(forwarder.payloads) == 1


def test_failed_forwards_are_retried_in_order_and_alert_once(
    run_with, telegram, notifier, forwarder
):
    run_with(WHOLE_TEXT)
    forwarder.failure = "ConnectionRefusedError"
    telegram.publish(TEXT_POST)
    run_with(WHOLE_TEXT)
    telegram.publish(CAPTION_POST, *ALBUM_POSTS)
    run_with(WHOLE_TEXT)
    forwarder.failure = None
    run_with(WHOLE_TEXT)
    run_with(WHOLE_TEXT)
    assert forwarded_data(forwarder) == [
        {"text": "Synthetic text post"},
        {"text": "Synthetic caption"},
        {"text": "Synthetic album caption"},
        {"text": None},
        {"text": None},
    ]
    assert forwarder.attempts == 7
    assert notifier.messages == [
        FORWARD_FAILED,
        heard("Synthetic text post"),
        heard("Synthetic caption"),
        heard("Synthetic album caption"),
        heard(None),
        heard(None),
    ]


def test_forwarding_alerts_again_after_it_recovered(
    run_with, telegram, notifier, forwarder
):
    run_with(WHOLE_TEXT)
    forwarder.failure = "ConnectionRefusedError"
    telegram.publish(TEXT_POST)
    run_with(WHOLE_TEXT)
    forwarder.failure = None
    run_with(WHOLE_TEXT)
    forwarder.failure = "ConnectionRefusedError"
    telegram.publish(CAPTION_POST)
    run_with(WHOLE_TEXT)
    assert notifier.messages.count(FORWARD_FAILED) == 2


def test_pending_matches_are_forwarded_while_telegram_is_unreachable(
    run_with, telegram, forwarder
):
    run_with(WHOLE_TEXT)
    telegram.publish(TEXT_POST)
    forwarder.failure = "ConnectionRefusedError"
    run_with(WHOLE_TEXT)
    forwarder.failure = None
    telegram.connect_failure = NetworkUnavailable()
    run_with(WHOLE_TEXT)
    assert forwarded_data(forwarder) == [{"text": "Synthetic text post"}]


def test_matches_are_delivered_when_a_later_chat_fails(run_with, telegram, forwarder):
    run_with(WHOLE_TEXT)
    telegram.publish(TEXT_POST)
    telegram.chat_failures[SECOND_CHAT] = RuntimeError()
    run_with(WHOLE_TEXT)
    assert forwarded_data(forwarder) == [{"text": "Synthetic text post"}]


def test_without_forwarding_matches_are_only_notified(run_with, telegram, notifier):
    run_with(WHOLE_TEXT, forward_with=None)
    telegram.publish(TEXT_POST)
    run_with(WHOLE_TEXT, forward_with=None)
    assert notifier.messages == [heard("Synthetic text post")]


def test_python_files_load_as_listeners_except_the_example(tmp_path):
    (tmp_path / "signals.py").write_text(LISTENER_SOURCE.format(chat_id=WATCHED_CHAT))
    (tmp_path / "example.py").write_text(LISTENER_SOURCE.format(chat_id=SECOND_CHAT))
    (tmp_path / "notes.txt").write_text("Synthetic notes")
    [listener] = load_listeners(tmp_path)
    assert (listener.name, listener.chat_id) == ("signals", WATCHED_CHAT)
    assert list(listener.listen(TEXT_POST)) == [{"text": TEXT_POST.text}]


def test_listener_that_fails_to_load_is_logged_and_skipped(tmp_path, caplog):
    (tmp_path / "no_chat_id.py").write_text("def listen(post):\n    return []\n")
    (tmp_path / "raises.py").write_text("raise RuntimeError\n")
    (tmp_path / "signals.py").write_text(LISTENER_SOURCE.format(chat_id=WATCHED_CHAT))
    assert [listener.name for listener in load_listeners(tmp_path)] == ["signals"]
    assert "listener not loaded: no_chat_id, AttributeError" in caplog.text
    assert "listener not loaded: raises, RuntimeError" in caplog.text


def test_missing_directory_has_no_listeners(tmp_path):
    assert load_listeners(tmp_path / "listeners") == ()


def test_a_copy_of_the_example_is_a_working_listener(tmp_path):
    shutil.copy(EXAMPLE, tmp_path / "signals.py")
    [listener] = load_listeners(tmp_path)
    post = replace(TEXT_POST, text="Synthetic signal: BUY ABC at 1.25")
    assert list(listener.listen(post)) == [
        {"side": "BUY", "symbol": "ABC", "price": "1.25"}
    ]

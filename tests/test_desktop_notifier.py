import pytest

from group_watcher.desktop_notifier import DesktopNotifier
from group_watcher.notifier import NotificationFailed


def test_message_is_shown_as_a_desktop_notification(notify_send):
    DesktopNotifier().notify("Synthetic alert")
    assert notify_send.calls == [
        ["--app-name=Telegram watcher", "Telegram watcher", "Synthetic alert"]
    ]


def test_rejected_notification_is_reported_as_not_delivered(notify_send):
    notify_send.answer_with(1)
    with pytest.raises(NotificationFailed):
        DesktopNotifier().notify("Synthetic alert")


def test_missing_notify_send_is_reported_as_not_delivered(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))
    with pytest.raises(NotificationFailed):
        DesktopNotifier().notify("Synthetic alert")

import subprocess

from telegram_watcher.notifier import NotificationFailed

APP_NAME = "Telegram watcher"


class DesktopNotifier:
    def notify(self, message: str) -> None:
        try:
            subprocess.run(
                ["notify-send", f"--app-name={APP_NAME}", APP_NAME, message],
                check=True,
                capture_output=True,
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise NotificationFailed(type(error).__name__) from error

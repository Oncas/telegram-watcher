import http.client
import json
import urllib.error
import urllib.request
from collections.abc import Mapping
from typing import Any

from group_watcher.forward_target import ForwardTarget
from group_watcher.forwarder import ForwardFailed

TIMEOUT_SECONDS = 10


class _RefuseRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *arguments: Any) -> None:
        return None


class HttpForwarder:
    def __init__(self, target: ForwardTarget):
        self._target = target
        self._opener = urllib.request.build_opener(_RefuseRedirects)

    def forward(self, payload: Mapping[str, Any]) -> None:
        request = urllib.request.Request(
            self._target.url,
            data=json.dumps(payload).encode(),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._target.token}",
            },
            method="POST",
        )
        try:
            with self._opener.open(request, timeout=TIMEOUT_SECONDS):
                pass
        except urllib.error.HTTPError as error:
            error.close()
            raise ForwardFailed(f"HTTP {error.code}") from error
        except urllib.error.URLError as error:
            raise ForwardFailed(_failure_name(error.reason)) from error
        except (OSError, http.client.HTTPException) as error:
            raise ForwardFailed(type(error).__name__) from error


def _failure_name(reason: object) -> str:
    return type(reason).__name__ if isinstance(reason, Exception) else "URLError"

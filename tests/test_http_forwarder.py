import json
import socket

import pytest

from group_watcher.forward_target import ForwardTarget
from group_watcher.forwarder import ForwardFailed
from group_watcher.http_forwarder import HttpForwarder

TOKEN = "synthetic-token"
PAYLOAD = {"id": "synthetic", "data": {"text": "Synthetic caption \u00fc"}}


def forwarder_to(url):
    return HttpForwarder(ForwardTarget(url=url, token=TOKEN))


def test_payload_is_posted_as_json_with_the_token(endpoint):
    forwarder_to(endpoint.url).forward(PAYLOAD)
    [(method, path, headers, body)] = endpoint.requests
    assert (method, path) == ("POST", "/matches")
    assert headers["Content-Type"] == "application/json"
    assert headers["Authorization"] == f"Bearer {TOKEN}"
    assert json.loads(body) == PAYLOAD


@pytest.mark.parametrize("status", [401, 500])
def test_error_status_fails_the_forward(endpoint, status):
    endpoint.status = status
    with pytest.raises(ForwardFailed, match=f"HTTP {status}"):
        forwarder_to(endpoint.url).forward(PAYLOAD)


@pytest.mark.parametrize("status", [302, 303])
def test_redirect_is_not_followed(endpoint, status):
    endpoint.status = status
    with pytest.raises(ForwardFailed, match=f"HTTP {status}"):
        forwarder_to(endpoint.url).forward(PAYLOAD)
    assert [path for _, path, _, _ in endpoint.requests] == ["/matches"]


def test_unreachable_endpoint_fails_the_forward():
    with socket.socket() as unused:
        unused.bind(("127.0.0.1", 0))
        port = unused.getsockname()[1]
    with pytest.raises(ForwardFailed, match="ConnectionRefusedError"):
        forwarder_to(f"http://127.0.0.1:{port}/matches").forward(PAYLOAD)


def test_endpoint_that_never_answers_times_out(monkeypatch):
    monkeypatch.setattr("group_watcher.http_forwarder.TIMEOUT_SECONDS", 0.05)
    with socket.socket() as silent:
        silent.bind(("127.0.0.1", 0))
        silent.listen()
        port = silent.getsockname()[1]
        with pytest.raises(ForwardFailed, match="TimeoutError"):
            forwarder_to(f"http://127.0.0.1:{port}/matches").forward(PAYLOAD)

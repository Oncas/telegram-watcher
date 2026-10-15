import pytest

from group_watcher.config import ConfigError
from group_watcher.forward_target import ForwardTarget, load_forward_target
from group_watcher.private_files import write_private_file

TOKEN = "synthetic-token_0123456789"


@pytest.fixture
def path(tmp_path):
    return tmp_path / "forward.toml"


def test_missing_file_turns_forwarding_off(path):
    assert load_forward_target(path) is None


def test_url_and_token_are_loaded(path):
    write_private_file(
        path, f'url = "https://127.0.0.1:8443/matches"\ntoken = "{TOKEN}"\n'
    )
    assert load_forward_target(path) == ForwardTarget(
        url="https://127.0.0.1:8443/matches", token=TOKEN
    )


def test_token_is_kept_out_of_repr():
    assert TOKEN not in repr(ForwardTarget(url="http://127.0.0.1/", token=TOKEN))


def test_file_readable_by_others_is_refused(path):
    write_private_file(path, f'url = "http://127.0.0.1/"\ntoken = "{TOKEN}"\n')
    path.chmod(0o640)
    with pytest.raises(ConfigError, match="chmod 600"):
        load_forward_target(path)


@pytest.mark.parametrize(
    "text",
    [
        f'url = "http://127.0.0.1/"\ntoken = "{TOKEN}',
        'url = "http://127.0.0.1/"\n',
        f'url = "http://127.0.0.1/"\ntoken = "{TOKEN}"\nretries = "3"\n',
        f'url = "http://127.0.0.1/"\ntoken = ["{TOKEN}"]\n',
        f'url = "ftp://127.0.0.1/"\ntoken = "{TOKEN}"\n',
        f'url = "http:///matches"\ntoken = "{TOKEN}"\n',
        f'url = "http://127.0.0.1:port/"\ntoken = "{TOKEN}"\n',
        f'url = "http://[::1/"\ntoken = "{TOKEN}"\n',
        'url = "http://127.0.0.1/"\ntoken = ""\n',
        f'url = "http://127.0.0.1/"\ntoken = "{TOKEN}\\r\\nX-Injected: 1"\n',
    ],
)
def test_malformed_file_is_refused_without_echoing_the_token(path, text):
    write_private_file(path, text)
    with pytest.raises(ConfigError) as raised:
        load_forward_target(path)
    assert TOKEN not in str(raised.value)

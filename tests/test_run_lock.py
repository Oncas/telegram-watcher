import stat

import pytest

from group_watcher.run_lock import AlreadyRunning, run_lock


def test_second_run_is_refused_while_the_first_holds_the_lock(tmp_path):
    path = tmp_path / "watcher.lock"
    with run_lock(path), pytest.raises(AlreadyRunning), run_lock(path):
        pass


def test_lock_is_released_when_the_run_ends(tmp_path):
    path = tmp_path / "watcher.lock"
    with run_lock(path):
        pass
    with run_lock(path):
        pass


def test_lock_file_is_owner_only(tmp_path):
    path = tmp_path / "watcher.lock"
    with run_lock(path):
        assert stat.S_IMODE(path.stat().st_mode) == 0o600

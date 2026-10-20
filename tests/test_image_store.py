import os
import stat

import pytest
from samples import WATCHED_CHAT

from telegram_watcher.client import Image
from telegram_watcher.image_store import ImageStore

IMAGE = Image(data=b"synthetic image", extension=".jpg")


@pytest.fixture
def root(tmp_path):
    return tmp_path / "media"


@pytest.fixture
def store(root):
    return ImageStore(root)


def test_image_is_saved_under_its_chat_and_message(root, store):
    store.add(WATCHED_CHAT, 103, IMAGE)
    saved = root / "downloaded" / f"{WATCHED_CHAT}_103.jpg"
    assert saved.read_bytes() == IMAGE.data
    assert store.contains(WATCHED_CHAT, 103)


def test_every_folder_is_created_when_the_first_image_is_saved(root, store):
    store.add(WATCHED_CHAT, 103, IMAGE)
    assert sorted(entry.name for entry in root.iterdir()) == [
        "archived",
        "downloaded",
        "rejected",
    ]
    assert not list((root / "archived").iterdir())
    assert not list((root / "rejected").iterdir())


def test_nothing_is_created_before_an_image_is_saved(root, store):
    store.clear_unfinished()
    assert not store.contains(WATCHED_CHAT, 103)
    assert not root.exists()


def test_files_are_owner_only(root, store):
    store.add(WATCHED_CHAT, 103, IMAGE)
    assert (
        stat.S_IMODE((root / "downloaded" / f"{WATCHED_CHAT}_103.jpg").stat().st_mode)
        == 0o600
    )
    for directory in [root, root / "downloaded", root / "rejected", root / "archived"]:
        assert stat.S_IMODE(directory.stat().st_mode) == 0o700


def test_no_temporary_file_is_left_after_saving(root, store):
    store.add(WATCHED_CHAT, 103, IMAGE)
    assert [entry.name for entry in root.iterdir() if entry.is_file()] == []
    assert len(list((root / "downloaded").iterdir())) == 1


@pytest.mark.parametrize("folder", ["downloaded", "rejected", "archived"])
def test_image_in_any_folder_counts_as_stored(root, store, folder):
    store.add(WATCHED_CHAT, 1, IMAGE)
    (root / "downloaded" / f"{WATCHED_CHAT}_1.jpg").rename(
        root / folder / f"{WATCHED_CHAT}_1.jpg"
    )
    assert store.contains(WATCHED_CHAT, 1)


def test_other_messages_and_chats_are_not_mistaken_for_a_stored_image(root, store):
    store.add(WATCHED_CHAT, 10, IMAGE)
    assert not store.contains(WATCHED_CHAT, 1)
    assert not store.contains(WATCHED_CHAT, 100)
    assert not store.contains(WATCHED_CHAT + 1, 10)


def test_image_without_an_extension_is_still_found(store):
    store.add(WATCHED_CHAT, 103, Image(data=b"synthetic", extension=""))
    assert store.contains(WATCHED_CHAT, 103)


def test_unfinished_files_are_removed_and_finished_images_are_kept(root, store):
    store.add(WATCHED_CHAT, 103, IMAGE)
    (root / f".{WATCHED_CHAT}_104.jpg.part").write_bytes(b"partial")
    store.clear_unfinished()
    assert [entry.name for entry in root.iterdir() if entry.is_file()] == []
    assert store.contains(WATCHED_CHAT, 103)


def test_stale_temporary_file_does_not_block_saving_the_same_image(root, store):
    root.mkdir()
    (root / f".{WATCHED_CHAT}_103.jpg.part").write_bytes(b"partial")
    store.add(WATCHED_CHAT, 103, IMAGE)
    assert (root / "downloaded" / f"{WATCHED_CHAT}_103.jpg").read_bytes() == IMAGE.data
    assert [entry.name for entry in root.iterdir() if entry.is_file()] == []


def test_directory_is_synced_after_the_image_is_moved_in(root, store, monkeypatch):
    synced = []
    real_fsync = os.fsync

    def recording_fsync(descriptor):
        synced.append(os.fstat(descriptor).st_ino)
        real_fsync(descriptor)

    monkeypatch.setattr(os, "fsync", recording_fsync)
    store.add(WATCHED_CHAT, 103, IMAGE)
    assert synced[-1] == (root / "downloaded").stat().st_ino


def test_failed_write_leaves_no_partial_file_in_downloaded(root, store, monkeypatch):
    def fail(descriptor):
        raise OSError

    monkeypatch.setattr(os, "fsync", fail)
    with pytest.raises(OSError):
        store.add(WATCHED_CHAT, 103, IMAGE)
    assert list((root / "downloaded").iterdir()) == []
    assert [entry.name for entry in root.iterdir() if entry.is_file()] == [
        f".{WATCHED_CHAT}_103.jpg.part"
    ]
    assert not store.contains(WATCHED_CHAT, 103)

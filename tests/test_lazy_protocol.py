"""Test of the Lazy File Protocol."""
import os
import tempfile
from queue import Queue
from typing import Optional

import pytest  # type: ignore

from prusa.connect.printer import const
from prusa.connect.printer.files import Filesystem, InvalidStorageError
from prusa.connect.printer.models import Event

# pylint: disable=missing-function-docstring
# pylint: disable=redefined-outer-name


@pytest.fixture
def queue():
    yield Queue()


@pytest.fixture
def storage_dir():
    with tempfile.TemporaryDirectory() as tmp_dir:
        yield tmp_dir


@pytest.fixture
def fs(queue, storage_dir):

    def event_cb(event: const.Event,
                 source: const.Source,
                 timestamp: Optional[float] = None,
                 command_id: Optional[int] = None,
                 **kwargs) -> None:
        queue.put(Event(event, source, timestamp, command_id, **kwargs))

    filesystem = Filesystem(event_cb=event_cb)
    filesystem.from_dir(storage_dir,
                        "PrusaLink gcodes",
                        const.StorageType.LOCAL,
                        system_space=1024)
    return filesystem


class TestStorages:
    """INFO and MEDIUM_* carry storages, never files"""

    def test_storages(self, fs):
        storages = fs.storages()
        assert len(storages) == 1
        storage = storages[0]
        assert storage["mountpoint"] == "/PrusaLink gcodes"
        assert storage["name"] == "PrusaLink gcodes"
        assert storage["type"] == "LOCAL"
        assert storage["is_sfn"] is False
        assert storage["read_only"] is False
        assert storage["system_space"] == 1024
        assert (storage["free_space"] + storage["system_space"]
                <= storage["total_space"])
        assert "files" not in storage
        assert "children" not in storage

    def test_mountpoints_are_top_level(self, fs):
        for storage in fs.storages():
            assert storage["mountpoint"].count("/") == 1

    def test_system_space_is_optional(self, storage_dir):
        fs = Filesystem()
        fs.from_dir(storage_dir, "usb", const.StorageType.USB)
        storage = fs.storages()[0]
        assert storage["type"] == "USB"
        assert "system_space" not in storage

    def test_root_storage_is_not_supported(self, storage_dir):
        fs = Filesystem()
        with pytest.raises(InvalidStorageError):
            fs.from_dir(storage_dir, "/")

    def test_nested_mountpoint_is_not_supported(self, storage_dir):
        fs = Filesystem()
        with pytest.raises(InvalidStorageError):
            fs.from_dir(storage_dir, "usb/nested")

    def test_medium_inserted(self, fs, queue):
        event = queue.get_nowait()
        assert event.event == const.Event.MEDIUM_INSERTED
        assert event.data == fs.storages()[0]

    def test_medium_ejected(self, fs, queue):
        queue.get_nowait()  # MEDIUM_INSERTED
        fs.detach("PrusaLink gcodes")
        event = queue.get_nowait()
        assert event.event == const.Event.MEDIUM_EJECTED
        assert event.data == {"mountpoint": "/PrusaLink gcodes"}
        assert fs.storages() == []

    def test_no_storage_dir(self):
        fs = Filesystem()
        assert fs.storages() == []

    def test_storage_dir_removed(self, fs, storage_dir):
        os.rmdir(storage_dir)
        storage = fs.storages()[0]
        assert "free_space" not in storage
        os.mkdir(storage_dir)  # let the fixture clean up

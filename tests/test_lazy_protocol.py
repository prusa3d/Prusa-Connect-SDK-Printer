"""Test of the Lazy File Protocol."""
import os
import tempfile
from queue import Queue
from typing import Optional
from unittest.mock import patch

import pytest  # type: ignore

from prusa.connect.printer import const
from prusa.connect.printer.command import CommandRejected
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


class TestFileInfo:
    """SEND_FILE_INFO answers from the live filesystem"""

    @staticmethod
    def _make(storage_dir):
        os.mkdir(os.path.join(storage_dir, "models"))
        os.mkdir(os.path.join(storage_dir, "empty"))
        os.mkdir(os.path.join(storage_dir, ".hidden"))
        with open(os.path.join(storage_dir, "models", "a.gcode"), "w") as f:
            f.write("; temperature = 250\n")
        with open(os.path.join(storage_dir, "models", "b.txt"), "w") as f:
            f.write("text")
        with open(os.path.join(storage_dir, "models", "fw.hex"), "w") as f:
            f.write("fw")
        open(os.path.join(storage_dir, "models", ".a.gcode.cache"),
             "w").close()
        os.mkdir(os.path.join(storage_dir, "models", "sub"))

    def test_empty_folder(self, fs):
        os.mkdir(
            os.path.join(fs.storage_dict["PrusaLink gcodes"].path_storage,
                         "empty"))
        info = fs.file_info("/PrusaLink gcodes/empty")
        assert info["type"] == "FOLDER"
        assert info["path"] == "/PrusaLink gcodes/empty"
        assert info["children"] == []
        assert "m_timestamp" in info
        assert "read_only" in info

    def test_storage_root(self, fs, storage_dir):
        self._make(storage_dir)
        info = fs.file_info("/PrusaLink gcodes")
        names = sorted(child["name"] for child in info["children"])
        assert names == ["empty", "models"]  # hidden are skipped

    def test_only_direct_children(self, fs, storage_dir):
        self._make(storage_dir)
        info = fs.file_info("/PrusaLink gcodes/models")
        children = {child["name"]: child for child in info["children"]}
        assert sorted(children) == ["a.gcode", "b.txt", "fw.hex", "sub"]
        assert children["a.gcode"]["type"] == "PRINT_FILE"
        assert children["a.gcode"]["size"] == 20
        assert children["b.txt"]["type"] == "FILE"
        assert children["fw.hex"]["type"] == "FIRMWARE"
        assert children["sub"]["type"] == "FOLDER"
        assert "children" not in children["sub"]
        assert "size" not in children["sub"]
        for child in children.values():
            assert isinstance(child["m_timestamp"], int)
            assert child["read_only"] is False

    def test_fresh_data(self, fs, storage_dir):
        """Changes on the disk are visible without any cache refresh"""
        assert fs.file_info("/PrusaLink gcodes")["children"] == []
        open(os.path.join(storage_dir, "new.gcode"), "w").close()
        children = fs.file_info("/PrusaLink gcodes")["children"]
        assert [child["name"] for child in children] == ["new.gcode"]

    def test_file(self, fs, storage_dir):
        self._make(storage_dir)
        info = fs.file_info("/PrusaLink gcodes/models/a.gcode")
        assert info["type"] == "PRINT_FILE"
        assert info["path"] == "/PrusaLink gcodes/models/a.gcode"
        assert info["size"] == 20
        assert info["temperature"] == 250
        assert "m_timestamp" in info
        assert "display_name" not in info

    def test_path_is_normalized(self, fs, storage_dir):
        self._make(storage_dir)
        info = fs.file_info("//PrusaLink gcodes//models/")
        assert info["path"] == "/PrusaLink gcodes/models"

    @pytest.mark.parametrize("path", [
        "/PrusaLink gcodes/missing",
        "/PrusaLink gcodes/missing/deeper",
        "/other/models",
        "/",
        "",
        "/PrusaLink gcodes/.hidden",
    ])
    def test_not_found(self, fs, storage_dir, path):
        self._make(storage_dir)
        with pytest.raises(CommandRejected) as err:
            fs.file_info(path)
        assert err.value.reason == "File not found"

    @pytest.mark.parametrize("path", [
        "/PrusaLink gcodes/../etc",
        "/PrusaLink gcodes/models/../../",
        "/..",
    ])
    def test_forbidden_path(self, fs, path):
        with pytest.raises(CommandRejected) as err:
            fs.file_info(path)
        assert err.value.reason == "Forbidden path"

    def test_symlink_outside_storage(self, fs, storage_dir):
        with tempfile.TemporaryDirectory() as outside:
            os.symlink(outside, os.path.join(storage_dir, "link"))
            with pytest.raises(CommandRejected) as err:
                fs.file_info("/PrusaLink gcodes/link")
            assert err.value.reason == "Forbidden path"
            # and it is not offered in the listing
            assert fs.file_info("/PrusaLink gcodes")["children"] == []

    def test_symlink_inside_storage(self, fs, storage_dir):
        self._make(storage_dir)
        os.symlink(os.path.join(storage_dir, "models"),
                   os.path.join(storage_dir, "link"))
        children = fs.file_info("/PrusaLink gcodes")["children"]
        link = next(c for c in children if c["name"] == "link")
        assert link["type"] == "FOLDER"

    def test_broken_symlink_is_skipped(self, fs, storage_dir):
        os.symlink(os.path.join(storage_dir, "nowhere"),
                   os.path.join(storage_dir, "broken"))
        assert fs.file_info("/PrusaLink gcodes")["children"] == []

    def test_oversized(self, fs, storage_dir):
        for i in range(5):
            open(os.path.join(storage_dir, f"{i}.gcode"), "w").close()
        with patch("prusa.connect.printer.fileinfo.MAX_FOLDER_ITEMS", 4):
            with pytest.raises(CommandRejected) as err:
                fs.file_info("/PrusaLink gcodes")
        assert err.value.reason == "Oversized command"

    def test_at_the_limit(self, fs, storage_dir):
        for i in range(4):
            open(os.path.join(storage_dir, f"{i}.gcode"), "w").close()
        with patch("prusa.connect.printer.fileinfo.MAX_FOLDER_ITEMS", 4):
            assert len(fs.file_info("/PrusaLink gcodes")["children"]) == 4

    def test_idempotent(self, fs, storage_dir, queue):
        self._make(storage_dir)
        queue.get_nowait()  # MEDIUM_INSERTED
        first = fs.file_info("/PrusaLink gcodes/models")
        assert fs.file_info("/PrusaLink gcodes/models") == first
        assert queue.empty()

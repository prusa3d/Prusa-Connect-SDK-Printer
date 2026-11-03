"""Test of the Lazy File Protocol."""
import os
import tempfile
from queue import Queue
from types import SimpleNamespace
from typing import Optional
from unittest.mock import patch

import pytest  # type: ignore

from prusa.connect.printer import const
from prusa.connect.printer.command import CommandRejected
from prusa.connect.printer.files import (
    RESCAN_THRESHOLD,
    File,
    Filesystem,
    InotifyHandler,
    InvalidStorageError,
)
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


def drain(queue):
    events = []
    while not queue.empty():
        events.append(queue.get_nowait())
    return events


@pytest.fixture
def handler(fs, queue):
    """Inotify handler of the storage, the MEDIUM_INSERTED is consumed"""
    queue.get_nowait()
    return InotifyHandler(fs)


class TestFileChanged:
    """FILE_CHANGED carries what Connect needs to keep its cache fresh"""

    def test_created_file(self, handler, queue, storage_dir):
        open(os.path.join(storage_dir, "a.gcode"), "w").close()
        handler()
        event, = drain(queue)
        assert event.event == const.Event.FILE_CHANGED
        assert event.data["new_path"] == "/PrusaLink gcodes/a.gcode"
        assert event.data["old_path"] is None
        assert event.data["file"]["type"] == "PRINT_FILE"
        assert "rescan" not in event.data

    def test_created_folder_has_no_children(self, handler, queue, storage_dir):
        os.mkdir(os.path.join(storage_dir, "folder"))
        handler()
        event = drain(queue)[0]
        assert event.data["new_path"] == "/PrusaLink gcodes/folder"
        assert event.data["file"]["type"] == "FOLDER"
        assert "children" not in event.data["file"]

    def test_space_info(self, handler, queue, storage_dir):
        open(os.path.join(storage_dir, "a.gcode"), "w").close()
        handler()
        event, = drain(queue)
        assert event.data["free_space"] > 0
        assert event.data["total_space"] > 0
        assert event.data["system_space"] == 1024

    def test_deleted_file(self, handler, queue, storage_dir):
        path = os.path.join(storage_dir, "a.gcode")
        open(path, "w").close()
        handler()
        drain(queue)
        os.unlink(path)
        handler()
        event, = drain(queue)
        assert event.data["old_path"] == "/PrusaLink gcodes/a.gcode"
        assert event.data["new_path"] is None
        assert "file" not in event.data

    def test_renamed_file(self, handler, queue, storage_dir):
        path = os.path.join(storage_dir, "a.gcode")
        open(path, "w").close()
        handler()
        drain(queue)
        os.rename(path, os.path.join(storage_dir, "b.gcode"))
        handler()
        events = drain(queue)
        paths = {(e.data["old_path"], e.data["new_path"]) for e in events}
        assert ("/PrusaLink gcodes/a.gcode", None) in paths
        assert (None, "/PrusaLink gcodes/b.gcode") in paths

    def test_burst_is_one_rescan(self, handler, queue, storage_dir):
        folder = os.path.join(storage_dir, "bulk")
        os.mkdir(folder)
        handler()
        drain(queue)
        for i in range(RESCAN_THRESHOLD + 5):
            open(os.path.join(folder, f"{i}.gcode"), "w").close()
        handler()
        event, = drain(queue)
        assert event.event == const.Event.FILE_CHANGED
        assert event.data["new_path"] == "/PrusaLink gcodes/bulk"
        assert event.data["rescan"] is True
        assert event.data["free_space"] > 0

    def test_burst_in_more_folders(self, handler, queue, storage_dir):
        for name in ("one", "two"):
            os.mkdir(os.path.join(storage_dir, name))
        handler()
        drain(queue)
        for i in range(RESCAN_THRESHOLD):
            for name in ("one", "two"):
                open(os.path.join(storage_dir, name, f"{i}.gcode"),
                     "w").close()
        handler()
        event, = drain(queue)
        assert event.data["new_path"] == "/PrusaLink gcodes"
        assert event.data["rescan"] is True

    def test_burst_in_more_storages(self, handler, queue, storage_dir):
        """One rescan for each storage, never for the root"""
        with tempfile.TemporaryDirectory() as other_dir:
            handler.fs.from_dir(other_dir, "usb", const.StorageType.USB)
            handler = InotifyHandler(handler.fs)
            drain(queue)
            for i in range(RESCAN_THRESHOLD):
                for dir_ in (storage_dir, other_dir):
                    open(os.path.join(dir_, f"{i}.gcode"), "w").close()
            handler()
            events = drain(queue)
        assert {(e.data["new_path"], e.data["rescan"])
                for e in events} == {
                    ("/PrusaLink gcodes", True),
                    ("/usb", True),
                }

    def test_small_change_is_not_rescan(self, handler, queue, storage_dir):
        for i in range(5):
            open(os.path.join(storage_dir, f"{i}.gcode"), "w").close()
        handler()
        events = drain(queue)
        assert len(events) == 5
        assert all("rescan" not in event.data for event in events)


class TestInfo:
    """The INFO event of the printer"""

    def test_info_has_storages_only(self, printer, storage_dir):
        printer.attach(storage_dir, "usb", const.StorageType.USB)
        info = printer.get_info()
        assert "files" not in info
        assert [s["mountpoint"] for s in info["storages"]] == ["/usb"]
        assert info["storages"][0]["type"] == "USB"

    def test_info_without_storage(self, printer):
        assert printer.get_info()["storages"] == []

    def test_info_does_not_walk_the_disk(self, printer, storage_dir):
        printer.attach(storage_dir, "usb")
        with patch("prusa.connect.printer.files.walk") as walk, \
                patch("os.walk") as os_walk, \
                patch("os.scandir") as scandir:
            printer.get_info()
        assert not walk.called
        assert not os_walk.called
        assert not scandir.called

    def test_no_legacy_events(self, printer, storage_dir):
        """No event contains the old protocol keys"""
        printer.attach(storage_dir, "usb")
        printer.detach("usb")
        events = drain(printer.queue)
        assert {e.event
                for e in events
                } >= {const.Event.MEDIUM_INSERTED, const.Event.MEDIUM_EJECTED}
        for event in events:
            assert "files" not in event.data
            assert "root" not in event.data

    def test_send_file_info_command(self, printer, storage_dir):
        os.mkdir(os.path.join(storage_dir, "folder"))
        printer.attach(storage_dir, "usb")

        caller = SimpleNamespace(kwargs={"path": "/usb"})
        info = printer.get_file_info(caller)
        assert info["event"] == const.Event.FILE_INFO
        assert info["type"] == "FOLDER"
        assert info["children"][0]["name"] == "folder"


class TestTreeStorage:
    """A storage without the path on the disk is answered from its tree"""

    @pytest.fixture
    def sd(self, queue):
        filesystem = Filesystem(event_cb=lambda event, source, **kw: queue.put(
            Event(event, source, **kw)))
        tree = File("SD Card", is_dir=True)
        folder = tree.add("folder", is_dir=True, m_timestamp=10)
        folder.add("a.gcode", size=100, m_timestamp=11, read_only=True)
        folder.add("b.txt", size=5)
        folder.add("sub", is_dir=True)
        tree.add(".hidden.gcode", size=1)
        filesystem.attach("SD Card",
                          tree,
                          "",
                          use_inotify=False,
                          storage_type=const.StorageType.SD_CARD)
        return filesystem

    def test_storage_description(self, sd, queue):
        storage = sd.storages()[0]
        assert storage["mountpoint"] == "/SD Card"
        assert storage["type"] == "SD_CARD"
        assert storage["is_sfn"] is False
        assert "read_only" not in storage
        assert "free_space" not in storage
        assert queue.get_nowait().data == storage

    def test_root_of_storage(self, sd):
        info = sd.file_info("/SD Card")
        assert info["type"] == "FOLDER"
        assert info["path"] == "/SD Card"
        assert [c["name"] for c in info["children"]] == ["folder"]

    def test_folder(self, sd):
        info = sd.file_info("/SD Card/folder")
        assert info["m_timestamp"] == 10
        children = {c["name"]: c for c in info["children"]}
        assert sorted(children) == ["a.gcode", "b.txt", "sub"]
        assert children["a.gcode"] == {
            "name": "a.gcode",
            "type": "PRINT_FILE",
            "size": 100,
            "m_timestamp": 11,
            "read_only": True,
        }
        assert children["sub"] == {"name": "sub", "type": "FOLDER"}

    def test_file(self, sd):
        info = sd.file_info("/SD Card/folder/a.gcode")
        assert info["type"] == "PRINT_FILE"
        assert info["size"] == 100
        assert info["path"] == "/SD Card/folder/a.gcode"
        assert "children" not in info

    def test_reflects_tree_changes(self, sd):
        sd.storage_dict["SD Card"].tree.get(["folder"]).add("c.gcode", size=1)
        names = [
            c["name"] for c in sd.file_info("/SD Card/folder")["children"]
        ]
        assert "c.gcode" in names

    @pytest.mark.parametrize("path", [
        "/SD Card/missing",
        "/SD Card/folder/missing",
        "/SD Card/.hidden.gcode",
    ])
    def test_not_found(self, sd, path):
        with pytest.raises(CommandRejected) as err:
            sd.file_info(path)
        assert err.value.reason == "File not found"

    def test_forbidden(self, sd):
        with pytest.raises(CommandRejected) as err:
            sd.file_info("/SD Card/../x")
        assert err.value.reason == "Forbidden path"

    def test_oversized(self, sd):
        with patch("prusa.connect.printer.fileinfo.MAX_FOLDER_ITEMS", 2):
            with pytest.raises(CommandRejected) as err:
                sd.file_info("/SD Card/folder")
        assert err.value.reason == "Oversized command"

    def test_ejected(self, sd, queue):
        queue.get_nowait()
        sd.detach("SD Card")
        assert queue.get_nowait().data == {"mountpoint": "/SD Card"}
        with pytest.raises(CommandRejected):
            sd.file_info("/SD Card")

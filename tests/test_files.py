"""Test of files handling."""
import os
import shutil
import stat
import sys
import tempfile

from collections import namedtuple
from unittest.mock import patch
from queue import Queue

import pytest  # type: ignore

from prusa.connect.printer import const
from prusa.connect.printer.files import File, Filesystem, \
    InvalidMountpointError, InotifyHandler
from prusa.connect.printer.models import Event


# pylint: disable=missing-function-docstring
# pylint: disable=no-self-use
# pylint: disable=invalid-name
# pylint: disable=redefined-outer-name


@pytest.fixture
def nodes():
    """Create file tree in memory."""
    root = File(None, is_dir=True)
    a = root.add("a", is_dir=True)
    a.add("1.txt")
    a.add("b", is_dir=True)
    c = a.add("c", is_dir=True)
    c.add("2.txt")
    c.add("3.txt")
    return root


@pytest.fixture
@patch("prusa.connect.printer.files.stat",
       return_value=os.stat_result((33188, 267912, 64768, 1, 0, 0, 3044,
                                    1599740701, 1596120005, 1596120005)))
@patch("prusa.connect.printer.files.path.abspath", return_value='/a')
@patch("prusa.connect.printer.files.walk",
       return_value=[('/a', ['b', 'c'], ['1.txt']), ('/a/b', [], []),
                     ('/a/c', [], ['2.txt', '3.txt'])])
def fs_from_dir(*mocks):
    fs = Filesystem()
    fs.from_dir('/somewhere/on/the/disk/a', 'a')
    return fs


InotifyFixture = namedtuple('InotifyFixture',
                            ['path', 'handler', 'fs', 'queue'])


@pytest.fixture
def queue():
    yield Queue()


@pytest.fixture
def inotify(queue, nodes):
    """Create and cleanup the same structure as in `nodes` in a temporary
    directory. This returns the path to the dir on storage, the Inotify
    handler and filesystem as a tuple: (path, handler, filesystem).
    """

    def create_on_storage(root_dir, node):
        parts = node.abs_parts()
        parts.insert(0, root_dir)
        path = os.path.sep.join(parts)
        if node.is_dir:
            if not os.path.exists(path):  # root node is already created
                os.mkdir(path)
        else:
            open(path, "w").close()  # create file
        for n in node.children.values():
            create_on_storage(root_dir, n)

    def event_cb(event: const.Event,
                 source: const.Source,
                 timestamp: float = None,
                 command_id: int = None,
                 **kwargs) -> None:
        event_ = Event(event, source, timestamp, command_id, **kwargs)
        queue.put(event_)

    tmp_dir = tempfile.TemporaryDirectory()
    create_on_storage(tmp_dir.name, nodes)

    # mount storage:$tmp_dir as Filesystem:/test
    fs = Filesystem(event_cb=event_cb)
    fs.from_dir(tmp_dir.name, "test")
    # Test event in queue
    event = queue.get_nowait()
    assert event.event == const.Event.MEDIUM_INSERTED
    assert event.source == const.Source.WUI
    assert event.data['root'] == '/test'
    assert len(event.data['files']) == 6
    handler = InotifyHandler(fs)

    yield InotifyFixture(tmp_dir.name, handler, fs, queue)
    del tmp_dir


@pytest.fixture
def fs(nodes):
    fs = Filesystem()
    fs.mount("a", nodes.a, use_inotify=False)
    return fs


class TestFile:
    """Test the methods of the File class"""

    def test_add(self):
        root = File("root", is_dir=True)
        assert not root.children
        assert root.is_dir
        root.add("child")
        assert "child" in root
        assert not root.child.is_dir

    def test_add_to_file(self):
        file = File("file")
        assert not file.is_dir
        with pytest.raises(ValueError):
            file.add("another_file")

    def test_add_multiple(self):
        """Make sure that adding twice the same name adds it only once.
        Any further add() with the name name overwrite the previous file.
        """
        root = File("root", is_dir=True)
        assert len(root.children) == 0
        root.add("a", is_dir=True)
        assert root.a.is_dir
        root.add("a", is_dir=False)
        assert not root.a.is_dir
        assert root.a
        assert len(root.children) == 1

    def test_get(self, nodes):
        # 1st level
        assert nodes.get(["a"])
        # deeper
        assert nodes.get(["a", "c", "2.txt"])

    def test_get_str(self, nodes):
        """One cannot call node.get with a string argument"""
        with pytest.raises(TypeError):
            nodes.get("b/b/d")

    def test_delete(self, nodes):
        nodes.get(["a", "c"]).delete()
        assert "c" not in nodes.get(["a"])

    def test_getitem(self, nodes):
        assert nodes['not found'] is None
        assert nodes.get(["a"])['1.txt']
        assert nodes.get(["a", "c"])['2.txt']
        assert nodes.get(["a", "c"])['3.txt']

    def test_getattr(self, nodes):
        assert nodes.a.b.name == 'b'
        assert nodes.a.c.name == 'c'

    def test_parent(self, nodes):
        assert nodes.a.c.parent == nodes.a

    def test_size(self, fs_from_dir):
        assert fs_from_dir.get("/a").size == 9132
        assert fs_from_dir.get("/a/c").size == 6088
        assert fs_from_dir.get("/a/c/2.txt").size == 3044
        assert fs_from_dir.get("/a/b").size == 0
        assert fs_from_dir.get("/a/1.txt").size == 3044

    def test_m_time(self, fs_from_dir):
        assert fs_from_dir.get("/a").attrs['m_time'] \
               == (2020, 7, 30, 16, 40, 5)

    def test_to_dict(self, fs_from_dir):
        res = fs_from_dir.get("/a").to_dict()
        assert res == {
            'type':
                'DIR',
            'name':
                'a',
            'ro':
                True,
            'm_time': (2020, 7, 30, 16, 40, 5),
            'size':
                9132,
            'children': [{
                'type': 'DIR',
                'name': 'b',
                'ro': True,
                'm_time': (2020, 7, 30, 16, 40, 5),
                'size': 0
            }, {
                'type':
                    'DIR',
                'name':
                    'c',
                'ro':
                    True,
                'm_time': (2020, 7, 30, 16, 40, 5),
                'size':
                    6088,
                'children': [{
                    'type': 'FILE',
                    'name': '2.txt',
                    'ro': True,
                    'm_time': (2020, 7, 30, 16, 40, 5),
                    'size': 3044
                }, {
                    'type': 'FILE',
                    'name': '3.txt',
                    'ro': True,
                    'm_time': (2020, 7, 30, 16, 40, 5),
                    'size': 3044
                }]
            }, {
                'type': 'FILE',
                'name': '1.txt',
                'ro': True,
                'm_time': (2020, 7, 30, 16, 40, 5),
                'size': 3044
            }]
        }

    def test_contains(self, nodes):
        assert "a" in nodes

    def test_str(self):
        d = File("directory", is_dir=True)
        f = File("filename")
        assert str(d) == "directory"
        assert str(f) == "filename"

    def test_abs_parts(self, nodes):
        node = nodes.a.c["2.txt"]
        assert node.abs_parts() == ["a", "c", "2.txt"]

    @pytest.mark.skipif(sys.platform == "win",
                        reason="UINX only tests (depends on path sep)")
    def test_abs_path(self, nodes):
        node = nodes.a.c["2.txt"]
        assert node.abs_path("/test") == "/test/a/c/2.txt"


class TestFilesystem:
    """Test Filesystem class interface."""

    def test_mount(self, fs):
        assert len(fs.mounts) == 1
        assert "a" in fs.mounts

    def test_mount_empty(self, fs, nodes):
        with pytest.raises(InvalidMountpointError):
            fs.mount("", nodes)

    def test_mount_contains_sep(self, fs, nodes):
        with pytest.raises(InvalidMountpointError):
            fs.mount("/b", nodes)

    def test_mount_already_used(self, fs, nodes):
        with pytest.raises(InvalidMountpointError):
            fs.mount("a", nodes)

    def test_unmount(self, fs):
        fs.unmount("a")
        assert len(fs.mounts) == 0

    def test_unmount_invalid_mountpoint(self):
        fs = Filesystem()
        with pytest.raises(ValueError):
            fs.unmount("doesn-not-exist")

    def test_from_dir(self, fs_from_dir, fs):
        b = fs_from_dir.get("/a/b")
        assert not b.children
        assert b.is_dir
        assert fs_from_dir.get("/a/1.txt")
        assert fs_from_dir.get("/a/c/2.txt")
        assert fs_from_dir.get("/a/c/3.txt")

        # test root node
        assert fs_from_dir.get("/a").is_dir is True
        assert fs_from_dir.get("/a/").name == "a"
        assert fs_from_dir.get("/a/").parent is None

    def test_get_root(self, fs):
        a = fs.get("a")
        assert a.name == "a"
        assert a.is_dir
        assert len(a.children) == 3

    def test_get_deep(self, fs):
        """Test walking along the file tree using get()"""
        assert fs.get("a/1.txt")
        assert fs.get("a/c/2.txt")
        assert fs.get("a/c/3.txt")

    def test_to_dict(self, fs):
        assert fs.to_dict() == \
               {'type': 'DIR', 'name': '/', 'ro': True, 'children': [
                   {'type': 'DIR', 'name': 'a', 'size': 0, 'children': [
                       {'type': 'FILE', 'name': '1.txt', 'size': 0},
                       {'type': 'DIR', 'name': 'b', 'size': 0},
                       {'type': 'DIR', 'name': 'c', 'size': 0, 'children': [
                           {'type': 'FILE', 'name': '2.txt', 'size': 0},
                           {'type': 'FILE', 'name': '3.txt', 'size': 0}]}]}]}


class TestINotify:
    """Test events from Inotify class."""

    def test_CREATE_file(self, inotify):
        """Test that creating a file is reflected in the Filesystem
        and that also Connect is notified by the means of an Event
        """
        p = os.path.join(inotify.path, "simple.txt")
        open(p, "w").close()
        inotify.handler()
        assert inotify.fs.get("/test/simple.txt")
        assert inotify.fs.get("/test/does-not-exit.txt") is None

        # check event to Connect
        event = inotify.queue.get_nowait()
        assert event.event == const.Event.FILE_CHANGED
        assert event.source == const.Source.WUI
        assert len(event.data['file']['m_time']) == 6
        assert event.data['file']['name'] == "simple.txt"
        assert not event.data['file']['ro']
        assert event.data['file']['type'] == "FILE"
        assert event.data['new_path'] == '/test/simple.txt'
        assert event.data['old_path'] is None

    def test_CREATE_dir(self, inotify):
        """Same as CREATE_file but this time a directory is used."""
        p = os.path.join(inotify.path, "directory")
        os.mkdir(p)
        inotify.handler()
        d = inotify.fs.get("/test/directory")
        assert d
        assert d.is_dir

        # check event to Connect
        event = inotify.queue.get_nowait()
        assert event.event == const.Event.FILE_CHANGED
        assert event.source == const.Source.WUI
        assert len(event.data['file']['m_time']) == 6
        assert event.data['file']['name'] == "directory"
        assert not event.data['file']['ro']
        assert event.data['file']['type'] == "DIR"
        assert event.data['new_path'] == '/test/directory'
        assert event.data['old_path'] is None

        # test that a inotify watch has also been installed for the
        #  newly added dir
        file_path = os.path.join(p, "file.txt")
        open(file_path, "w").close()
        inotify.handler()
        assert inotify.fs.get("/test/directory/file.txt")

    def test_DELETE_file(self, inotify):
        """Test deleting a file by creating it first, then deleting it and
        requesting it from the Filesystem. Also test that other file(s) were
        not affected
        """
        p = os.path.join(inotify.path, "simple.txt")
        open(p, "w").close()
        inotify.handler()
        assert inotify.fs.get("/test/simple.txt")

        os.unlink(p)
        inotify.handler()
        assert not inotify.fs.get("/test/simple.txt")
        assert inotify.fs.get("/test/a/c/2.txt")

        # check event to Connect
        event = None
        while not inotify.queue.empty():
            event = inotify.queue.get_nowait()
        assert event.event == const.Event.FILE_CHANGED
        assert event.source == const.Source.WUI
        assert event.data['old_path'] == "/test/simple.txt", event.data
        assert event.data['new_path'] is None

    def test_DELETE_dir(self, inotify):
        """Test that after deleting a directory it is removed from the
        Filesystem.
        """
        node = inotify.fs.get("/test/a/b")
        path = node.abs_path(inotify.path)
        assert inotify.fs.get("/test/a/b")
        os.rmdir(path)
        inotify.handler()
        assert not inotify.fs.get("/test/a/b")

        # check event to Connect
        event = inotify.queue.get_nowait()
        assert event.event == const.Event.FILE_CHANGED
        assert event.source == const.Source.WUI
        assert event.data['old_path'] == "/test/a/b"
        assert event.data['new_path'] is None

    def test_DELETE_root_dir(self, inotify):
        """Test removing the root of a mount `Mount.path_storage` in a
        `Filesystem`
        """
        shutil.rmtree(inotify.path)
        inotify.handler()
        assert not inotify.fs.get("/test/a/1.txt")
        assert not inotify.fs.get("/test/a/c")

        # check event to Connect
        event = None
        while not inotify.queue.empty():
            event = inotify.queue.get_nowait()
        assert event.event == const.Event.FILE_CHANGED
        assert event.source == const.Source.WUI
        assert event.data['old_path'] == event.data['new_path'] == "/test/"
        assert event.data['file']['type'] == "DIR"
        assert "m_time" not in event.data['file']
        assert event.data['file']['name'] == "test"

    def test_MOVE_file(self, inotify):
        """Create a file and move it to a different directory"""
        src = inotify.fs.get("/test/a/1.txt")
        assert src
        src_path = src.abs_path(inotify.path)

        dst = inotify.fs.get("/test/a/c")
        assert dst
        dst_path = dst.abs_path(inotify.path)

        shutil.move(src_path, dst_path)
        inotify.handler()
        assert inotify.fs.get("/test/a/c/1.txt")

        # check event to Connect
        event = None
        while not inotify.queue.empty():
            event = inotify.queue.get_nowait()
        assert event.event == const.Event.FILE_CHANGED
        assert event.source == const.Source.WUI
        assert event.data['old_path'] is None
        assert event.data['file']['name'] == "1.txt"
        assert event.data['new_path'] == "/test/a/c/1.txt"

    def test_MODIFY_file(self, inotify):
        """Write into a file and make sure that the change is reflected"""
        node = inotify.fs.get("/test/a/1.txt")
        assert node.attrs['size'] == 0
        assert node.attrs['ro'] is False
        path = node.abs_path(inotify.path)
        with open(path, "a") as fh:
            fh.write("Hello World")
        os.chmod(path, stat.S_IREAD)

        inotify.handler()
        node = inotify.fs.get("/test/a/1.txt")
        assert node.attrs['size'] == 11
        assert node.attrs['ro'] is True

        # check event to Connect
        event = None
        while not inotify.queue.empty():
            event = inotify.queue.get_nowait()
        assert event.event == const.Event.FILE_CHANGED
        assert event.source == const.Source.WUI
        assert event.data['file']['name'] == "1.txt"
        assert "m_time" in event.data['file']
        assert event.data['file']['ro']
        assert event.data['old_path'] == "/test/a/1.txt"
        assert event.data['new_path'] == "/test/a/1.txt"

    def test_connect_302(self, inotify, nodes):
        inotify.fs.mount("wrong", nodes, storage_path="/t")
        inotify.fs.mount("right", nodes, storage_path="/tmp")

        mount = inotify.handler.mount_for("/tmp/a/b")
        assert mount.mountpoint == 'right'

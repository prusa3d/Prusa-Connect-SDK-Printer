import os
import shutil
import stat
import sys
import tempfile
from collections import namedtuple
from unittest.mock import patch

import pytest  # type: ignore
import requests_mock    # type: ignore

from prusa.connect.printer.files import File, Filesystem, \
    InvalidMountpointError, InotifyHandler
from .test_events import connection, SERVER

assert connection       # stop pre-commit from complaining


EVENTS_URL = f"{SERVER}/p/events"


@pytest.fixture
def nodes():
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
@patch("prusa.connect.printer.files.walk", return_value=[
    ('/a', ['b', 'c'], ['1.txt']),
    ('/a/b', [], []),
    ('/a/c', [], ['2.txt', '3.txt'])
])
def fs_from_dir(*mocks):
    fs = Filesystem()
    fs.from_dir('/somewhere/on/the/disk/a', 'a')
    return fs


InotifyFixture = namedtuple('InotifyFixture', ['path', 'handler', 'fs'])


@pytest.fixture
def inotify(nodes, connection):
    """Create and cleanup the same structure as in `nodes` in a temporary
    directory. This returns the path to the dir on storage, the Inotify
    handler and filesystem as a tuple: (path, handler, filesystem).
    """
    def create_on_storage(root_dir, node):
        parts = node.abs_parts()
        parts.insert(0, root_dir)
        path = os.path.sep.join(parts)
        if node.is_dir:
            if not os.path.exists(path):    # root node is already created
                os.mkdir(path)
        else:
            open(path, "w").close()         # create file
        for n in node.children.values():
            create_on_storage(root_dir, n)
    tmp_dir = tempfile.TemporaryDirectory()
    create_on_storage(tmp_dir.name, nodes)

    # mount storage:$tmp_dir as Filesystem:/test
    fs = Filesystem(connection=connection)
    with requests_mock.Mocker() as req_mock:
        # test within a context manager so each test for events
        #  has to explicitly mock POST $EVENTS_URL
        res = req_mock.post(EVENTS_URL, status_code=204)
        fs.from_dir(tmp_dir.name, "test")
        data = res.last_request.json()
        assert data['event'] == 'MEDIUM_INSERTED'
        assert data['source'] == 'CONNECT'
        assert data['data']['root'] == '/test'
        assert len(data['data']['files']) == 5
    handler = InotifyHandler(fs)

    yield InotifyFixture(tmp_dir.name, handler, fs)
    del tmp_dir


@pytest.fixture
def fs(nodes):
    fs = Filesystem()
    fs.mount("a", nodes.a)
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

    def test_umount(self, fs):
        fs.umount("a")
        assert len(fs.mounts) == 0

    def test_umount_invalid_mountpoint(self):
        fs = Filesystem()
        with pytest.raises(ValueError):
            fs.umount("doesn-not-exist")

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
               {'type': 'DIR', 'path': '/', 'ro': True, 'children': [
                   {'type': 'DIR', 'path': 'a', 'children': [
                       {'type': 'FILE', 'path': '1.txt'},
                       {'type': 'DIR', 'path': 'b'},
                       {'type': 'DIR', 'path': 'c', 'children': [
                           {'type': 'FILE', 'path': '2.txt'},
                           {'type': 'FILE', 'path': '3.txt'}]}]}]}


class TestINotify:

    def test_CREATE_file(self, inotify, requests_mock):
        """Test that creating a file is reflected in the Filesystem
        and that also Connect is notified by the means of an Event
        """
        res = requests_mock.post(EVENTS_URL, status_code=204)
        p = os.path.join(inotify.path, "simple.txt")
        open(p, "w").close()
        inotify.handler()
        assert inotify.fs.get("/test/simple.txt")
        assert inotify.fs.get("/test/does-not-exit.txt") is None

        # check event to Connect
        data = res.last_request.json()
        assert data['event'] == 'FILE_CHANGED'
        assert data['source'] == 'CONNECT'
        assert len(data['data']['file']['m_time']) == 6
        assert data['data']['file']['path'] == "simple.txt"
        assert not data['data']['file']['ro']
        assert data['data']['file']['type'] == "FILE"
        assert data['data']['new_path'] == '/test/simple.txt'
        assert data['data']['old_path'] is None

    def test_CREATE_dir(self, inotify, requests_mock):
        """Same as CREATE_file but this time a directory is used."""
        res = requests_mock.post(EVENTS_URL, status_code=204)
        p = os.path.join(inotify.path, "directory")
        os.mkdir(p)
        inotify.handler()
        d = inotify.fs.get("/test/directory")
        assert d
        assert d.is_dir

        # test that an event to CONNECT was sent
        data = res.last_request.json()
        assert data['event'] == 'FILE_CHANGED'
        assert data['source'] == 'CONNECT'
        assert len(data['data']['file']['m_time']) == 6
        assert data['data']['file']['path'] == "directory"
        assert not data['data']['file']['ro']
        assert data['data']['file']['type'] == "DIR"
        assert data['data']['new_path'] == '/test/directory'
        assert data['data']['old_path'] is None

        # test that a inotify watch has also been installed for the
        #  newly added dir
        file_path = os.path.join(p, "file.txt")
        open(file_path, "w").close()
        inotify.handler()
        assert inotify.fs.get("/test/directory/file.txt")

    def test_DELETE_file(self, inotify, requests_mock):
        """Test deleting a file by creating it first, then deleting it and
        requesting it from the Filesystem. Also test that other file(s) were
        not affected
        """
        res = requests_mock.post(EVENTS_URL, status_code=204)
        p = os.path.join(inotify.path, "simple.txt")
        open(p, "w").close()
        inotify.handler()
        assert inotify.fs.get("/test/simple.txt")

        os.unlink(p)
        inotify.handler()
        assert not inotify.fs.get("/test/simple.txt")
        assert inotify.fs.get("/test/a/c/2.txt")

        # test sending of event to CONNECT`
        data = res.last_request.json()
        assert data['event'] == 'FILE_CHANGED'
        assert data['source'] == 'CONNECT'
        assert data['data']['old_path'] == "/test/simple.txt"
        assert data['data']['new_path'] is None

    def test_DELETE_dir(self, inotify, requests_mock):
        """Test that after deleting a directory it is removed from the
        Filesystem.
        """
        res = requests_mock.post(EVENTS_URL, status_code=204)
        node = inotify.fs.get("/test/a/b")
        path = node.abs_path(inotify.path)
        assert inotify.fs.get("/test/a/b")
        os.rmdir(path)
        inotify.handler()
        assert not inotify.fs.get("/test/a/b")

        # test event to Connect
        data = res.last_request.json()
        assert data['event'] == 'FILE_CHANGED'
        assert data['source'] == 'CONNECT'
        assert data['data']['old_path'] == "/test/a/b"
        assert data['data']['new_path'] is None

    def test_DELETE_root_dir(self, inotify, requests_mock):
        """Test removing the root of a mount `Mount.path_storage` in a
        `Filesystem`
        """
        # this also tests MOVE_SELF and events
        res = requests_mock.post(EVENTS_URL, status_code=204)
        shutil.rmtree(inotify.path)
        inotify.handler()
        assert not inotify.fs.get("/test/a/1.txt")
        assert not inotify.fs.get("/test/a/c")

        # check Connect event
        data = res.last_request.json()
        assert data['event'] == 'FILE_CHANGED'
        assert data['source'] == 'CONNECT'
        assert data['data']['old_path'] == data['data']['new_path'] == "/test/"
        assert data['data']['file']['type'] == "DIR"
        assert "m_time" not in data['data']['file']
        assert data['data']['file']['path'] == "test"

    def test_MOVE_file(self, inotify, requests_mock):
        """Create a file and move it to a different directory"""
        res = requests_mock.post(EVENTS_URL, status_code=204)
        src = inotify.fs.get("/test/a/1.txt")
        assert src
        src_path = src.abs_path(inotify.path)

        dst = inotify.fs.get("/test/a/c")
        assert dst
        dst_path = dst.abs_path(inotify.path)

        shutil.move(src_path, dst_path)
        inotify.handler()
        assert inotify.fs.get("/test/a/c/1.txt")

        # test that last move sends events to CONNECT
        data = res.last_request.json()
        assert data['event'] == 'FILE_CHANGED'
        assert data['source'] == 'CONNECT'
        assert data['data']['old_path'] is None
        assert data['data']['file']['path'] == "1.txt"
        assert data['data']['new_path'] == "/test/a/c/1.txt"

    def test_MODIFY_file(self, inotify, requests_mock):
        """Write into a file and make sure that the change is reflected"""
        res = requests_mock.post(EVENTS_URL, status_code=204)
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

        # test events -> Connect
        data = res.last_request.json()
        assert data['event'] == 'FILE_CHANGED'
        assert data['source'] == 'CONNECT'
        assert data['data']['file']['path'] == "1.txt"
        assert "m_time" in data['data']['file']
        assert data['data']['file']['ro']
        assert data['data']['old_path'] == "/test/a/1.txt"
        assert data['data']['new_path'] == "/test/a/1.txt"

import pytest
import tempfile
import os
import shutil
import stat
import sys

from collections import namedtuple
from prusa.connect.printer.files import File, Filesystem, \
    InvalidMountpointError, InotifyHandler


@pytest.fixture
def nodes():
    root = File(None, dir=True)
    a = root.add("a", dir=True)
    a.add("1.txt")
    a.add("b", dir=True)
    c = a.add("c", dir=True)
    c.add("2.txt")
    c.add("3.txt")
    return root


@pytest.fixture
def fs_from_dir(mocker):
    fs = Filesystem()
    stat_mock = os.stat_result((33188, 267912, 64768, 1, 0, 0, 3044,
                               1599740701, 1596120005, 1596120005))
    mocker.patch("os.stat", return_value=stat_mock)
    mocker.patch("os.path.abspath", return_value='/a')
    mocker.patch("os.walk", return_value=[
            ('/a', ['b', 'c'], ['1.txt']),
            ('/a/b', [], []),
            ('/a/c', [], ['2.txt', '3.txt'])
        ])
    fs.from_dir('/somewhere/on/the/disk/a', 'a')
    return fs


InotifyFixture = namedtuple('InotifyFixture', ['path', 'handler', 'fs'])


@pytest.fixture()
def inotify(nodes):
    """Create and cleanup the same structure as in `nodes` in a temporary
    directory. This returns the path to the dir on storage, the Inotify
    handler and filesystem as a tuple: (path, handler, filesystem).
    """
    def create_on_storage(root_dir, node):
        parts = node.abs_parts()
        parts.insert(0, root_dir)
        path = os.path.sep.join(parts)
        if node.dir:
            if not os.path.exists(path):    # root node is already created
                os.mkdir(path)
        else:
            open(path, "w").close()         # create file
        for n in node.children.values():
            create_on_storage(root_dir, n)
    tmp_dir = tempfile.TemporaryDirectory()
    create_on_storage(tmp_dir.name, nodes)

    # mount storage:$tmp_dir as Filesystem:/test
    fs = Filesystem()
    fs.from_dir(tmp_dir.name, "test")
    handler = InotifyHandler(fs)

    yield InotifyFixture(tmp_dir.name, handler, fs)
    del tmp_dir


@pytest.fixture
def fs(nodes):
    fs = Filesystem()
    fs.mount("a", nodes.a)
    return fs


class TestFile:
    def test_add(self):
        root = File("root", dir=True)
        assert not root.children
        assert root.dir
        root.add("child")
        assert "child" in root
        assert not root.child.dir

    def test_add_to_file(self):
        file = File("file")
        assert not file.dir
        with pytest.raises(ValueError):
            file.add("another_file")

    def test_add_multiple(self):
        root = File("root", dir=True)
        assert len(root.children) == 0
        root.add("a")
        root.add("a")
        assert root.a
        assert len(root.children) == 1

    def test_get(self, nodes):
        # 1st level
        assert nodes.get(["a"])
        # deeper
        assert nodes.get(["a", "c", "2.txt"])

    def test_get_str(self, nodes):
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
        d = File("directory", dir=True)
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
        assert b.dir
        assert fs_from_dir.get("/a/1.txt")
        assert fs_from_dir.get("/a/c/2.txt")
        assert fs_from_dir.get("/a/c/3.txt")

        # test root node
        assert fs_from_dir.get("/a").dir is True
        assert fs_from_dir.get("/a/").name == "a"
        assert fs_from_dir.get("/a/").parent is None

    def test_get_root(self, fs):
        a = fs.get("a")
        assert a.name == "a"
        assert a.dir
        assert len(a.children) == 3

    def test_get_deep(self, fs):
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
                           {'type': 'FILE', 'path': '3.txt'}]}]}]}  # NOQA: E501


class TestINotify:
    def test_CREATE_file(self, inotify):
        p = os.path.join(inotify.path, "simple.txt")
        open(p, "w").close()
        inotify.handler()
        assert inotify.fs.get("/test/simple.txt")
        assert inotify.fs.get("/test/does-not-exit.txt") is None

    def test_CREATE_dir(self, inotify):
        p = os.path.join(inotify.path, "directory")
        os.mkdir(p)
        inotify.handler()
        d = inotify.fs.get("/test/directory")
        assert d
        assert d.dir

        # test that a inotify watch has also been installed
        file_path = os.path.join(p, "file.txt")
        open(file_path, "w").close()
        inotify.handler()
        assert inotify.fs.get("/test/directory/file.txt")

    def test_DELETE_file(self, inotify):
        p = os.path.join(inotify.path, "simple.txt")
        open(p, "w").close()
        inotify.handler()
        assert inotify.fs.get("/test/simple.txt")

        os.unlink(p)
        inotify.handler()
        assert not inotify.fs.get("/test/simple.txt")
        assert inotify.fs.get("/test/a/c/2.txt")

    def test_DELETE_dir(self, inotify):
        node = inotify.fs.get("/test/a/b")
        path = node.abs_path(inotify.path)
        assert inotify.fs.get("/test/a/b")
        os.rmdir(path)
        inotify.handler()
        assert not inotify.fs.get("/test/a/b")

    def test_DELETE_SELF(self, inotify):
        # this also tests MOVE_SELF and UNMOUNT events
        shutil.rmtree(inotify.path)
        inotify.handler()
        assert not inotify.fs.get("/test/a/1.txt")
        assert not inotify.fs.get("/test/a/c")

    def test_MOVE_file(self, inotify):
        src = inotify.fs.get("/test/a/1.txt")
        assert src
        src_path = src.abs_path(inotify.path)

        dst = inotify.fs.get("/test/a/c")
        assert dst
        dst_path = dst.abs_path(inotify.path)

        shutil.move(src_path, dst_path)
        inotify.handler()
        assert inotify.fs.get("/test/a/c/1.txt")

    def test_MODIFY_file(self, inotify):
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

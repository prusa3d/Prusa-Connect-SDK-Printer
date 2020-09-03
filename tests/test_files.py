import pytest

from prusa.connect.printer.files import File, Filesystem, \
    InvalidMountpointError


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
    mocker.patch("os.path.abspath", return_value='/a')
    mocker.patch("os.walk", return_value=[
            ('/a', ['b', 'c'], ['1.txt']),
            ('/a/b', [], []),
            ('/a/c', [], ['2.txt', '3.txt'])
        ])
    fs.from_dir('/somewhere/on/the/disk/a', 'a')
    return fs


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

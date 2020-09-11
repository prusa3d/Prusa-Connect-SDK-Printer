"""File management"""

import os
import typing

# pylint: disable=fixme
# pylint: disable=redefined-builtin
# pylint: disable=too-few-public-methods
# pylint: disable=missing-class-docstring


class File:
    """A node of a Filesystem representing either a file or a directory"""

    def __init__(self, name: str, dir: bool = False, parent: "File" = None,
                 **attrs):
        """Create a File object

        :param name: Filename
        :param dr: Flag whether this is a directory
        :param parent: Parent for this File, which itself is a File(dir=True)
        :param attrs: Any attributes for the file you want to store. File's
            to_dict() method add `ro`, `m_time` and `size` attributes, if
            it finds them.
        """
        self.name = name
        self.dir = dir
        self.parent = parent
        self.attrs = attrs
        self.children: dict = {}

    def add(self, name: str, dir: bool = False, **attrs):
        """Add a file to this File's children.
        Note that `self` must be a directory.

        :param name: name of the file
        :param dir: Is this a directory?
        :param attrs: arbitrary File attributes
        :raise ValueError: if self is not a directory
        :return the added file.
        """
        if not self.dir:
            raise ValueError("You can add only to directories")
        node = File(name, dir=dir, parent=self, **attrs)
        self.children[node.name] = node
        return node

    def get(self, parts: typing.Iterable[str]):
        """
        Return the node identified by `parts`, which is a collection of
        names that will be matched on the path to it.

        :param parts: Path identifying a node you are looking fo
        :return: the found node
        :raise TypeError if `parts` is string and not a collection of strings
        """
        if isinstance(parts, str):
            raise TypeError("`part` must be a collection of strings")

        last = self
        for part in parts:
            node = last[part]
            if not node:
                return None
            last = node
        return last

    def delete(self):
        """Delete this node"""
        del self.parent.children[self.name]

    def pprint(self, file=None, _prefix="", _last=True, _first=True):
        """Pretty print the File as  a tree. `self` should be a directory
        for this method to make any makes sense.

        :param file: file object to that the tree will be printed
        """
        symbol = "└─" if _last else "├─"
        if _first:
            line = f"{symbol} {self.name}"
        else:
            line = f"{_prefix} {symbol} {self.name}"
        print(line, file=file)
        _prefix += "   " if _last else " │  "
        counter = len(self.children)
        for child in self.children.values():
            counter -= 1
            child.pprint(file=file,
                         _prefix=_prefix,
                         _last=counter == 0,
                         _first=False)

    def to_dict(self):
        """:return `self` in the format for Connect Backend"""
        result = {
            "type": "DIR" if self.dir else "FILE",
            "path": self.name,
        }
        for attr in ("ro", "size", "m_time"):
            if attr in self.attrs:
                result[attr] = self.attrs[attr]
        children = [child.to_dict() for child in self.children.values()]
        if children:
            result['children'] = children
        return result

    def __contains__(self, item):
        return item in self.children

    def __getitem__(self, name):
        return self.children.get(name)

    def __getattr__(self, item):
        return self[item]

    def __str__(self):
        return self.name


class Mount:
    """Represent a mountpoint"""
    def __init__(self, tree, fs_sync=False):
        self.tree = tree
        self.fs_sync = fs_sync


class InvalidMountpointError(ValueError):
    pass


class Filesystem:
    """Collection of Files (which are grouped in to trees). This is like
    the DOS filesystem flat, therefore unlike in UNIX OSes tress cannot
    be nested in eachother.
    """

    def __init__(self, sep="/"):
        self.sep = sep
        self.mounts = dict()

    def mount(self, name: str, tree: File):
        """Mount the a tree under a mountpoint.

        :param name: The mountpoint
        :param tree: The tree of `File` instances to be mounted
        :raises InvalidMountpointError: If the mountpoint is already used,
            or when it contains `self.sep` or when the `name` is empty or
            `self.sep` only.
        """
        # TODO MEDIUM_INSERTED event

        if not name:
            raise InvalidMountpointError("Mountpoint cannot be empty")

        if self.sep in name:
            msg = f"Mountpoints cannot contain {self.sep}"
            raise InvalidMountpointError(msg)

        if name in self.mounts:
            raise InvalidMountpointError(f"`{name}` is already used")

        self.mounts[name] = Mount(tree)

    def umount(self, name: str):
        """Umount a mountpoint.

        :param name: The mountpoint
        :raises InvalidMountpointError: if `name` is not mounted
        """
        # TODO MEDIUM_EJECTED event
        if name not in self.mounts:
            msg = f"`{name}` is not used as a mountpoint"
            raise InvalidMountpointError(msg)

        del self.mounts[name]

    def get(self, abs_path: str):
        """Return the File addressed by `abs_path`

        :param abs_path: Absolute path to the file
        :return File: return the File or none
        """
        abs_path = abs_path.strip(self.sep)
        mountpoint, *parts = abs_path.split(self.sep)
        if mountpoint not in self.mounts:
            return None

        return self.mounts[mountpoint].tree.get(parts)

    def to_dict(self):
        """Return all the tree in the representation Connect requires

        :return: dictionary representation of the Filesystem"""
        root = {
            "type": "DIR",
            "path": "/",
            "ro": True,
            "children": [m.tree.to_dict() for m in self.mounts.values()]
        }
        return root

    def from_dir(self, dirpath: str, mountpoint: str):
        """Initialize a (File) tree from `dirpath` and mount it.

        :param dirpath: The directory on store from which to create the FS
        :param mountpoint: Mountpoint
        """
        # normalize dirpath
        dirpath = os.path.abspath(dirpath)
        if not dirpath.endswith(os.path.sep):
            dirpath += os.path.sep

        # create nodes
        root = File(os.path.dirname(dirpath).strip(self.sep), dir=True)

        for abs_dir, dirs, files in os.walk(dirpath):
            dirname = abs_dir[len(dirpath):]
            if not dirname:
                parent = root
            else:
                parent = root.get(dirname.split(os.path.sep))

            for name in dirs:
                parent.add(name, dir=True)
            for name in files:
                parent.add(name)

        # mount
        self.mount(mountpoint, root)

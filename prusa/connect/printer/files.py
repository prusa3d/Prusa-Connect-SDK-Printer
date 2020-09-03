"""File management"""

import os
import typing

from logging import getLogger

log = getLogger("connect-files")


class File:
    """A node of a Filesystem representing either a file or a directory"""

    def __init__(self, name, dir=False, parent=None, **attr):
        self.name = name
        self.dir = dir
        self.parent = parent
        self.attr = attr
        self.children = {}

    def add(self, name, type=None, **attr):
        if not self.dir:
            raise ValueError("You can add only to directories")
        node = File(name, type=type, parent=self, **attr)
        self.children[node.name] = node
        return node

    def get(self, parts: typing.Iterable[str]):
        if type(parts) is str:
            raise TypeError("`part` must be a collection of strings")

        last = self
        for part in parts:
            node = last[part]
            if not node:
                return None
            last = node
        return last

    def delete(self):
        del self.parent.children[self.name]

    def pprint(self, prefix="", last=True, first=True, file=None):
        symbol = "└─" if last else "├─"
        if first:
            line = "%s %s" % (symbol, self.name)
        else:
            line = "%s %s %s" % (prefix, symbol, self.name)
        print(line, file=file)
        prefix += "   " if last else " │  "
        counter = len(self.children)
        for child in self.children.values():
            counter -= 1
            child.pprint(prefix=prefix,
                         file=file,
                         last=counter == 0,
                         first=False)

    def to_dict(self):
        result = {
            "type": "DIR" if self.dir else "FILE",
            "path": self.name,
        }
        for attr in ("ro", "size", "m_time"):
            if attr in self.attr:
                result[attr] = self.attr[attr]
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


class Mount:
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
            raise FileNotFoundError(abs_path)

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

"""File management"""

import os
import typing
from datetime import datetime

from inotify_simple import INotify, flags   # type: ignore

from . import log


# pylint: disable=fixme
# pylint: disable=invalid-name
# pylint: disable=redefined-builtin
# pylint: disable=too-few-public-methods
# pylint: disable=missing-function-docstring
# pylint: disable=unused-argument


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

    def _abs_parts(self, result):
        # pylint: disable=protected-access
        if self.parent:
            result.insert(0, self.name)
            self.parent._abs_parts(result)
        return result

    def abs_parts(self):
        """Return all the parts until root"""
        result = []
        return self._abs_parts(result)

    def abs_path(self, path_storage: str):
        """Return the absolute path of this File

        :param path_storage: path on the storage to be used for creation
            of the absolute path to this File.
        """
        return os.path.join(path_storage, os.sep.join(self.abs_parts()))

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

    def set_attrs(self, abs_path):
        """Set attributes on this file according to `abs_path` file on storage.
        """
        stats = os.stat(abs_path)
        self.attrs["ro"] = not os.access(abs_path, os.W_OK)
        if not self.dir:
            self.attrs["size"] = stats.st_size
        m_datetime = datetime.fromtimestamp(stats.st_mtime)
        m_time = datetime.timetuple(m_datetime)[:6]
        self.attrs["m_time"] = m_time


class Mount:
    """Represent a mountpoint"""

    def __init__(self, tree, mountpoint, abs_path_storage):
        """
        Initialize a Mount.

        :param tree: tree of File instances
        :param mountpoint: mount point on the virtual FS
        :param abs_path_storage: absolute path on the physical storage
        """
        self.tree = tree
        self.mountpoint = mountpoint
        self.path_storage = abs_path_storage


class InvalidMountpointError(ValueError):
    """Mountpoint is not valid"""
    ...


class Filesystem:
    """Collection of Files (which are grouped in to trees). This is like
    the DOS filesystem flat, therefore unlike in UNIX OSes tress cannot
    be nested in eachother.
    """

    def __init__(self, sep="/"):
        self.sep = sep
        self.mounts = dict()        # FS-mountpoint:Mount(...)

    def mount(self, name: str, tree: File, storage_path: str = None):
        """Mount the a tree under a mountpoint.

        :param name: The mountpoint
        :param tree: The tree of `File` instances to be mounted
        :param storage_path: Path on storage, if any
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

        self.mounts[name] = Mount(tree, name, storage_path)

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
        name = os.path.dirname(dirpath)
        name = os.path.split(name)[1]
        root = File(name, dir=True)
        root.set_attrs(dirpath)

        for abs_dir, dirs, files in os.walk(dirpath):
            dirname = abs_dir[len(dirpath):]
            if not dirname:
                parent = root
            else:
                parent = root.get(dirname.split(os.path.sep))

            for name in dirs:
                node = parent.add(name, dir=True)
                node.set_attrs(os.path.join(abs_dir, name))
            for name in files:
                node = parent.add(name)
                node.set_attrs(os.path.join(abs_dir, name))

        # mount
        self.mount(mountpoint, root, dirpath)


class InotifyHandler:
    """This is handler is initialised with a Filesystem instance and
    using it makes sure that all its mounts `tree`s are updated on changes
    on the physical storage"""

    WATCH_FLAGS = flags.CREATE | flags.DELETE | flags.MODIFY | \
        flags.DELETE_SELF | flags.MOVED_TO | flags.MOVED_FROM | \
        flags.MOVE_SELF | flags.UNMOUNT

    def __init__(self, fs: Filesystem):
        self.fs = fs
        self.inotify = INotify()
        self.wds: typing.Dict[int, str] = {}       # watch descriptors
        # init mount watches
        for mount in self.fs.mounts.values():
            self._init_wd(mount.path_storage, mount.tree)

    def _init_wd(self, path_storage, node):
        abs_dir = os.path.join(path_storage, os.sep.join(node.abs_parts()))
        try:
            wd = self.inotify.add_watch(abs_dir, self.WATCH_FLAGS)
            self.wds[wd] = abs_dir
            log.debug("Added watch (%s) for %s", wd, abs_dir)
            for n in node.children.values():
                if n.dir:
                    self._init_wd(path_storage, n)
        except PermissionError:
            pass

    def __call__(self, timeout=0):
        """Process inotify events. This picks the proper `process_$FLAG`
        handler method and executes it with the absolute_path of the
        affected file as argument.
        """
        for event in self.inotify.read(timeout=timeout):
            for flag in flags.from_mask(event.mask):
                # ignore not watched events
                if not self.WATCH_FLAGS & flag:
                    log.debug("Ignoring %s", flag.name)
                    continue
                handler = getattr(self, f"process_{flag.name}")
                parent_dir = self.wds[event.wd]
                abs_path = os.path.join(parent_dir, event.name)
                print("Flag", flag.name, abs_path, event)
                handler(abs_path, event)

    # pylint: disable=inconsistent-return-statements
    def mount_for(self, abs_path):
        """Find the proper mount for the `path` in self.fs"""
        for mount in self.fs.mounts.values():
            if abs_path.startswith(mount.path_storage):
                return mount

    def _rel_path_parts(self, path, mount):
        rel_path = path[len(mount.path_storage):]
        return rel_path.split(self.fs.sep)

    def process_CREATE(self, abs_path, event):
        mount = self.mount_for(abs_path)
        parts = self._rel_path_parts(abs_path, mount)
        *parent, name = parts
        is_dir = event.mask & flags.ISDIR
        node = mount.tree.get(parent).add(name, dir=is_dir)
        node.set_attrs(abs_path)
        if is_dir:
            # add inotify watch
            self._init_wd(mount.path_storage, node)

    def process_DELETE(self, abs_path, event):
        mount = self.mount_for(abs_path)
        parts = self._rel_path_parts(abs_path, mount)
        node = mount.tree.get(parts)
        if node:
            node.delete()

    def process_MODIFY(self, abs_path, event):
        mount = self.mount_for(abs_path)
        parts = self._rel_path_parts(abs_path, mount)
        node = mount.tree.get(parts)
        node.set_attrs(abs_path)

    def process_MOVED_FROM(self, abs_path, event):
        self.process_DELETE(abs_path, event)

    def process_MOVED_TO(self, abs_path, event):
        self.process_CREATE(abs_path, event)

    def process_DELETE_SELF(self, abs_path, event):
        mount = self.mount_for(abs_path)
        mount.tree.children = dict()
        mount.tree.attrs = dict()

    def process_MOVE_SELF(self, abs_path, event):
        self.process_DELETE_SELF(abs_path, event)

    def process_UNMOUNT(self, abs_path, event):
        self.process_DELETE_SELF(abs_path, event)

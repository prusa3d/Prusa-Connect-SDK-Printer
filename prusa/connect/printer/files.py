"""File management"""

from __future__ import annotations
import typing
import weakref
from logging import getLogger
from datetime import datetime
from os import path, access, W_OK, stat, walk

from inotify_simple import INotify, flags  # type: ignore

from .models import EventCallback
from . import const

log = getLogger("connect-printer")

# pylint: disable=fixme
# pylint: disable=too-few-public-methods


class File:
    """A node of a Filesystem representing either a file or a directory"""
    def __init__(self,
                 name: str,
                 is_dir: bool = False,
                 parent: "File" = None,
                 **attrs):
        """Create a File object

        :param name: Filename
        :param is_dir: Flag whether this is a directory
        :param parent: Parent for this File, which itself is a
            File(is_dir=True)
        :param attrs: Any attributes for the file you want to store. File's
            to_dict() method add `ro`, `m_time` and `size` attributes, if
            it finds them.
        """
        self.name = name
        self.is_dir = is_dir
        if parent is not None:
            self._parent: File = weakref.proxy(parent)
        else:
            self._parent = None
        self.attrs = attrs
        self.children: dict = {}

    @property
    def parent(self):
        return self._parent

    @parent.setter
    def parent(self, parent):
        self._parent = weakref.ref(parent)

    def add(self, name: str, is_dir: bool = False, **attrs):
        """Add a file to this File's children.
        Note that `self` must be a directory.

        :param name: name of the file
        :param is_dir: Is this a directory?
        :param attrs: arbitrary File attributes
        :raise ValueError: if self is not a directory
        :return the added file.
        """
        if not self.is_dir:
            raise ValueError("You can add only to directories")
        node = File(name, is_dir=is_dir, parent=self, **attrs)
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

        if parts == [""]:
            return self

        last = self
        for part in parts:
            node = last[part]
            if not node:
                return None
            last = node
        return last

    def abs_parts(self, result=None):
        """Return all the parts until root"""
        result = result or []
        if self.parent:
            result.insert(0, self.name)
            return self.parent.abs_parts(result)
        return result

    def abs_path(self, prefix: str):
        """Return the absolute path of this File

        :param prefix: prefix as a path to be used for absolute path geneartion
        """
        if not prefix.startswith(path.sep):
            prefix = path.sep + prefix
        return path.join(prefix, path.sep.join(self.abs_parts()))

    def delete(self):
        """Delete this node"""
        if self.parent:  # only if parent is set
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
            "type": "DIR" if self.is_dir else "FILE",
            "name": self.name,
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
        """Set `ro`, `size_` and `m_time` attributes on this file
        according to `abs_path` file on storage.
        """
        stats = stat(abs_path)
        self.attrs["ro"] = not access(abs_path, W_OK)
        if not self.is_dir:
            self.attrs["size"] = stats.st_size
        m_datetime = datetime.fromtimestamp(stats.st_mtime)
        m_time = datetime.timetuple(m_datetime)[:6]
        self.attrs["m_time"] = m_time


class Mount:
    """Represent a mountpoint"""
    def __init__(self, tree: File, mountpoint: str, abs_path_storage: str,
                 to_inotify=True):
        """
        Initialize a Mount.

        :param tree: tree of File instances
        :param mountpoint: mount point on the virtual FS
        :param abs_path_storage: absolute path on the physical storage
        :param to_inotify: whether to handle this mount point using inotify
        """
        self.tree = tree
        self.mountpoint = mountpoint
        self.path_storage = abs_path_storage
        self.to_inotify = to_inotify

    def __str__(self):
        return f"Mount({self.mountpoint} -> {self.path_storage})"

    __repr__ = __str__


class InvalidMountpointError(ValueError):
    """Mountpoint is not valid"""
    ...


class Filesystem:
    """Model a collection of Files (which are grouped in to trees).
    This is flat like the DOS filesystem, therefore unlike in UNIX OSes
    trees cannot be nested in each other.

    A filesystem translates from physical representation on the storage to
    virtual organised by mount (points). This virtual one is then
    sent to Connect.
    """
    def __init__(self, sep: str = "/", event_cb: EventCallback = None):
        """Create a Filesystem (FS).

        :sep: Separator on the FS
        :event_cb: SDK's Printer.event_cb method. If set, the FS
            will call callback to put events to event queue on mount/umount
            operations and the InotifyHandler on changes to the FS.
        """
        self.sep = sep
        self.mounts: typing.Dict[str, Mount] = {}
        self.event_cb = event_cb

    def mount(self, name: str, tree: File, storage_path: str = "",
              to_inotify=True):
        """Mount the a tree under a mountpoint.

        :param name: The mountpoint
        :param tree: The tree of `File` instances to be mounted
        :param storage_path: Path on storage
        :param to_inotify: Whether to use inotify on this mountpoint or not
        :raises InvalidMountpointError: If the mountpoint is already used,
            or when it contains `self.sep` or when the `name` is empty or
            `self.sep` only.
        """
        if not name:
            raise InvalidMountpointError("Mountpoint cannot be empty")

        if self.sep in name:
            msg = f"Mountpoints cannot contain {self.sep}"
            raise InvalidMountpointError(msg)

        if name in self.mounts:
            raise InvalidMountpointError(f"`{name}` is already used")

        self.mounts[name] = Mount(tree, name, storage_path, to_inotify)

        # send MEDIUM_INSERTED event
        if self.event_cb:
            payload = {"root": f"{self.sep}{name}", "files": tree.to_dict()}
            self.connect_event(const.Event.MEDIUM_INSERTED, payload)

    def umount(self, name: str):
        """Umount a mountpoint.

        :param name: The mountpoint
        :raises InvalidMountpointError: if `name` is not mounted
        """
        if name not in self.mounts:
            msg = f"`{name}` is not used as a mountpoint"
            raise InvalidMountpointError(msg)

        del self.mounts[name]

        # send MEDIUM_EJECTED event
        if self.event_cb:
            payload = {
                "root": f"{self.sep}{name}",
            }
            self.connect_event(const.Event.MEDIUM_EJECTED, payload)

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

        :return: dictionary representation of the Filesystem.
        """
        root = {
            "type": "DIR",
            "name": "/",
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
        dirpath = path.abspath(dirpath)
        if not dirpath.endswith(path.sep):
            dirpath += path.sep

        root = File(mountpoint, is_dir=True)
        root.set_attrs(dirpath)

        for abs_dir, dirs, files in walk(dirpath):
            dirname = abs_dir[len(dirpath):]
            if not dirname:
                parent = root
            else:
                parent = root.get(dirname.split(path.sep))

            for name in dirs:
                node = parent.add(name, is_dir=True)
                node.set_attrs(path.join(abs_dir, name))
            for name in files:
                node = parent.add(name)
                node.set_attrs(path.join(abs_dir, name))

        # mount
        self.mount(mountpoint, root, dirpath)

    def connect_event(self, event: const.Event, data: dict):
        """Send an event to connect if `self.events` is set"""
        if self.event_cb:
            self.event_cb(event, const.Source.WUI, **data)


class InotifyHandler:
    """This handler is initialised with a Filesystem instance and
    using it it makes sure that all its mounts' `tree`s are updated on changes
    on the physical storage"""

    WATCH_FLAGS = flags.CREATE | flags.DELETE | flags.MODIFY | \
        flags.DELETE_SELF | flags.MOVED_TO | flags.MOVED_FROM | \
        flags.MOVE_SELF

    def __init__(self, fs: Filesystem):
        # pylint: disable=invalid-name
        self.fs = fs
        self.inotify = INotify()
        self.wds: typing.Dict[int, str] = {}  # watch descriptors
        # init mount watches
        for mount in self.fs.mounts.values():
            if mount.to_inotify:
                self.__init_wd(mount.path_storage, mount.tree)

    def __init_wd(self, path_storage, node):
        # pylint: disable=invalid-name
        abs_dir = path.join(path_storage, path.sep.join(node.abs_parts()))
        try:
            wd = self.inotify.add_watch(abs_dir, self.WATCH_FLAGS)
            self.wds[wd] = abs_dir
            log.debug("Added watch (%s) for %s", wd, abs_dir)
            for child in node.children.values():
                if child.is_dir:
                    self.__init_wd(path_storage, child)
        except PermissionError:
            pass

    def filter_delete_events(self, events):
        """Because we are adding inotify watch descriptors to all
        subdirectories, ignore all DELETE events if they are
        followed by any DElETE on any of their parents."""
        # TODO add test
        ignorelist = [False] * len(events)
        rev_events = list(reversed(events))  # we are examining from the end
        for i, event in enumerate(rev_events):
            log.debug("Event: %d %s (%s), %s", i, event,
                      [f.name for f in flags.from_mask(event.mask)],
                      self.wds[event.wd])
            if (event.mask & flags.ISDIR and event.mask & flags.DELETE) \
                    or event.mask & flags.DELETE_SELF:
                log.debug(" found DEL at %d", i)
                event_dir = self.wds[event.wd].rstrip(path.sep)
                if event.mask & flags.DELETE:
                    event_dir = path.join(event_dir, event.name)
                for j, nxt in enumerate(rev_events[i + 1:]):
                    if (nxt.mask & flags.DELETE and nxt.mask & flags.ISDIR) \
                            or nxt.mask & flags.DELETE_SELF:
                        sub_event_dir = self.wds[nxt.wd].rstrip(path.sep)
                        if nxt.mask & flags.DELETE:
                            sub_event_dir = path.join(sub_event_dir, nxt.name)
                        if sub_event_dir.startswith(event_dir):
                            # i+1: 0 is the DELETE_SELF/DELETE (DIR) event,
                            #  1 is the following event
                            ignorelist[i + 1 + j] = True
        log.debug("ignore: %s", list(enumerate(ignorelist)))
        result = [e for (e, i) in zip(rev_events, ignorelist) if not i]
        return result[::-1]

    def __call__(self, timeout=0):
        """Process inotify events. This picks the proper `process_$FLAG`
        handler method and executes it with the absolute_path of the
        affected file as argument.
        """
        events = self.inotify.read(timeout=timeout)
        events = self.filter_delete_events(events)
        for event in events:
            for flag in flags.from_mask(event.mask):
                # remove wds that are no longer needed
                if flag.name == "IGNORED":
                    del self.wds[event.wd]
                    continue
                # ignore non watched events
                if not self.WATCH_FLAGS & flag:
                    log.debug("Ignoring %s", flag.name)
                    continue
                parent_dir = self.wds[event.wd]
                abs_path = path.join(parent_dir, event.name)
                log.debug("Flag: %s %s %s", flag.name, abs_path, event)
                handler = self.HANDLERS[flag.name]
                log.debug("Calling %s: %s", handler, abs_path)
                handler(self, abs_path, event.mask & flags.ISDIR)

    # pylint: disable=inconsistent-return-statements
    def mount_for(self, abs_path):
        """Find the proper mount for the `path` in self.fs"""
        for mount in self.fs.mounts.values():
            if abs_path.startswith(mount.path_storage):
                return mount

    def __rel_path_parts(self, abs_path, mount) -> typing.List[str]:
        """
        Return the relative part of `abs_path` minus the `mount` split by
        self.fs.sep

        :param abs_path: path
        :param mount: mount point - beginning of the abs_path
        :return: list of parts of the relative path
        """
        rel_path = abs_path[len(mount.path_storage):]
        return rel_path.rstrip(self.fs.sep).split(self.fs.sep)

    def process_create(self, abs_path, is_dir):
        """Handle CREATE inotify signal by creating the file/directory
        determined by `abs_path`. `is_dir` is set, if the event was generated
        for a directory.
        """
        # pylint: disable=unused-argument
        mount = self.mount_for(abs_path)
        parts = self.__rel_path_parts(abs_path, mount)
        *parent, name = parts
        node = mount.tree.get(parent).add(name, is_dir=is_dir)
        node.set_attrs(abs_path)
        if is_dir:
            # add inotify watch
            self.__init_wd(mount.path_storage, node)  # add inotify watch
        self.send_file_changed(file=node,
                               new_path=node.abs_path(mount.mountpoint))

    def process_delete(self, abs_path, is_dir):
        """Handle DELETE inotify signal by deleting the node
        indicated by `abs_path`.
        """
        # pylint: disable=unused-argument
        mount = self.mount_for(abs_path)
        parts = self.__rel_path_parts(abs_path, mount)
        # top level dir (mount.tree) was deleted or unmounted
        if abs_path == mount.path_storage:
            node = mount.tree
            node.children = dict()
            node.attrs = dict()
            path_ = node.abs_path(mount.mountpoint)
            self.send_file_changed(old_path=path_, new_path=path_, file=node)
        else:
            # some watched directory other than top level was deleted
            node = mount.tree.get(parts)
            node.delete()
            path_ = node.abs_path(mount.mountpoint)
            self.send_file_changed(old_path=path_)

    def process_modify(self, abs_path, is_dir):
        """Process MODIFY inotify signal by updating the
        attributes for a file indicated by `abs_path`.
        """
        # pylint: disable=unused-argument
        mount = self.mount_for(abs_path)
        parts = self.__rel_path_parts(abs_path, mount)
        node = mount.tree.get(parts)
        node.set_attrs(abs_path)
        path_ = node.abs_path(mount.mountpoint)
        self.send_file_changed(old_path=path_, new_path=path_, file=node)

    def send_file_changed(self,
                          old_path: str = None,
                          new_path: str = None,
                          file: File = None):
        """If self.fs.events is set, put FIlE_CHANGED event to event queue.

        :raises ValueError: if both old_path and new_path are not set
        """
        if not old_path and not new_path:
            msg = "At least one of (old_path, new_path) must be set"
            raise ValueError(msg)
        data = {
            "old_path": old_path,
            "new_path": new_path,
        }
        if file:
            data["file"] = file.to_dict()
        self.fs.connect_event(const.Event.FILE_CHANGED, data)

    # handlers for inotify file events
    HANDLERS = {
        "CREATE": process_create,
        "MODIFY": process_modify,
        "DELETE": process_delete,
        "MOVED_TO": process_create,
        "MOVED_FROM": process_delete,
        "DELETE_SELF": process_delete,
        "MOVE_SELF": process_delete,
    }

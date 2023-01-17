"""File management"""

import os
import typing
import weakref
from logging import getLogger
from time import time, sleep
from os import path, access, W_OK, stat, walk
from collections import Counter
from typing import Optional
from inotify_simple import INotify, flags  # type: ignore

from . import const
from .metadata import get_metadata, UnknownGcodeFileType
from .models import EventCallback

ROOT = '__ROOT__'
log = getLogger("connect-printer")

# pylint: disable=fixme
# pylint: disable=too-few-public-methods
# NOTE: Temporary for pylint with python3.9
# pylint: disable=unsubscriptable-object


# https://stackoverflow.com/a/18715879
def common_start(sa, sb):
    """ returns the longest common substring from the beginning of sa and sb"""

    # pylint: disable=invalid-name
    def _iter():
        for a, b in zip(sa, sb):
            if a == b:
                yield a
            else:
                return

    return ''.join(_iter())


def delete(abs_path, is_dir):
    """Delete file or folder.

    :param abs_path: absolute path
    :param is_dir: True if folder
    """
    if os.path.exists(abs_path):
        if is_dir:
            os.rmdir(abs_path)
        else:
            os.unlink(abs_path)
    else:
        raise FileNotFoundError(f"{abs_path}."
                                f" File or folder doesn't exist.")


class File:
    """A node of a Filesystem representing either a file or a folder"""
    def __init__(self,
                 name: str,
                 is_dir: bool = False,
                 parent: Optional["File"] = None,
                 **attrs):
        """Create a File object

        :param name: Filename
        :param is_dir: Flag whether this is a folder
        :param parent: Parent for this File, which itself is a
            File(is_dir=True)
        :param attrs: Any attributes for the file you want to store. File's
            to_dict() method add `ro`, `m_timestamp` and `size`
            attributes, if it finds them.
        """
        self.name = name
        self.is_dir = is_dir
        if parent is not None:
            self._parent: typing.Optional["File"] = weakref.proxy(parent)
        else:
            self._parent = None
        self.attrs = attrs
        self.children: dict = {}

    @property
    def size(self):
        """Return `size` from `self.attrs` for a FILE or compute it for
        a folder.
        """
        if not self.is_dir:  # file
            return self.attrs.get('size', 0)

        # folder
        size = 0
        for child in self.children.values():
            size += child.size
        return size

    @size.setter
    def size(self, value):
        self.attrs['size'] = value

    @property
    def parent(self):
        """Gets a parent node"""
        return self._parent

    @parent.setter
    def parent(self, parent):
        """
        Sets a parent node, uses weakref, to stop the creation of a refloop
        """
        self._parent = weakref.proxy(parent)

    def add(self, name: str, is_dir: bool = False, **attrs):
        """Add a file to this File's children.
        Note that `self` must be a folder.

        :param name: name of the file
        :param is_dir: Is this a folder?
        :param attrs: arbitrary File attributes
        :raise ValueError: if self is not a folder
        :return the added file.
        """
        if not self.is_dir:
            raise ValueError("You can add only to directories")
        node = File(name, is_dir=is_dir, parent=self, **attrs)
        # Ignore hidden files and folders .<filename> / .<foldername>
        if not node.name.startswith("."):
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

        :param prefix: prefix as a path to be used for absolute path generation
        """
        if not prefix.startswith(path.sep):
            prefix = path.sep + prefix
        return path.join(prefix, path.sep.join(self.abs_parts()))

    def delete(self):
        """Delete this node"""
        if self.parent:  # only if parent is set
            del self.parent.children[self.name]

    def pprint(self, file=None, _prefix="", _last=True, _first=True):
        """Pretty print the File as  a tree. `self` should be a folder
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
        if self.is_dir:
            file_type = const.FileType.FOLDER.value
        else:
            if self.name.endswith(const.GCODE_EXTENSIONS):
                file_type = const.FileType.PRINT_FILE.value
            elif self.name.endswith(const.FIRMWARE_EXTENSION):
                file_type = const.FileType.FIRMWARE.value
            else:
                file_type = const.FileType.FILE.value

        result = {
            "type": file_type,
            "name": self.name,
        }
        for attr in ("ro", "m_timestamp"):
            if attr in self.attrs:
                result[attr] = self.attrs[attr]
        result['size'] = self.size
        children = [child.name for child in self.children.values()]
        if self.is_dir:
            result['children'] = children
        return result

    def to_dict_legacy(self):
        """:return `self` in the format for Connect Backend
        This is a deprecated legacy code"""
        if self.is_dir:
            file_type = const.FileType.FOLDER.value
        else:
            if self.name.endswith(const.GCODE_EXTENSIONS):
                file_type = const.FileType.PRINT_FILE.value
            elif self.name.endswith(const.FIRMWARE_EXTENSION):
                file_type = const.FileType.FIRMWARE.value
            else:
                file_type = const.FileType.FILE.value

        result = {
            "type": file_type,
            "name": self.name,
        }
        for attr in ("ro", "m_timestamp"):
            if attr in self.attrs:
                result[attr] = self.attrs[attr]
        result['size'] = self.size
        children = [child.to_dict_legacy() for child in self.children.values()]
        if self.is_dir:
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
        """Set `ro`, `size_` and `m_timestamp` attributes on this file
        according to `abs_path` file on storage.
        """
        stats = stat(abs_path)
        self.attrs["ro"] = not access(abs_path, W_OK)
        if not self.is_dir:
            self.size = stats.st_size
        self.attrs["m_timestamp"] = int(stats.st_mtime)


class Storage:
    """Represent a storage"""
    def __init__(self,
                 tree: File,
                 storage: str,
                 abs_path_storage: Optional[str] = None,
                 use_inotify=True):
        """
        Initialize a Storage.

        :param tree: tree of File instances
        :param storage: storage on the virtual FS
        :param abs_path_storage: absolute path on the physical storage
        :param use_inotify: whether to handle this storage using inotify
        """
        if use_inotify:
            if not abs_path_storage:
                msg = "`use_inotify` requires `abs_path_storage` to be set"
                raise ValueError(msg)
        # TODO check other storage if there is already abs_path_storage used
        self.tree = tree
        self.storage = storage
        self.path_storage = abs_path_storage
        self.use_inotify = use_inotify
        self.last_updated = time()

    def get_space_info(self):
        """Returns free space of storage in bytes"""
        if os.path.exists(self.path_storage):
            path_ = os.statvfs(self.path_storage)
            free_space = path_.f_bavail * path_.f_bsize
            total_space = path_.f_blocks * path_.f_bsize

            space_info = {"free_space": free_space, "total_space": total_space}
            return space_info
        return {}

    def to_dict(self):
        """Returns tree in a format for Connect. Add attributes free_space and
        total_space to tree, if available"""
        if self.tree:
            tree = self.tree.to_dict()
        space_info = self.get_space_info()
        free_space = space_info.get("free_space")
        total_space = space_info.get("total_space")
        if free_space:
            tree["free_space"] = free_space
        if total_space:
            tree["total_space"] = total_space
        return tree

    def to_dict_legacy(self):
        """Returns tree in a format for Connect. Add attributes free_space and
        total_space to tree, if available
        This is a deprecated legacy code"""
        if self.tree:
            tree = self.tree.to_dict_legacy()
        space_info = self.get_space_info()
        free_space = space_info.get("free_space")
        total_space = space_info.get("total_space")
        if free_space:
            tree["free_space"] = free_space
        if total_space:
            tree["total_space"] = total_space
        return tree

    def __str__(self):
        return f"Storage({self.storage} -> {self.path_storage})"

    __repr__ = __str__


class InvalidStorageError(ValueError):
    """Storage is not valid"""


class Filesystem:
    """Model a collection of Files (which are grouped in to trees).
    This is flat like the DOS filesystem, therefore unlike in UNIX OSes
    trees cannot be nested in each other.

    A filesystem translates from physical representation on the storage to
    virtual. This virtual one is then sent to Connect.
    """
    def __init__(self,
                 sep: str = "/",
                 event_cb: Optional[EventCallback] = None):
        """Create a Filesystem (FS).

        :sep: Separator on the FS
        :event_cb: SDK's Printer.event_cb method. If set, the FS
            will call callback to put events to event queue on attach/detach
            operations and the InotifyHandler on changes to the FS.
        """
        self.sep = sep
        self.storage_dict: typing.Dict[str, Storage] = {}
        self.checked_files: typing.List = []
        self.event_cb = event_cb

    def attach(self,
               name: str,
               tree: File,
               storage_path: str = "",
               use_inotify=True):
        """Attach the tree under a storage.

        :param name: The storage
        :param tree: The tree of `File` instances to be attached
        :param storage_path: Path on storage
        :param use_inotify: Whether to use inotify on this storage or not
        :raises InvalidStorageError
        : If the storage is already used,
            or when it contains `self.sep` or when the `name` is empty or
            `self.sep` only.
        """
        if not name:
            raise InvalidStorageError("Storage cannot be empty")

        if name == '/':
            name = ROOT

        if self.sep in name:
            msg = f"Storage cannot contain {self.sep}"
            raise InvalidStorageError(msg)

        if name in self.storage_dict:
            raise InvalidStorageError(f"`{name}` is already used")

        self.storage_dict[name] = Storage(tree, name, storage_path,
                                          use_inotify)

        # send MEDIUM_INSERTED event
        if self.event_cb:
            payload = {
                "root": f"{self.sep}{name}",
                "files": self.storage_dict[name].to_dict()
            }
            self.connect_event(const.Event.MEDIUM_INSERTED, payload)

    def detach(self, name: str):
        """Detach a storage.

        :param name: The storage
        :raises InvalidStorageError
        : if `name` is not atached
        """
        if name not in self.storage_dict:
            msg = f"`{name}` is not used as a storage"
            raise InvalidStorageError(msg)

        del self.storage_dict[name]

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
        storage, *parts = abs_path.split(self.sep)

        if ROOT in self.storage_dict:
            abs_path = '/'.join([ROOT, abs_path])
            storage, *parts = abs_path.split(self.sep)
            if storage not in self.storage_dict:
                return None
        elif storage not in self.storage_dict:
            return None

        return self.storage_dict[storage].tree.get(parts)

    @staticmethod
    def update(abs_paths: list, abs_storage: str, node: Optional[File] = None):
        """Update storage.tree structure.

        Add the nearest part of real file system to tree.

        Example:
        Filesystem.update(['/tmp/tmpvbqhald4/folder/a'],
        '/tmp/tmpvbqhald4/folder', File('test'))

        result is node File('a') is add to File('test')

        :param abs_paths: absolute paths
        :param abs_storage: absolute path to storage
        :param node: instance of File
        """
        if node:
            relative_paths = InotifyHandler.get_relative_paths(
                abs_storage, abs_paths)

            for item in relative_paths:
                if os.sep not in item and os.sep != item:
                    node.add(item, is_dir=True)

    def get_os_path(self, abs_path):
        """Gets the OS file path of the file specified by abs_path"""
        file = self.get(abs_path)
        abs_path = abs_path.strip(self.sep)
        storage_name = abs_path.split(self.sep)[0]
        storage = self.storage_dict[storage_name]
        return file.abs_path(storage.path_storage)

    def to_dict(self):
        """Returns all the tree in the representation Connect requires

        :return: dictionary representation of the Filesystem.
        """
        root = {"type": "FOLDER", "name": "/", "ro": True, "children": []}

        if ROOT in self.storage_dict:
            root = self.storage_dict[ROOT].to_dict()

        root["children"].extend([
            v.to_dict()["name"] for k, v in self.storage_dict.items()
            if k != ROOT
        ])
        return root

    def to_dict_legacy(self):
        """Returns all the tree in the representation Connect requires
        This is a deprecated legacy code

        :return: dictionary representation of the Filesystem.
        """
        root = {"type": "FOLDER", "name": "/", "ro": True, "children": []}

        if ROOT in self.storage_dict:
            root = self.storage_dict[ROOT].to_dict()

        root["children"].extend([
            v.to_dict_legacy() for k, v in self.storage_dict.items()
            if k != ROOT
        ])
        return root

    def from_dir(self, dirpath: str, storage: str):
        """Initialize a (File) tree from `dirpath` and attach it.

        :param dirpath: The folder on store from which to create the FS
        :param storage: Storage
        """
        # normalize dirpath
        dirpath = path.abspath(dirpath)
        if not dirpath.endswith(path.sep):
            dirpath += path.sep

        root = File(storage, is_dir=True)
        root.set_attrs(dirpath)

        for abs_dir, dirs, files in walk(dirpath):
            dirname = abs_dir[len(dirpath):]

            # skip hidden folders
            if dirname.startswith("."):
                continue

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

        self.attach(storage, root, dirpath)

    def connect_event(self, event: const.Event, data: dict):
        """Send an event to connect if `self.events` is set"""
        if self.event_cb:
            self.event_cb(event, const.Source.WUI, **data)

    def wait_until_path(self, path_, wait_timeout=-1):
        """Wait for the max time until a file is appended to the path
        by inotify."""
        i = 0
        while wait_timeout < 0 or i < wait_timeout:
            if self.get(path_):
                return True
            sleep(0.1)
            i += 0.1
        return False


class InotifyHandler:
    """This handler is initialised with a Filesystem instance and
    using it makes sure that all its storage' `trees are updated on changes
    on the physical storage"""

    WATCH_FLAGS = (flags.CREATE | flags.DELETE | flags.MODIFY
                   | flags.DELETE_SELF | flags.MOVED_TO | flags.MOVED_FROM
                   | flags.MOVE_SELF)

    def __init__(self, fs: Filesystem):
        # pylint: disable=invalid-name
        self.fs = fs
        self.inotify = INotify()
        self.wds: typing.Dict[int, str] = {}  # watch descriptors
        # init storage watches
        for storage in self.fs.storage_dict.values():
            if storage.use_inotify and storage.path_storage is not None:
                self.__init_wd(storage.path_storage, init=True)

    def create_cache(self, new_path):
        """Creates the cache file"""
        path_ = os.path.join(self.get_abs_os_path(new_path))
        if os.path.exists(path_):
            try:
                meta = get_metadata(path_)
                meta.save_cache()
            except UnknownGcodeFileType:
                pass

    def delete_cache(self, old_path):
        """When a file is deleted, the cache file is deleted"""
        path_ = os.path.split(self.get_abs_os_path(old_path))
        cache_path = path_[0] + "/." + path_[1] + ".cache"
        if os.path.exists(cache_path):
            os.unlink(cache_path)

    def update_watch_dir(self, abs_paths: list):
        """Check if the path is watched and if not, it's added.

        :param abs_paths: list of absolute paths
        """
        for abs_path in abs_paths:
            if abs_path not in self.wds.values():
                watch_dir_id = self.inotify.add_watch(abs_path,
                                                      self.WATCH_FLAGS)
                self.wds[watch_dir_id] = abs_path
                log.debug("Added watch (%s) for %s", watch_dir_id, abs_path)

    @staticmethod
    def get_relative_paths(relative_point: str, abs_paths: list) -> list:
        """Returns paths relative to the relative_point.

        >>> InotifyHandler.get_relative_paths('/tmp/r', ['/tmp/r/folder'])
        ['folder']

        :param relative_point: relativ point
        :param abs_paths: absolute patths
        """
        relative_paths = [
            os.path.relpath(abs_path, start=relative_point)
            for abs_path in abs_paths if relative_point in abs_path
        ]

        if '.' in relative_paths:
            relative_paths.remove('.')

        return relative_paths

    def __init_wd(self,
                  abs_storage: str,
                  node: Optional[File] = None,
                  init=True):
        """Update all dirs from root to bottom.

        Add all dirs to inotify to watcher.

        :param abs_storage: absolute path to storage
        :param node: instance of File
        :param init: wheter the function is called on init or create handler
        """
        walk_storage = os.walk(abs_storage, topdown=True)

        if init:
            abs_paths = [abs_path for abs_path, _, _ in walk_storage]
            self.update_watch_dir(abs_paths)
            Filesystem.update(abs_paths, abs_storage, node)

        else:
            dir_paths = []
            file_paths = []

            for dir_path, _, file_names in walk_storage:
                dir_paths.append(dir_path)
                for file_name in file_names:
                    if not file_name.startswith('.'):
                        file_path = path.join(dir_path, file_name)
                        file_paths.append(file_path)

            self.update_watch_dir(dir_paths)
            Filesystem.update(dir_paths, abs_storage, node)

            for file_path in file_paths:
                if file_path not in self.fs.checked_files:
                    self.process_create(file_path, is_dir=False)
                    self.fs.checked_files.append(file_path)

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
        # NOTE DBG: log.debug("ignore: %s", list(enumerate(ignorelist)))
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
            parent_dir = self.wds[event.wd]
            for storage in self.fs.storage_dict.values():
                if parent_dir.startswith(storage.path_storage):
                    storage.last_updated = time()
            for flag in flags.from_mask(event.mask):
                # remove wds that are no longer needed
                if flag.name == "IGNORED":
                    del self.wds[event.wd]
                    continue
                # ignore non watched events
                if not self.WATCH_FLAGS & flag:
                    log.debug("Ignoring %s", flag.name)
                    continue

                abs_path = path.join(parent_dir, event.name)
                # Ignore hidden files .<filename>
                if not event.name.startswith("."):
                    log.debug("Flag: %s %s %s", flag.name, abs_path, event)
                    handler = self.HANDLERS[flag.name]
                    log.debug("Calling %s: %s", handler, abs_path)
                    handler(self, abs_path, event.mask & flags.ISDIR)

    def get_abs_os_path(self, relative_path_param):
        """Relative path to os path.

        '/test/test_dir' -> '/tmp/tmpbvyl_9mr/test_dir'
        """
        normal_path = os.path.normpath(relative_path_param)
        split_path = normal_path.split(os.sep)
        split_path.remove('')
        try:
            split_path[0] = self.fs.storage_dict[split_path[0]].path_storage
        except KeyError as error:
            raise FileNotFoundError("Storage doesn't exist.") from error

        return os.path.join(*split_path)

    # pylint: disable=inconsistent-return-statements
    def attach_for(self, abs_path):
        """Find the proper storage for the `path` in self.fs"""
        storage_list = []
        # exclude non-applicable storage
        for storage in self.fs.storage_dict.values():
            if not storage.use_inotify:
                continue
            if not abs_path.startswith(storage.path_storage):
                continue
            storage_list.append(storage)
        # return the storage with the longest match
        # NOTE this is still not really deterministic. Consider the scenario
        # storage1:       /tmp/
        # storage2:       /tmp/a
        # abs_path:     /tmp/a/d
        # which one to take in this case?
        counter = Counter()
        for storage in storage_list:
            overlap = common_start(storage.path_storage.rstrip(path.sep),
                                   abs_path)
            counter[storage] = len(overlap)
        result = counter.most_common()[0]
        assert result[1] > 0
        return result[0]

    def __rel_path_parts(self, abs_path, storage) -> typing.List[str]:
        """
        Return the relative part of `abs_path` minus the `storage` split by
        self.fs.sep

        :param abs_path: path
        :param storage: storage - beginning of the abs_path
        :return: list of parts of the relative path
        """
        rel_path = abs_path[len(storage.path_storage):]
        return rel_path.rstrip(self.fs.sep).split(self.fs.sep)

    def process_create(self, abs_path, is_dir):
        """Handle CREATE inotify signal by creating the file/folder
        determined by `abs_path`. `is_dir` is set, if the event was generated
        for a folder.
        """
        # pylint: disable=unused-argument
        base_storage = self.attach_for(abs_path)
        parts = self.__rel_path_parts(abs_path, base_storage)
        *parent, name = parts
        parent = base_storage.tree.get(parent)
        if not parent:
            return
        node = parent.add(name, is_dir=is_dir)
        node.set_attrs(abs_path)
        if is_dir:
            # add inotify watch
            self.__init_wd(abs_path, node, init=False)  # add inotify watch
        else:
            self.create_cache(node.abs_path(base_storage.storage))
        self.send_file_changed(
            file=node,
            new_path=node.abs_path(base_storage.storage),
            free_space=base_storage.get_space_info().get("free_space"))

    def process_delete(self, abs_path, is_dir):
        """Handle DELETE inotify signal by deleting the node
        indicated by `abs_path`.
        """
        # pylint: disable=unused-argument
        base_storage = self.attach_for(abs_path)
        parts = self.__rel_path_parts(abs_path, base_storage)
        # top level dir (storage.tree) was deleted or detached
        if abs_path == base_storage.path_storage:
            node = base_storage.tree
            node.children = {}
            node.attrs = {}
            path_ = node.abs_path(base_storage.storage)
            self.delete_cache(path_)
            # TODO: Why is this there?
            # self.create_cache(path_)
            self.send_file_changed(
                old_path=path_,
                new_path=path_,
                file=node,
                free_space=base_storage.get_space_info().get("free_space"))
        else:
            # some watched folder other than top level was deleted
            node = base_storage.tree.get(parts)
            node.delete()
            path_ = node.abs_path(base_storage.storage)
            self.delete_cache(path_)
            self.send_file_changed(
                old_path=path_,
                free_space=base_storage.get_space_info().get("free_space"))

    def process_modify(self, abs_path, is_dir):
        """Process MODIFY inotify signal by updating the
        attributes for a file indicated by `abs_path`.
        """
        # pylint: disable=unused-argument
        base_storage = self.attach_for(abs_path)
        parts = self.__rel_path_parts(abs_path, base_storage)
        node = base_storage.tree.get(parts)
        node.set_attrs(abs_path)
        path_ = node.abs_path(base_storage.storage)
        self.send_file_changed(
            old_path=path_,
            new_path=path_,
            file=node,
            free_space=base_storage.get_space_info().get("free_space"))

    def send_file_changed(self,
                          old_path: Optional[str] = None,
                          new_path: Optional[str] = None,
                          file: Optional[File] = None,
                          free_space=None):
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
        if free_space is not None:
            data["free_space"] = free_space
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

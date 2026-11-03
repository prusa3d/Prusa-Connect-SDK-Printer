"""Listing of folders and files for the Lazy File Protocol.

Everything here reads the real disk, nothing is cached.
"""
import os
from os import W_OK, access, path

from gcode_metadata import UnknownGcodeFileType, get_metadata

from . import const
from .command import CommandRejected

# Maximum number of items of a folder, which can be sent in one FILE_INFO.
# Larger folders are rejected with REASON_OVERSIZED.
MAX_FOLDER_ITEMS = 20000

# Reasons of rejecting SEND_FILE_INFO. They are part of the contract with
# Connect, so they must not be changed or translated.
REASON_NOT_FOUND = "File not found"
REASON_OVERSIZED = "Oversized command"
REASON_FORBIDDEN = "Forbidden path"


def get_type_by_name(name: str, is_dir: bool):
    """Return file type of file/folder by its name"""
    if is_dir:
        return const.FileType.FOLDER

    filename = name.lower()
    if filename.endswith(const.GCODE_EXTENSIONS):
        return const.FileType.PRINT_FILE

    if filename.endswith(const.FIRMWARE_EXTENSION):
        return const.FileType.FIRMWARE

    return const.FileType.FILE


def is_inside(os_path: str, storage_path: str) -> bool:
    """Check that `os_path` doesn't lead outside of `storage_path`, even
    through symlinks"""
    storage_real = path.realpath(storage_path)
    real = path.realpath(os_path)
    return real == storage_real or real.startswith(
        storage_real.rstrip(path.sep) + path.sep)


def folder_info(virtual_path: str, storage_path: str, os_path: str,
                stats) -> dict:
    """Return list of direct children of a folder"""
    children: list = []
    with os.scandir(os_path) as entries:
        for entry in entries:
            if entry.name.startswith("."):
                continue
            if entry.is_symlink() and not is_inside(entry.path, storage_path):
                continue
            try:
                is_dir = entry.is_dir()
                entry_stats = entry.stat()
            except OSError:  # e.g. a broken link or a removed file
                continue
            if len(children) >= MAX_FOLDER_ITEMS:
                raise CommandRejected(REASON_OVERSIZED)
            child = {
                "name": entry.name,
                "type": get_type_by_name(entry.name, is_dir).value,
                "read_only": not access(entry.path, W_OK),
                "m_timestamp": int(entry_stats.st_mtime),
            }
            if not is_dir:
                child["size"] = entry_stats.st_size
            children.append(child)
    return {
        "type": const.FileType.FOLDER.value,
        "path": virtual_path,
        "read_only": not access(os_path, W_OK),
        "m_timestamp": int(stats.st_mtime),
        "children": children,
    }


def file_info(virtual_path: str, os_path: str, stats) -> dict:
    """Return info and metadata of a file"""
    info = {
        "type": get_type_by_name(os_path, False).value,
        "path": virtual_path,
        "read_only": not access(os_path, W_OK),
        "m_timestamp": int(stats.st_mtime),
        "size": stats.st_size,
    }
    try:
        meta = get_metadata(os_path)
    except (UnknownGcodeFileType, FileNotFoundError):
        return info
    info.update(meta.data)

    # include the biggest thumbnail, if available
    if meta.thumbnails:
        biggest = b""
        for data in meta.thumbnails.values():
            if len(data) > len(biggest):
                biggest = data
        info["preview"] = biggest.decode()
    return info


def node_info(virtual_path: str, node) -> dict:
    """Return FILE_INFO data of a node of the tree (`files.File`).

    It is for storages which can't be read from the disk. Only the
    attributes stored in the node are available, there are no metadata.
    """
    info = {
        "type": get_type_by_name(node.name, node.is_dir).value,
        "path": virtual_path,
    }
    for attr in ("read_only", "m_timestamp"):
        if attr in node.attrs:
            info[attr] = node.attrs[attr]
    if not node.is_dir:
        info["size"] = node.size
        return info

    children: list = []
    for child in node.children.values():
        if len(children) >= MAX_FOLDER_ITEMS:
            raise CommandRejected(REASON_OVERSIZED)
        item = {
            "name": child.name,
            "type": get_type_by_name(child.name, child.is_dir).value,
        }
        for attr in ("read_only", "m_timestamp"):
            if attr in child.attrs:
                item[attr] = child.attrs[attr]
        if not child.is_dir:
            item["size"] = child.size
        children.append(item)
    info["children"] = children
    return info

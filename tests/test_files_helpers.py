"""Tests for the helper functions of the files module"""
import pytest

from prusa.connect.printer.files import common_start, delete


def test_common_start():
    """The longest common beginning of two strings"""
    assert common_start("/a/b/c", "/a/b/d") == "/a/b/"
    assert common_start("abc", "xyz") == ""


def test_delete_file(tmp_path):
    """A file is deleted"""
    path = tmp_path / "file.txt"
    path.write_text("x")
    delete(str(path), is_dir=False)
    assert not path.exists()


def test_delete_directory(tmp_path):
    """An empty directory is deleted, a non-empty one only when forced"""
    empty = tmp_path / "empty"
    empty.mkdir()
    delete(str(empty), is_dir=True)
    assert not empty.exists()

    full = tmp_path / "full"
    full.mkdir()
    (full / "file.txt").write_text("x")
    with pytest.raises(OSError):
        delete(str(full), is_dir=True)
    delete(str(full), is_dir=True, force=True)
    assert not full.exists()


def test_delete_missing(tmp_path):
    """Deleting something which does not exist fails"""
    with pytest.raises(FileNotFoundError):
        delete(str(tmp_path / "missing"), is_dir=False)

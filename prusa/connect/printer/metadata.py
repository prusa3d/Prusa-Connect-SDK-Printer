"""Metadata parser for Prusa Slicer gcode files. Extracts preview pictures
as well.
"""

import base64
import json
import re
import os
import zipfile
from typing import Dict, Any, List
from logging import getLogger
from .const import GCODE_EXTENSIONS

log = getLogger("connect-printer")
RE_ESTIMATED = re.compile(r"((?P<days>[0-9]+)d\s*)?"
                          r"((?P<hours>[0-9]+)h\s*)?"
                          r"((?P<minutes>[0-9]+)m\s*)?"
                          r"((?P<seconds>[0-9]+)s)?")


class UnknownGcodeFileType(ValueError):
    # pylint: disable=missing-class-docstring
    ...


def thumbnail_from_bytes(data_input):
    """Parse thumbnail from bytes to string format because
    of JSON serialization requirements"""
    converted_data = dict()
    for key, value in data_input.items():
        if isinstance(value, bytes):
            converted_data[key] = str(value, 'utf-8')
    return converted_data


def thumbnail_to_bytes(data_input):
    """Parse thumbnail from string to original bytes format"""
    converted_data = dict()
    for key, value in data_input.items():
        converted_data[key] = bytes(value, 'utf-8')
    return converted_data


def estimated_to_seconds(value: str):
    """Convert string value to seconds.

    >>> estimated_to_seconds("2s")
    2
    >>> estimated_to_seconds("2m 2s")
    122
    >>> estimated_to_seconds("2M")
    120
    >>> estimated_to_seconds("2h 2m 2s")
    7322
    >>> estimated_to_seconds("2d 2h 2m 2s")
    180122
    >>> estimated_to_seconds("bad value")
    """
    match = RE_ESTIMATED.match(value.lower())
    if not match:
        return None
    values = match.groupdict()
    retval = int(values['days'] or 0) * 60 * 60 * 24
    retval += int(values['hours'] or 0) * 60 * 60
    retval += int(values['minutes'] or 0) * 60
    retval += int(values['seconds'] or 0)

    return retval or None


class MetaData:
    """Base MetaData class"""

    path: str
    thumbnails: Dict[str, bytes]  # dimensions: base64(data)
    data: Dict[str, str]  # key: value

    Attrs: Dict[str, Any] = {}  # metadata (name, convert_fct)

    def __init__(self, path: str):
        self.path = path
        self.thumbnails = {}
        self.data = {}

    @property
    def cache_name(self):
        """Create cache name in format .<filename>.cache

        >>> MetaData("/test/a.gcode").cache_name
        '/test/.a.gcode.cache'

        >>> MetaData("/test/a.txt").cache_name
        '/test/.a.txt.cache'

        >>> MetaData("x").cache_name
        '/.x.cache'

        """
        path_ = os.path.split(self.path)
        new_path = path_[0] + "/." + path_[1] + ".cache"
        return new_path

    def is_cache_fresh(self):
        """If cache is fresher than file, returns True"""
        file_time_created = os.path.getctime(self.path)
        try:
            cache_time_created = os.path.getctime(self.cache_name)
            return file_time_created < cache_time_created
        except FileNotFoundError:
            return False

    def save_cache(self):
        """Take metadata from source file and save them as JSON to
        <file_name>.cache file"""
        try:
            if self.thumbnails or self.data:
                dict_data = {
                    "thumbnails": thumbnail_from_bytes(self.thumbnails),
                    "data": self.data
                }
                with open(self.cache_name, "w") as file:
                    json.dump(dict_data, file, indent=2)
        except PermissionError:
            log.warning("You don't have permission for save file here")

    def load_cache(self):
        """Load metadata values from <file_name>.cache file"""
        try:
            with open(self.cache_name, "r") as file:
                cache_data = json.load(file)
            self.thumbnails = thumbnail_to_bytes(cache_data["thumbnails"])
            self.data = cache_data["data"]
        except (json.decoder.JSONDecodeError, FileNotFoundError, KeyError)\
                as err:
            raise ValueError(
                "JSON data not found or in incorrect format") from err

    def load(self, save_cache=True):
        """Extract and set metadata from `self.path`. Any metadata
        obtained from the path will be overwritten by metadata from
        the file if the metadata is contained there as well"""
        if self.is_cache_fresh():
            self.load_cache()
        else:
            self.load_from_path(self.path)
            self.load_from_file(self.path)
            if save_cache:
                self.save_cache()

    def load_from_file(self, path: str):
        """Load metadata and thumbnails from given `path`"""
        # pylint: disable=no-self-use
        # pylint: disable=unused-argument
        ...

    def load_from_path(self, path: str):
        """Load metadata from given path (path, not its content),
        if possible.
        """
        # pylint: disable=no-self-use
        # pylint: disable=unused-argument
        ...

    def set_data(self, data: Dict):
        """Helper function to save all items from `data` that
        match `self.Attr` in `self.data`.
        """
        for attr, conv in self.Attrs.items():
            val = data.get(attr)
            if val:
                try:
                    self.data[attr] = conv(val)
                except ValueError:
                    log.warning("Could not convert using %s: %s", conv, val)

    def __repr__(self):
        return f"Metadata: {self.path}, {len(self.data)} items, " \
               f"{len(self.thumbnails)} thumbnails"

    __str__ = __repr__


class FDMMetaData(MetaData):
    """Class for extracting Metadata for FDM gcodes"""

    # Meta data we are looking for and respective conversion functions
    Attrs = {
        "filament used [mm]": float,
        "filament used [cm3]": float,
        "filament used [g]": float,
        "filament cost": float,
        "estimated printing time (normal mode)": str,
        "filament_type": str,
        "nozzle_diameter": float,
        "printer_model": str,
        "layer_height": float,
        "fill_density": str,
        "bed_temperature": int,
        "brim_width": int,
        "temperature": int,
        "support_material": int,
        "ironing": int,
    }

    KEY_VAL_PAT = re.compile("; (?P<key>.*?) = (?P<value>.*)$")

    THUMBNAIL_BEGIN_PAT = re.compile(
        r"; thumbnail begin\s+(?P<dim>\w+) (?P<size>\d+)")
    THUMBNAIL_END_PAT = re.compile("; thumbnail end")

    FDM_FILENAME_PAT = re.compile(
        r"^(?P<name>.*?)_(?P<height>[0-9\.]+)mm_"
        r"(?P<material>\w+)_(?P<printer>\w+)_(?P<time>.*)\.")

    def load_from_path(self, path):
        """Try to obtain any usable metadata from the path itself"""
        filename = os.path.basename(path)
        match = self.FDM_FILENAME_PAT.match(filename)
        if match:
            data = {
                "name": match.group("name"),
                "layer_height": match.group("height"),
                "filament_type": match.group("material"),
                "printer_model": match.group("printer"),
                "estimated printing time (normal mode)": match.group("time"),
            }
            self.set_data(data)

    def load_from_file(self, path: str):
        """Load metadata from file

        :path: Path to the file to load the metadata from
        """
        # pylint: disable=redefined-outer-name
        # pylint: disable=invalid-name
        meta = {}
        thumbnail: List[str] = []
        dimension = ""
        size = None
        with open(path) as f:
            for line in f.readlines():
                # thumbnail handling
                match = self.THUMBNAIL_BEGIN_PAT.match(line)
                if match:
                    dimension = match.group("dim")
                    size = int(match.group("size"))
                    thumbnail = []
                    continue
                match = self.THUMBNAIL_END_PAT.match(line)
                if match:
                    data = "".join(thumbnail)
                    self.thumbnails[dimension] = data.encode()
                    assert len(data) == size, len(data)
                    thumbnail = []
                    dimension = ""
                    continue
                if dimension:
                    data = line[2:].strip()
                    thumbnail.append(data)
                else:  # looking for metadata
                    match = self.KEY_VAL_PAT.match(line)
                    if match:
                        key, val = match.groups()
                        meta[key] = val
        self.set_data(meta)


class SLMetaData(MetaData):
    """Class that can extract available metadata and thumbnails from
    ziparchives used by SL1 slicers"""

    # Thanks to Bruno Carvalho for sharing code to extract metadata and
    # thumbnails!

    Attrs = {
        "printer_model": str,
        "printTime": int,
        "faded_layers": int,
        "exposure_time": float,
        "initial_exposure_time": float,
        "max_initial_exposure_time": float,
        "max_exposure_time": float,
        "min_initial_exposure_time": float,
        "min_exposure_time": float,
        "layer_height": float,
        "materialName": str,
        "fileCreationTimestamp": str,
    }

    THUMBNAIL_NAME_PAT = re.compile(r"(?P<dim>\d+x\d+)")

    def load(self, save_cache=True):
        """Load metadata"""
        try:
            super().load(save_cache)
        except zipfile.BadZipFile:
            # NOTE can't import `log` from __init__.py because of
            #  circular dependencies
            print("%s is not a valid SL1 archive", self.path)

    def load_from_file(self, path: str):
        """Load SL1 metadata

        :path: path to the file to load the metadata from
        """
        data = self.extract_metadata(path)
        self.set_data(data)

        self.thumbnails = self.extract_thumbnails(path)

    @staticmethod
    def extract_metadata(path: str) -> Dict[str, str]:
        """Extract metadata from `path`.

        :param path: zip file
        :returns Dictionary with metadata name as key and its
            value as value
        """
        # pylint: disable=invalid-name
        data = {}
        with zipfile.ZipFile(path, "r") as zip_file:
            for fn in ("config.ini", "prusaslicer.ini"):
                config_file = zip_file.read(fn).decode("utf-8")
                for line in config_file.splitlines():
                    key, value = line.split(" = ")
                    try:
                        data[key] = json.loads(value)
                    except json.decoder.JSONDecodeError:
                        data[key] = value
        return data

    @staticmethod
    def extract_thumbnails(path: str) -> Dict[str, bytes]:
        """Extract thumbnails from `path`.

        :param path: zip file
        :returns Dictionary with thumbnail dimensions as key and base64
            encoded image as value.
        """
        thumbnails: Dict[str, bytes] = {}
        with zipfile.ZipFile(path, "r") as zip_file:
            for info in zip_file.infolist():
                if info.filename.startswith("thumbnail/"):
                    data = zip_file.read(info.filename)
                    data = base64.b64encode(data)
                    dim = SLMetaData.THUMBNAIL_NAME_PAT.findall(
                        info.filename)[-1]
                    thumbnails[dim] = data
        return thumbnails


def get_metadata(path: str, save_cache=True):
    """Return the Metadata for given `path`

    :param path: Gcode file
    :param save_cache: Boolean if cache should be saved
    """
    # pylint: disable=redefined-outer-name
    fnl = path.lower()
    meta: MetaData
    if fnl.endswith(GCODE_EXTENSIONS):
        meta = FDMMetaData(path)
    elif fnl.endswith(".sl1"):
        meta = SLMetaData(path)
    else:
        raise UnknownGcodeFileType(path)

    meta.load(save_cache)
    return meta


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("file", help="gcode file")
    args = parser.parse_args()

    meta = get_metadata(args.file)
    for k, v in meta.data.items():
        print(f"{k}: {v}")

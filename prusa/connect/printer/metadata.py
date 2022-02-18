"""Metadata parser for Prusa Slicer gcode files. Extracts preview pictures
as well.
"""
from time import time, sleep

import base64
import json
import re
import os
import zipfile
from typing import Dict, Any
from logging import getLogger
from .const import GCODE_EXTENSIONS

log = getLogger("connect-printer")

TOLERATED_COUNT = 2
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
    converted_data = {}
    for key, value in data_input.items():
        if isinstance(value, bytes):
            converted_data[key] = str(value, 'utf-8')
    return converted_data


def thumbnail_to_bytes(data_input):
    """Parse thumbnail from string to original bytes format"""
    converted_data = {}
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


class ParsedData:
    """Container for state sharing between FDM parsing methods"""

    # pylint: disable=too-few-public-methods

    def __init__(self):
        self.dimension = ""
        self.size = None
        self.meta = {}
        self.thumbnail = []


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
                with open(self.cache_name, "w", encoding='utf-8') as file:
                    json.dump(dict_data, file, indent=2)
        except PermissionError:
            log.warning("You don't have permission to save file here")

    def load_cache(self):
        """Load metadata values from <file_name>.cache file"""
        try:
            with open(self.cache_name, "r", encoding='utf-8') as file:
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

    # Metadata we are looking for and respective conversion functions
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

    @staticmethod
    def reverse_readline(file_descriptor, buf_size=8192):
        """
        A generator that returns the lines of a file in reverse order
        """
        segment = None
        offset = 0
        file_descriptor.seek(0, os.SEEK_END)
        file_size = remaining_size = file_descriptor.tell()
        while remaining_size > 0:
            offset = min(file_size, offset + buf_size)
            file_descriptor.seek(file_size - offset)
            buffer = file_descriptor.read(min(remaining_size, buf_size))
            remaining_size -= buf_size
            lines = buffer.split('\n')
            # The first line of the buffer is probably not a complete line, so
            # we'll save it and append it to the last line of the next buffer
            # we read
            if segment is not None:
                # If the previous chunk starts right from the beginning of
                # line do not concat the segment to the last line of new
                # chunk. Instead, yield the segment first
                if buffer[-1] != '\n':
                    lines[-1] += segment
                else:
                    yield segment
            segment = lines[0]
            for index in range(len(lines) - 1, 0, -1):
                if lines[index]:
                    yield lines[index]
        # Don't yield None if the file was empty
        if segment is not None:
            yield segment

    def from_line(self, data, line):
        """
        Parses data out of a given line
        data variable is used to store all temporary data
        """
        # thumbnail handling
        match = self.THUMBNAIL_BEGIN_PAT.match(line)
        if match:
            data.dimension = match.group("dim")
            data.size = int(match.group("size"))
            data.thumbnail = []
            return

        match = self.THUMBNAIL_END_PAT.match(line)
        if match:
            data.image_data = "".join(data.thumbnail)
            self.thumbnails[data.dimension] = data.image_data.encode()
            assert len(data.image_data) == data.size, len(data.image_data)
            data.thumbnail = []
            data.dimension = ""
            return
        if data.dimension:
            line = line[2:].strip()
            data.thumbnail.append(line)
        else:  # looking for metadata
            match = self.KEY_VAL_PAT.match(line)
            if match:
                key, val = match.groups()
                data.meta[key] = val

    def load_from_file(self, path):
        """Load metadata from file
        Tries to use the quick_parse function. Ff it keeps failing,
        tries the old technique of parsing.

        :path: Path to the file to load the metadata from
        """
        # pylint: disable=redefined-outer-name
        # pylint: disable=invalid-name
        started_at = time()
        data = ParsedData()

        retries = 10
        while retries:
            with open(path, "r", encoding='utf-8') as file_descriptor:
                if self.quick_parse(data, file_descriptor, retries == 1):
                    break
                retries -= 1
                sleep(0.2)

        if not retries:
            with open(path, "r", encoding='utf-8') as file_descriptor:
                data = ParsedData()
                file_descriptor.seek(0, 0)
                for line in file_descriptor:
                    self.from_line(data, line)

        self.set_data(data.meta)
        log.debug("Caching took %s", time() - started_at)

    def quick_parse(self, data, file_descriptor, is_last_retry=False):
        """
        Parse metadata by looking at comment blocks in the beginning
        and at the end of a file
        returns True if it thinks parsing succeeded
        """
        # pylint: disable=too-many-branches
        for line in file_descriptor:
            if not line.strip():
                continue
            if not line.startswith(";"):
                break
            self.from_line(data, line)

        comment_lines = 0
        parsing_image = False
        reverse_image = []
        for line in self.reverse_readline(file_descriptor):
            if not line.strip():
                continue
            if not line.startswith(";"):
                break
            comment_lines += 1

            # Images cannot be parsed on reverse,
            # so lets reverse them beforehand
            if self.THUMBNAIL_END_PAT.match(line):
                parsing_image = True
            if parsing_image:
                reverse_image.append(line)
            else:
                self.from_line(data, line)
            if self.THUMBNAIL_BEGIN_PAT.match(line):
                parsing_image = False
                for image_line in reversed(reverse_image):
                    self.from_line(data, image_line)

        wanted = set(self.Attrs.keys())
        got = set(data.meta.keys())
        missed = wanted - got
        log.debug("Wanted: %s", wanted)
        log.debug("Parsed: %s", got)

        log.debug(
            "By not reading the whole file, "
            "we have managed to miss %s", list(missed))

        # --- Was parsing successful? ---

        if comment_lines < 10:
            log.warning("Not enough comments discovered, "
                        "file not uploaded yet?")
            return False

        if missed and is_last_retry:
            if len(missed) == len(wanted):
                log.warning("No metadata parsed!")
            else:
                log.warning("Metadata missing %s", missed)
            if len(missed) <= TOLERATED_COUNT:
                log.warning("Missing meta tolerated, missing count < %s",
                            TOLERATED_COUNT)
            else:
                return False

        return True


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
    """Returns the Metadata for given `path`

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

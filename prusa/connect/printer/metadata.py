"""Metadata parser for Prusa Slicer gcode files. Extracts preview pictures
as well.
"""
import base64
import json
import os
import re
import zipfile
from logging import getLogger
from time import sleep, time
from typing import Any, Dict

from .const import (
    COMMENT_BLOCK_MAX_SIZE,
    GCODE_EXTENSIONS,
    METADATA_CHUNK_SIZE,
    METADATA_MAX_OFFSET,
)

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
        self.image_data = None
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
        try:
            file_time_created = os.path.getctime(self.path)
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
                    "data": self.data,
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
        # pylint: disable=unused-argument

    def load_from_path(self, path: str):
        """Load metadata from given path (path, not its content),
        if possible.
        """
        # pylint: disable=unused-argument

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

    def __init__(self, path: str):
        super().__init__(path)
        self.last_filename = None

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
        remaining_size = file_descriptor.tell()
        while remaining_size > 0:
            offset = min(remaining_size, buf_size)
            seek_to = remaining_size - offset
            remaining_size = file_descriptor.seek(seek_to)
            buffer = file_descriptor.read(offset)
            lines = buffer.split(b'\n')
            # The first line of the buffer is probably not a complete line, so
            # we'll save it and append it to the last line of the next buffer
            # we read
            if segment is not None:
                # If the previous chunk starts right from the beginning of
                # line do not concat the segment to the last line of new
                # chunk. Instead, yield the segment first
                if buffer[-1] != b'\n':
                    lines[-1] += segment
                else:
                    yield segment
            segment = lines[0]
            for line in reversed(lines[1:]):
                yield line
        # Don't yield None if the file was empty
        if segment is not None:
            yield segment

    def from_line(self, data: ParsedData, line):
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
        # We store the image dimensions only during parsing
        # If actively parsing:
        if data.dimension:
            line = line[2:].strip()
            data.thumbnail.append(line)

        match = self.KEY_VAL_PAT.match(line)
        if match:
            key, val = match.groups()
            data.meta[key] = val

    def load_from_file(self, path):
        """Load metadata from file
        Tries to use the quick_parse function. If it keeps failing,
        tries the old technique of parsing.

        :path: Path to the file to load the metadata from
        """
        # pylint: disable=redefined-outer-name
        # pylint: disable=invalid-name
        started_at = time()
        data = ParsedData()

        retries = 2
        while retries:
            with open(path, "rb") as file_descriptor:
                self.quick_parse(data, file_descriptor)
                parsing_new_file = self.last_filename != file_descriptor.name
                to_log = retries == 1 and parsing_new_file
                if self.evaluate_quick_parse(data, to_log):
                    break
                retries -= 1
                sleep(0.2)

        self.last_filename = file_descriptor.name

        self.set_data(data.meta)
        log.debug("Caching took %s", time() - started_at)

    def evaluate_quick_parse(self, data: ParsedData, to_log=False):
        """Evaluates if the parsed data is sufficient
        Can log the result
        Returns True if the data is sufficient"""
        wanted = set(self.Attrs.keys())
        got = set(data.meta.keys())
        missed = wanted - got
        log.debug("Wanted: %s", wanted)
        log.debug("Parsed: %s", got)

        log.debug(
            "By not reading the whole file, "
            "we have managed to miss %s", list(missed))

        # --- Was parsing successful? ---

        if len(data.meta) < 10:
            log.warning("Not enough info found, file not uploaded yet?")
            return False

        if missed and to_log:
            if len(missed) == len(wanted):
                log.warning("No metadata parsed!")
            else:
                log.warning("Metadata missing %s", missed)
            if len(missed) <= TOLERATED_COUNT:
                log.warning("Missing meta tolerated, missing count < %s",
                            TOLERATED_COUNT)

        if len(missed) > TOLERATED_COUNT:
            return False

        return True

    def parse_comment_block(self, data: ParsedData, file_descriptor):
        """Parses consecutive lines until one doesn't start with a semicolon
        returns how many bytes it parsed
        """
        bytes_parsed = 0
        for line in file_descriptor:
            if not line.strip():
                bytes_parsed += 1
                continue
            if not line.startswith(b";"):
                break
            # Do not read more than a xK of comments
            if bytes_parsed > COMMENT_BLOCK_MAX_SIZE:
                break
            bytes_parsed += len(line) + 1
            self.from_line(data, line.decode("UTF-8"))
        return bytes_parsed

    def find_block_start(self, file_descriptor):
        """Given a file descriptor at a position in a gcode file,
        gets the position of the first comment line in its block"""
        reverse_generator = self.reverse_readline(file_descriptor)
        position = file_descriptor.tell()
        block_start = position
        for line in reverse_generator:
            if not line.strip():
                block_start -= 1
                continue
            if not line.startswith(b";"):
                break
            # Stop if there are too many comments
            if position - block_start > COMMENT_BLOCK_MAX_SIZE:
                break
            # +1 for the newline which is not included
            block_start -= len(line) + 1
        reverse_generator.close()
        return block_start

    def quick_parse(self, data: ParsedData, file_descriptor):
        """Parse metadata on the start and end of the file"""
        # pylint: disable=too-many-branches
        position = self.parse_comment_block(data, file_descriptor)
        size = file_descriptor.seek(0, os.SEEK_END)
        file_descriptor.seek(position)
        while position != size:
            if METADATA_MAX_OFFSET < position < size - METADATA_MAX_OFFSET:
                # Skip the middle part of the file
                position = size - METADATA_MAX_OFFSET
            file_descriptor.seek(position)
            chunk = file_descriptor.read(METADATA_CHUNK_SIZE)
            if b"\n;" in chunk:
                relative_semicolon_index = chunk.index(b"\n;")
                semicolon_position = position + relative_semicolon_index
                file_descriptor.seek(semicolon_position)
                block_start = self.find_block_start(file_descriptor)
                file_descriptor.seek(block_start)
                parsed_bytes = self.parse_comment_block(data, file_descriptor)
                position = block_start + parsed_bytes
            else:
                offset = min(METADATA_CHUNK_SIZE, size - position)
                position = file_descriptor.seek(position + offset)


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
    metadata: MetaData
    if fnl.lower().endswith(GCODE_EXTENSIONS):
        metadata = FDMMetaData(path)
    elif fnl.lower().endswith(".sl1"):
        metadata = SLMetaData(path)
    else:
        raise UnknownGcodeFileType(path)

    metadata.load(save_cache)
    return metadata


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("file", help="gcode file")
    args = parser.parse_args()

    meta = get_metadata(args.file)
    for k, v in meta.data.items():
        print(f"{k}: {v}")

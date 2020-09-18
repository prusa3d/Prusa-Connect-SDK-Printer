"""Metadata parser for Prusa Slicer gcode files. Extracts preview pictures
as well.
"""

import base64
import json
import re
import zipfile
from os import path
from pathlib import Path
from typing import Dict, Any, List
from . import log


class UnknownGcodeFileType(ValueError):
    # pylint: disable=missing-class-docstring
    ...


class MetaData:
    """Base MetaData class"""

    filename: str
    thumbnails: Dict[str, bytes]  # dimensions: base64(data)
    data: Dict[str, str]  # key: value

    Attrs: Dict[str, Any] = {}  # metadata (name, convert_fct)

    def __init__(self, filename: str):
        self.filename = filename
        self.thumbnails = {}
        self.data = {}

    def load(self):
        """Extract and set metadata from `self.filename`. Any metadata
        obtained from the filename will be overwritten by metadata from
        the file if the metadata is contained there as well"""
        self.load_from_filename(self.filename)
        self.load_from_file(self.filename)

    def load_from_file(self, filename: Path):
        """Load metadata and thumbnails from given `filename`"""
        raise NotImplementedError

    def load_from_filename(self, filename: Path):
        """Load metadata from given filename (filename, not its content),
        if possible.
        """

    def _set_data(self, data: Dict):
        """Helper function to save all items from `data` that
        match `self.Attr` in `self.data`.
        """
        for attr, conv in self.Attrs.items():
            val = data.get(attr)
            if val:
                self.data[attr] = conv(val)

    def __repr__(self):
        return f"Metadata: {self.filename}, {len(self.data)} items, " \
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
        "fill_density": str,
        "bed_temperature": int,
        "brim_width": int,
        "temperature": int,
        "support_material": int,
    }

    KEY_VAL_PAT = re.compile("; (?P<key>.*?) = (?P<value>.*)$")

    THUMBNAIL_BEGIN_PAT = re.compile(
        r"; thumbnail begin\s+(?P<dim>\w+) (?P<size>\d+)")
    THUMBNAIL_END_PAT = re.compile("; thumbnail end")

    FDM_FILENAME_PAT = re.compile(
        r"^(?P<name>.*?)_(?P<height>[0-9\.]+)mm_"
        r"(?P<material>\w+)_(?P<printer>\w+)_(?P<time>.*)\.")

    def load_from_filename(self, filename):
        """Try to obtain any usable metadata from the filename itself"""
        match = self.FDM_FILENAME_PAT.match(filename)
        if match:
            data = {
                "name": path.basename(match.group("name")),
                "layer_height": match.group("height"),
                "filament_type": match.group("material"),
                "printer_model": match.group("printer"),
                "estimated printing time (normal mode)": match.group("time"),
            }
            self._set_data(data)

    def load_from_file(self, filename: Path):
        # pylint: disable=redefined-outer-name
        # pylint: disable=invalid-name
        meta = {}
        thumbnail: List[str] = []
        dimension = ""
        size = None
        with open(filename) as f:
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
        self._set_data(meta)


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

    def load(self):
        try:
            super().load()
        except zipfile.BadZipFile:
            log.error("%s is not a valid SL1 archive", self.filename)

    def load_from_file(self, filename):
        data = self.extract_metadata(filename)
        self._set_data(data)

        self.thumbnails = self.extract_thumbnails(filename)

    @staticmethod
    def extract_metadata(filename: str) -> Dict[str, str]:
        """Extract metadata from `filename`.

        :param filename: zip file
        :returns Dictionary with metadata name as key and its
            value as value
        """
        # pylint: disable=invalid-name
        data = {}
        with zipfile.ZipFile(filename, "r") as zip_file:
            for fn in ("config.ini", "prusaslicer.ini"):
                config_file = zip_file.read(fn).decode("utf-8")
                for line in config_file.splitlines():
                    key, value = line.split(" = ")
                    try:
                        data[key] = json.loads(value)
                    except json.decoder.JSONDecodeError:
                        data[key] = value
        return data

    def extract_thumbnails(self, filename: str) -> Dict[str, bytes]:
        """Extract thumbnails from `filename`.

        :param filename: zip file
        :returns Dictionary with thumbnail dimensions as key and base64
            encoded image as value.
        """
        thumbnails: Dict[str, bytes] = {}
        with zipfile.ZipFile(filename, "r") as zip_file:
            for info in zip_file.infolist():
                if info.filename.startswith("thumbnail/"):
                    data = zip_file.read(info.filename)
                    data = base64.b64encode(data)
                    dim = self.THUMBNAIL_NAME_PAT.findall(info.filename)[-1]
                    thumbnails[dim] = data
        return thumbnails


def get_metadata(filename: str):
    """Return the Metadata for given `filename`

    :param filename: Gcode file
    """
    # pylint: disable=redefined-outer-name
    fnl = filename.lower()
    meta: MetaData
    if fnl.endswith(".gcode"):
        meta = FDMMetaData(filename)
    elif fnl.endswith(".sl1"):
        meta = SLMetaData(filename)
    else:
        raise UnknownGcodeFileType(filename)

    meta.load()
    return meta


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("file", help="gcode file")
    args = parser.parse_args()

    meta = get_metadata(args.file)
    for k, v in meta.data.items():
        print(f"{k}: {v}")

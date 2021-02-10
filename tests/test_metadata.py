import os
import pytest
import tempfile
import filecmp
import shutil
import time
import json

from prusa.connect.printer.metadata import get_metadata, UnknownGcodeFileType

gcodes_dir = os.path.join(os.path.dirname(os.path.realpath(__file__)),
                          "gcodes", "metadata")


@pytest.fixture
def tmp_dir():
    tmp = tempfile.TemporaryDirectory()
    yield tmp
    del tmp


def test_save_cache_file():
    """Test save-cache() with correct data"""
    fn = os.path.join(gcodes_dir, "fdn_filename.gcode")
    meta = get_metadata(fn)
    meta.save_cache()


def test_load_cache_file():
    """Test load_cache() with correct data"""
    fn = os.path.join(gcodes_dir, "fdn_filename.gcode")
    meta = get_metadata(fn)
    meta.load_cache()


def test_get_metadata_file_does_not_exist():
    """Test get_metadata() with a non-existing file"""
    fn = '/somehwere/in/the/rainbow/my.gcode'
    with pytest.raises(FileNotFoundError):
        get_metadata(fn)


def test_save_cache_original_file_does_not_exist():
    """Test save_cache() with a non-existing original file"""
    with pytest.raises(FileNotFoundError):
        fn = os.path.join(gcodes_dir, "imaginary_filename.gcode")
        meta = get_metadata(fn)
        meta.save_cache()


def test_load_cache_file_does_not_exist():
    """Test load_cache() with a non-existing cache file"""
    with pytest.raises(ValueError):
        fn = os.path.join(gcodes_dir, "fdn_all_empty.gcode")
        meta = get_metadata(fn)
        meta.load_cache()


def test_load_cache_empty_file():
    """Test load_cache() with empty file"""
    fn = os.path.join(gcodes_dir, "fdn_filename_empty.gcode")
    with open(fn + ".cache", "w"):
        pass
    with pytest.raises(ValueError):
        meta = get_metadata(fn)
        meta.load_cache()


def test_key_error_load_cache():
    """test load_cache() with incorrect, or missing key"""
    fn = os.path.join(gcodes_dir, "fdn_filename_empty.gcode")
    meta = get_metadata(fn)
    with pytest.raises(ValueError):
        meta.load_cache()


def test_is_cache_fresh_fresher(tmp_dir):
    """is_cache_fresh, when cache file is fresher, than original file"""
    temp_dir = tmp_dir.name
    fn_gcode = os.path.join(gcodes_dir, "fdn_filename.gcode")
    temp_gcode = shutil.copy(fn_gcode, temp_dir)

    # Create the time difference
    time.sleep(0.01)

    meta = get_metadata(temp_gcode)
    fn_cache = os.path.join(gcodes_dir, "fdn_filename.gcode.cache")
    shutil.copy(fn_cache, temp_dir)
    assert meta.is_cache_fresh()


def test_is_cache_fresh_older(tmp_dir):
    """is_cache_fresh, when cache file is older, than original file"""
    temp_dir = tmp_dir.name
    fn_cache = os.path.join(gcodes_dir, "fdn_filename.gcode.cache")
    shutil.copy(fn_cache, temp_dir)

    # Create the time difference
    time.sleep(0.01)

    fn_gcode = os.path.join(gcodes_dir, "fdn_filename.gcode")
    temp_gcode = shutil.copy(fn_gcode, temp_dir)
    meta = get_metadata(temp_gcode)
    assert meta.is_cache_fresh() is False


def test_save_and_compare_cache_file(tmp_dir):
    """Save data using save_cache() and then compare values from
    load_cache() with default cache file values"""
    temp_dir = tmp_dir.name

    fn_gcode = os.path.join(gcodes_dir, "fdn_filename.gcode")
    fn_cache = os.path.join(gcodes_dir, "fdn_filename.gcode.cache")
    meta = get_metadata(fn_gcode)

    temp_gcode = shutil.copy(fn_gcode, temp_dir)
    temp_cache = os.path.join(temp_dir, "fdn_filename.gcode.cache")
    get_metadata(temp_gcode).save_cache()

    with open(temp_cache, "r") as file:
        cache_data = json.load(file)
        cache_data["path"] = meta.path

    with open(temp_cache, "w") as file:
        json.dump(cache_data, file, indent=2)

    assert filecmp.cmp(temp_cache, fn_cache, shallow=False)


def test_get_metadata_invalid_file():
    """Test get_metadata() with a file that has a wrong ending"""
    fn = tempfile.mkstemp()[1]
    with pytest.raises(UnknownGcodeFileType):
        get_metadata(fn)


class TestFDNMetaData:
    def test_full(self):
        """Both the file and filename contain metadata. There are thumbnails.
        """
        fn = os.path.join(gcodes_dir, "fdn_full_0.25mm_PETG_MINI_2h9m.gcode")
        meta = get_metadata(fn)
        assert meta.data == {
            'bed_temperature': 90,
            'brim_width': 0,
            'estimated printing time (normal mode)': '2h 9m 24s',
            'filament cost': 0.6,
            'filament used [cm3]': 16.8,
            'filament used [g]': 21.4,
            'filament used [mm]': 7003.4,
            'filament_type': 'PETG',
            'fill_density': '0%',
            'nozzle_diameter': 0.4,
            'printer_model': 'MINI',
            'support_material': 0,
            'temperature': 240,
            'ironing': 0
        }
        assert len(meta.thumbnails['16x16']) == 608
        assert len(meta.thumbnails['220x124']) == 11680

    def test_only_path(self):
        """Only the filename contains metadata. There are no thumbnails."""
        fn = os.path.join(gcodes_dir,
                          "fdn_only_filename_0.25mm_PETG_MINI_2h9m.gcode")
        meta = get_metadata(fn)
        assert meta.data == {
            'estimated printing time (normal mode)': '2h9m',
            'filament_type': 'PETG',
            'printer_model': 'MINI'
        }
        assert not meta.thumbnails

    def test_fdn_all_empty(self):
        """Only the file contains metadata. There are thumbnails."""
        fn = os.path.join(gcodes_dir, "fdn_all_empty.gcode")
        meta = get_metadata(fn)
        assert not meta.data
        assert not meta.thumbnails
        assert meta.path == fn


class TestSLMetaData:
    def test_sl(self):
        fn = os.path.join(gcodes_dir, "pentagonal-hexecontahedron-1.sl1")
        meta = get_metadata(fn)

        assert meta.data == {
            'printer_model': 'SL1',
            'printTime': 8720,
            'faded_layers': 10,
            'exposure_time': 7.5,
            'initial_exposure_time': 35.0,
            'max_initial_exposure_time': 300.0,
            'max_exposure_time': 120.0,
            'min_initial_exposure_time': 1.0,
            'min_exposure_time': 1.0,
            'layer_height': 0.05,
            'materialName': 'Prusa Orange Tough @0.05',
            'fileCreationTimestamp': '2020-09-17 at 13:53:21 UTC'
        }

        assert len(meta.thumbnails["400x400"]) == 19688
        assert len(meta.thumbnails["800x480"]) == 64524

    def test_sl_empty_file(self):
        """Test a file that is empty"""
        fn = os.path.join(gcodes_dir, "empty.sl1")
        meta = get_metadata(fn)

        assert not meta.data
        assert not meta.thumbnails

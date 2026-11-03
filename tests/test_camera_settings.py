"""Tests for the Camera settings and the Resolution class"""
from unittest.mock import Mock

import pytest

from prusa.connect.printer.camera import Camera, Resolution
from prusa.connect.printer.const import DriverError
from tests.test_cameras import GoodDriver


def test_resolution_comparison():
    """Resolutions are compared by their area"""
    small, big = Resolution(2, 2), Resolution(3, 3)
    assert big > small
    assert big >= small
    assert small <= big
    assert not small > big
    assert reversed(Resolution(4, 2)) == Resolution(2, 4)


@pytest.fixture(name="camera")
def fixture_camera():
    """A camera with a driver supporting all settings"""
    config = GoodDriver.scan()
    camera_id, camera_config = next(iter(config.items()))
    driver = GoodDriver(camera_id, camera_config, Mock())
    driver.connect()
    return Camera(driver)


def test_valid_settings(camera):
    """The settings are passed to the driver and remembered"""
    camera.rotation = 90
    camera.exposure = 1.5
    camera.focus = 0.5
    assert (camera.rotation, camera.exposure, camera.focus) == (90, 1.5, 0.5)
    assert camera.output_resolution == reversed(camera.resolution)


@pytest.mark.parametrize(("name", "value"), [
    ("rotation", 45),
    ("exposure", 3),
    ("focus", 2),
])
def test_invalid_settings(camera, name, value):
    """Out of range values are refused"""
    with pytest.raises(DriverError):
        setattr(camera, name, value)

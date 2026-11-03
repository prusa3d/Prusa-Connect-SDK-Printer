"""Tests for the less used paths of the camera driver and configurator"""
from configparser import ConfigParser
from types import MappingProxyType
from unittest.mock import Mock

import pytest

from prusa.connect.printer.camera import Resolution, Snapshot
from prusa.connect.printer.camera_configurator import CameraConfigurator
from prusa.connect.printer.camera_controller import CameraController
from prusa.connect.printer.camera_driver import CameraDriver
from prusa.connect.printer.const import (
    CameraAlreadyConnected,
    CameraNotFound,
    ConfigError,
)
from tests.test_cameras import DummyDriver


class BareDriver(CameraDriver):
    """A driver which implements nothing"""
    name = "Bare"


class BrokenScanDriver(BareDriver):
    """A driver whose scan and disconnect fail"""
    name = "Broken"

    @classmethod
    def _scan(cls):
        raise RuntimeError("scan failed")

    def _disconnect(self):
        raise RuntimeError("disconnect failed")


class TriggerSchemeDriver(BareDriver):
    """A driver which breaks the rules in its scan"""
    name = "Rulebreaker"

    @classmethod
    def _scan(cls):
        return {"x": {"trigger_scheme": "Manual"}}


def make_driver(cls=BareDriver, config=None):
    """Instances a driver"""
    return cls("id", config or {"name": "n"}, Mock())


def test_driver_without_name():
    """A driver must have a name"""

    class Nameless(CameraDriver):
        pass

    with pytest.raises(ValueError):
        Nameless("id", {}, Mock())


def test_driver_not_implemented():
    """The base class methods fail loudly"""
    driver = make_driver()
    with pytest.raises(NotImplementedError):
        driver._connect()
    with pytest.raises(NotImplementedError):
        driver.take_a_photo()
    for method, arg in ((driver.set_resolution, None),
                        (driver.set_rotation, 0), (driver.set_exposure,
                                                   0), (driver.set_focus, 0)):
        with pytest.raises(NotImplementedError):
            method(arg)


def test_driver_scan_errors():
    """A failing scan is an empty scan, trigger_scheme is dropped from rules"""
    assert BareDriver.scan() == {}
    assert BrokenScanDriver.scan() == {}
    # the invalid config is dropped, a warning is logged
    assert TriggerSchemeDriver.scan() == {}


def test_driver_connect():
    """connect needs a valid config and survives a failing _connect"""

    class NeedsOne(BareDriver):
        REQUIRES_SETTINGS = MappingProxyType({"a": "A"})

    with pytest.raises(ConfigError):
        NeedsOne("id", {}, Mock()).connect()
    driver = NeedsOne("id", {"a": "1", "name": "n", "driver": "d"}, Mock())
    driver.connect()  # BareDriver._connect raises, which is handled
    assert not driver.is_connected


def test_driver_initial_resolution():
    """An invalid or unavailable resolution falls back to the highest"""
    available = {Resolution(3, 3), Resolution(5, 5)}
    get = CameraDriver._get_initial_resolution
    assert get(available, {"resolution": "3x3"}) == Resolution(3, 3)
    assert get(available, {"resolution": "junk"}) == Resolution(5, 5)
    assert get(available, {"resolution": "9x9"}) == Resolution(5, 5)
    assert get(available, {}) == Resolution(5, 5)


def test_driver_photo_error():
    """A failing photo is logged and disconnects the driver"""
    driver = make_driver()
    snapshot = Snapshot()
    driver._photo_taker(snapshot)  # take_a_photo raises NotImplementedError
    assert snapshot.data is None
    assert snapshot.timestamp is None


def test_driver_disconnect_error():
    """A failing _disconnect does not raise"""
    driver = make_driver(BrokenScanDriver)
    driver.disconnect()
    assert not driver.is_connected


def test_driver_invalid_config_hash():
    """get_config_hash survives a missing setting"""

    class NeedsOne(BareDriver):
        REQUIRES_SETTINGS = MappingProxyType({"a": "A"})

    assert NeedsOne.get_config_hash({}) == NeedsOne.get_config_hash({})
    assert NeedsOne.get_config_hash({}) != NeedsOne.get_config_hash({"a": "1"})


def test_driver_store_settings():
    """Essential values cannot be stored as settings"""

    class NeedsOne(BareDriver):
        REQUIRES_SETTINGS = MappingProxyType({"a": "A"})

    driver = NeedsOne("id", {"a": "1"}, Mock())
    with pytest.raises(RuntimeError):
        driver.store_settings({"a": "2"})
    driver.store_settings({"other": "2"})
    assert driver.config["other"] == "2"


@pytest.fixture(name="configurator")
def fixture_configurator():
    """A configurator with the detected dummy cameras"""
    return CameraConfigurator(CameraController(Mock(), "", Mock()),
                              ConfigParser(),
                              "/dev/null",
                              drivers=[DummyDriver])


def test_configurator_add_errors(configurator):
    """Adding an already working or invalid camera fails"""
    camera_id = CameraDriver.make_hash("id1")
    with pytest.raises(CameraAlreadyConnected):
        configurator.add_camera(camera_id, {})
    with pytest.raises(ConfigError):
        configurator.add_camera("new", {"name": "no driver"})


def test_configurator_reset_to_defaults(configurator):
    """Only the essential config values survive the reset"""
    camera_id = CameraDriver.make_hash("id1")
    with pytest.raises(CameraNotFound):
        configurator.reset_to_defaults("missing")
    configurator.reset_to_defaults(camera_id)
    assert camera_id in configurator.loaded


def test_configurator_order_and_store(configurator):
    """Order ignores unknown cameras, store needs a loaded camera"""
    camera_id = CameraDriver.make_hash("id1")
    configurator.set_order(["unknown", camera_id])
    assert configurator.order[0] == camera_id
    with pytest.raises(CameraNotFound):
        configurator.store("unknown")
    configurator.store(camera_id)
    assert camera_id in configurator.stored

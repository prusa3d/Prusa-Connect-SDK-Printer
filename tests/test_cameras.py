"""Implements test for the camera related modules"""
from configparser import ConfigParser
from threading import Barrier, Event
from types import MappingProxyType
from typing import ClassVar
from unittest.mock import Mock

import pytest
from _pytest.python_api import raises

from prusa.connect.printer import get_timestamp
from prusa.connect.printer.camera import Camera, Resolution, Snapshot
from prusa.connect.printer.camera_configurator import CameraConfigurator
from prusa.connect.printer.camera_controller import CameraController
from prusa.connect.printer.camera_driver import CameraDriver
from prusa.connect.printer.const import (
    CapabilityType,
    DriverError,
    NotSupported,
    TriggerScheme,
)
from prusa.connect.printer.models import CameraRegister
from tests.util import SERVER, run_loop


class DummyDriver(CameraDriver):
    """It's a dummy driver for testing"""

    name = "Humpty Dumpty"
    REQUIRES_SETTINGS = MappingProxyType({
        "parameter":
        "A placeholder parameter for testing",
    })

    # Allows changes for tests with newly detected cameras
    scanned_cameras: ClassVar[dict] = {
        "id1": {
            "name": "Bad Camera 1",
            "parameter": "very parametric",
        },
        "id2": {
            "name": "Bad Camera 2",
            # "parameter" missing
        },
        "id3": {
            # everything missing
        },
    }

    @classmethod
    def _scan(cls):
        return cls.scanned_cameras

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.current_resolution = None

    def _connect(self):
        self._capabilities = ({
            CapabilityType.TRIGGER_SCHEME,
            CapabilityType.RESOLUTION,
            CapabilityType.IMAGING,
        })
        self._available_resolutions = ({Resolution(3, 3), Resolution(5, 5)})
        self._config.update({"resolution": "3x3"})
        if self.config["parameter"] == "fall over":
            self.fall_over()
        elif self.config["parameter"] == "screw up":
            raise RuntimeError("I just don't know what went wrong.")

    def take_a_photo(self):
        if self.config["parameter"] == "Camera shy":
            raise RuntimeError("Humpty was terrified of the camera, "
                               "so he dropped it")
        res = self.current_resolution
        return [[1] * res.width] * res.height

    def set_resolution(self, resolution):
        if self.config["parameter"] == "Driver error":  # Get it? :D
            raise RuntimeError(":( Your Humpty ran into a problem and needs "
                               "to restart.")
        self.current_resolution = resolution

    def fall_over(self):
        """Make Humpty stumble and fall over, at least he calls the handler"""
        raise RuntimeError("Humpty has fallen over")

    @property
    def is_registered(self):
        """Override the default for easier testing"""
        return True


class AdditionalCamera:
    def __enter__(self):
        """Adds a camera to a scan"""
        DummyDriver.scanned_cameras["extra"] = {
            "name": "Another one",
            "driver": "Humpty Dumpty",
            "parameter": "filled",
        }

    def __exit__(self, *_):
        """Tidies up removing the camera"""
        if "extra" in DummyDriver.scanned_cameras:
            del DummyDriver.scanned_cameras["extra"]


class GoodDriver(CameraDriver):
    """It's a feature complete dummy driver"""

    name = "GigaChad"

    @classmethod
    def _scan(cls):
        return {
            "EnormousCamera": {
                "name": "The most muscular camera you've ever seen",
                "resolution": "12288x6480",
                "rotation": "0",
                "exposure": "0",
                "focus": "0",
            },
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.current_resolution = None

    def _connect(self):
        self._capabilities = ({
            CapabilityType.TRIGGER_SCHEME,
            CapabilityType.RESOLUTION,
            CapabilityType.IMAGING,
            CapabilityType.ROTATION,
            CapabilityType.EXPOSURE,
            CapabilityType.FOCUS,
        })
        self._available_resolutions = ({Resolution(12288, 6480)})

    def set_resolution(self, resolution):
        self._config["resolution"] = resolution

    def set_rotation(self, rotation):
        self._config["rotation"] = rotation

    def set_exposure(self, exposure):
        self._config["exposure"] = exposure

    def set_focus(self, focus):
        self._config["focus"] = focus

    def take_a_photo(self):
        return "photo_data"


class EventSetMock(Mock):
    """
    Sets its built in event when called, otherwise it's a regular mock
    """
    def __init__(self, *args, side_effect=None, **kwargs):
        if side_effect is not None:
            raise AttributeError("Do not provide a side effect to this mock, "
                                 "it has its own waiting one")

        super().__init__(*args,
                         side_effect=lambda *args, **kwargs: self.event.set(),
                         **kwargs)
        self.event = Event()


def test_humpty_function():
    # Humpty tries to return configs, only one of them has everything needed
    available = DummyDriver.scan()
    id1 = CameraDriver.make_hash("id1")
    id2 = CameraDriver.make_hash("id2")
    id3 = CameraDriver.make_hash("id3")
    assert id1 in available
    assert id2 not in available
    assert id3 not in available
    available = DummyDriver.scan()
    driver = DummyDriver(id1, available[id1], Mock())
    driver.connect()
    del driver._config[CapabilityType.RESOLUTION.value]
    with raises(AttributeError):
        camera = Camera(driver)
    driver = DummyDriver(id1, available[id1], Mock())
    driver.connect()
    camera = Camera(driver)
    expected = {
        CapabilityType.TRIGGER_SCHEME,
        CapabilityType.RESOLUTION,
        CapabilityType.IMAGING,
    }
    differences = camera.capabilities.symmetric_difference(expected)
    assert differences == set()
    assert camera.resolution == Resolution(3, 3)
    assert camera.trigger_scheme == TriggerScheme.THIRTY_SEC
    camera.resolution = sorted(camera.available_resolutions)[1]
    assert camera.resolution == Resolution(5, 5)
    assert driver.current_resolution == Resolution(5, 5)
    camera.photo_cb = EventSetMock()
    camera.trigger_a_photo()
    camera.photo_cb.event.wait(0.1)
    camera.photo_cb.assert_called_once()
    snapshot = camera.photo_cb.call_args.args[0]
    assert snapshot.camera_fingerprint == camera.fingerprint
    assert len(snapshot.data) == 5
    assert len(snapshot.data[0]) == 5
    last = camera.last_snapshot.data
    assert len(last) == len(last[0]) == 5

    camera.scheme_cb = EventSetMock()
    camera.trigger_scheme = TriggerScheme.EACH_LAYER
    camera.scheme_cb.event.wait(0.1)
    camera.scheme_cb.assert_called_once()
    assert camera.scheme_cb.call_args.args[0] == camera
    assert camera.scheme_cb.call_args.args[1] == TriggerScheme.THIRTY_SEC
    assert camera.scheme_cb.call_args.args[2] == TriggerScheme.EACH_LAYER

    with raises(NotSupported):
        camera.rotation = 90

    driver.disconnected_cb.assert_not_called()
    driver.disconnect()
    driver.disconnected_cb.assert_called_with(driver)

    driver = DummyDriver(id1, available[id1], Mock())
    driver.connect()
    driver._capabilities.add(CapabilityType.EXPOSURE)
    with raises(DriverError):
        camera = Camera(driver)
        camera.exposure = 1

    driver = DummyDriver(
        "id4", {
            "name": "Camera with errors",
            "driver": "Humpty Dumpty",
            "parameter": "Driver error",
        }, Mock())
    driver.connect()
    assert driver.is_connected
    with raises(RuntimeError):
        driver.set_resolution(Resolution(5, 5))

    driver = DummyDriver(
        "id5", {
            "name": "Camera-shy camera",
            "driver": "Humpty Dumpty",
            "parameter": "Camera shy",
        }, Mock())
    driver.connect()
    assert driver.is_connected
    driver._photo_taker(Snapshot())
    driver.disconnected_cb.assert_called_once()


def test_configurator_from_config():
    id1 = CameraDriver.make_hash("id1")
    config = ConfigParser()
    config.read_dict({
        "camera_order": {
            "1": "derp",
            "3": str(id1),
        },
        f"camera::{id1}": {
            "name": "The best camera I own",
            "driver": "Humpty Dumpty",
            "parameter": "old value",
        },
        "camera::bar": {
            "name": "Disconnected Camera",
            "driver": "Humpty Dumpty",
            "parameter": "fall over",
        },
        "camera::derp": {
            "name": "Derpy is best pony",
            "driver": "Humpty Dumpty",
            "parameter": "screw up",
        },
        "camera::foo": {
            "name": "Missing required parameter Camera",
            "driver": "Humpty Dumpty",
        },
        "camera::asdf": {
            "name": "Made up Camera",
            "driver": "Non existent",
        },
    })
    configurator = CameraConfigurator(CameraController(Mock(), "", Mock()),
                                      config,
                                      "/dev/null",
                                      drivers=[DummyDriver])
    assert id1 in configurator.stored
    assert id1 in configurator.loaded
    assert configurator.loaded[id1].config["parameter"] == "very parametric"
    assert configurator.is_connected(id1)
    assert "bar" in configurator.stored
    assert "bar" in configurator.loaded
    assert not configurator.is_connected("bar")
    assert "foo" not in configurator.stored
    assert "foo" not in configurator.loaded
    assert not configurator.is_connected("foo")
    assert "asdf" not in configurator.stored
    assert "asdf" not in configurator.loaded
    assert not configurator.is_connected("asdf")

    driver_id1 = configurator.loaded[id1]
    assert driver_id1.is_connected

    driver_bar = configurator.loaded["bar"]
    assert not driver_bar.is_connected

    assert configurator.order == ["derp", str(id1), "bar"]


def test_duplicates():
    id1 = CameraDriver.make_hash("id1")
    config = ConfigParser()
    config.read_dict({
        "camera_order": {
            "1": "derp",
            "3": str(id1),
        },
        "camera::same_cfg_as_id1": {
            "name": "Duplicate bad camera",
            "driver": "Humpty Dumpty",
            "parameter": "very parametric",
        },
    })
    configurator = CameraConfigurator(CameraController(Mock(), "", Mock()),
                                      config,
                                      "/dev/null",
                                      drivers=[DummyDriver])
    assert id1 in configurator.detected
    assert id1 in configurator.loaded
    assert configurator.is_connected(id1)
    assert "same_cfg_as_id1" in configurator.stored
    assert "same_cfg_as_id1" in configurator.loaded
    assert not configurator.is_connected("same_cfg_as_id1")


def test_configurator_auto_add():
    configurator = CameraConfigurator(CameraController(Mock(), "", Mock()),
                                      ConfigParser(),
                                      "/dev/null",
                                      drivers=[DummyDriver])
    id1 = CameraDriver.make_hash("id1")
    assert id1 in configurator.order
    # Detected camera - not stored
    assert id1 not in configurator.stored
    assert id1 in configurator.loaded
    assert id1 in configurator.detected

    configurator.add_camera(
        "abc", {
            "name": "Camera-shy camera",
            "driver": "Humpty Dumpty",
            "parameter": "Camera shy",
        })
    assert "abc" in configurator.order
    assert "abc" in configurator.stored
    assert "abc" in configurator.loaded
    # Do not use trigger, that creates a thread and we do not want to deal
    # with synchronization in the tests
    configurator.loaded["abc"]._photo_taker(Snapshot())
    assert "abc" not in configurator.camera_controller
    assert not configurator.is_connected("abc")


def test_configurator_remove():
    configurator = CameraConfigurator(CameraController(Mock(), "", Mock()),
                                      ConfigParser(),
                                      "/dev/null",
                                      drivers=[DummyDriver])
    configurator.add_camera(
        "def", {
            "name": "A camera",
            "driver": "Humpty Dumpty",
            "parameter": "Nothing special",
        })
    assert "def" in configurator.loaded
    assert "def" in configurator.camera_controller
    configurator.remove_camera("def")
    assert "def" not in configurator.loaded
    assert "def" not in configurator.order
    assert "def" not in configurator.stored
    assert "def" not in configurator.camera_controller


def test_configurator_remove_detected():
    """Verifies that detected camera removal is impossible"""
    configurator = CameraConfigurator(CameraController(Mock(), "", Mock()),
                                      ConfigParser(),
                                      "/dev/null",
                                      drivers=[DummyDriver])
    id1 = CameraDriver.make_hash("id1")
    assert id1 in configurator.loaded
    with raises(RuntimeError):
        configurator.remove_camera(id1)


def test_reset_settings():
    """Tests that reset of the settings removes all but essential ones"""
    configurator = CameraConfigurator(CameraController(Mock(), "", Mock()),
                                      ConfigParser(),
                                      "/dev/null",
                                      drivers=[DummyDriver])
    configurator.add_camera(
        "def", {
            "name": "Not a camera",
            "driver": "Humpty Dumpty",
            "parameter": "Bass canon",
            "extra_garbage": "begone",
        })
    assert "def" in configurator.loaded
    assert "extra_garbage" in configurator.loaded["def"].config
    configurator.reset_to_defaults("def")
    assert "def" in configurator.loaded
    assert "extra_garbage" not in configurator.loaded["def"].config


def test_add_more():
    """Tests that if a new camera becomes available, calling load_cameras()
    works"""
    configurator = CameraConfigurator(CameraController(Mock(), "", Mock()),
                                      ConfigParser(),
                                      "/dev/null",
                                      drivers=[DummyDriver])
    extra = CameraDriver.make_hash("extra")
    with AdditionalCamera():
        assert extra not in configurator.loaded
        configurator.load_cameras()
        assert extra in configurator.loaded
        assert extra not in configurator.stored
        assert extra in configurator.order
        assert extra in configurator.detected
        assert configurator.is_connected(extra)


def test_update_disconnected():
    """Tests that configured disconnected cameras re-connect automatically"""
    configurator = CameraConfigurator(CameraController(Mock(), "", Mock()),
                                      ConfigParser(),
                                      "/dev/null",
                                      drivers=[DummyDriver])
    extra = CameraDriver.make_hash("extra")
    configurator.add_camera(
        extra, {
            "name": "Re-connecting Camera",
            "driver": "Humpty Dumpty",
            "parameter": "fall over",
        })
    assert extra in configurator.loaded
    assert not configurator.is_connected(extra)
    with AdditionalCamera():
        configurator.load_cameras()
        assert extra in configurator.loaded
        assert configurator.is_connected(extra)


RES_BOTH = 3


def test_camera_controller():
    controller = CameraController(Mock(), "", Mock())
    configurator = CameraConfigurator(controller,
                                      ConfigParser(),
                                      "/dev/null",
                                      drivers=[DummyDriver])

    id1 = CameraDriver.make_hash("id1")
    configurator.add_camera(
        "abc", {
            "name": "Second camera",
            "driver": "Humpty Dumpty",
            "parameter": "my imagination ran out",
            "trigger_scheme": "MANUAL",
        })

    camera_id1 = controller.get_camera(id1)
    camera_abc = controller.get_camera("abc")

    camera_id1.wait_ready(0.1)
    camera_abc.wait_ready(0.1)

    assert camera_abc in controller._trigger_piles[TriggerScheme.MANUAL]

    camera_id1.trigger_scheme = TriggerScheme.TEN_SEC
    camera_id1.resolution = Resolution(RES_BOTH, RES_BOTH)
    camera_abc.trigger_scheme = TriggerScheme.TEN_SEC
    camera_abc.resolution = Resolution(RES_BOTH, RES_BOTH)

    assert camera_id1 in controller._trigger_piles[TriggerScheme.TEN_SEC]
    assert camera_abc in controller._trigger_piles[TriggerScheme.TEN_SEC]

    barrier = Barrier(3)
    barrier_mock = Mock()

    def barrier_handler(*args, **kwargs):
        """If the test and both cameras wait on this barrier, they'll get
        through. A timeout means fail of the test"""
        barrier_mock(*args, **kwargs)
        barrier.wait()

    camera_id1.photo_cb = barrier_handler
    camera_abc.photo_cb = barrier_handler

    controller.trigger_pile(TriggerScheme.TEN_SEC)
    barrier.wait(0.1)
    assert barrier_mock.call_count == 2
    for arguments in barrier_mock.call_args_list:
        snapshot = arguments.args[0]
        assert snapshot.data == [[1] * RES_BOTH] * RES_BOTH
        assert snapshot.camera_fingerprint in {
            camera_abc.fingerprint,
            camera_id1.fingerprint,
        }

    camera_abc.wait_ready(0.1)
    camera_abc.trigger_scheme = TriggerScheme.EACH_LAYER
    assert camera_abc in controller._trigger_piles[TriggerScheme.EACH_LAYER]


def test_passing_printer_uuid():
    """Test that we can reliably pass a printer UUID
    through the photo call chain"""
    controller = CameraController(Mock(), "", Mock())
    configurator = CameraConfigurator(controller,
                                      ConfigParser(),
                                      "/dev/null",
                                      drivers=[DummyDriver])
    assert configurator is not None

    id1 = CameraDriver.make_hash("id1")
    camera_id1 = controller.get_camera(id1)

    camera_id1.wait_ready(0.1)

    camera_id1.trigger_scheme = TriggerScheme.MANUAL
    assert camera_id1 in controller._trigger_piles[TriggerScheme.MANUAL]

    barrier = Barrier(2)
    barrier_mock = Mock()

    def barrier_handler(*args, **kwargs):
        """If the test and both cameras wait on this barrier, they'll get
        through. A timeout means fail of the test"""
        barrier_mock(*args, **kwargs)
        barrier.wait()

    camera_id1.photo_cb = barrier_handler

    snapshot = Snapshot()
    snapshot.printer_uuid = "Rise and shine"
    camera_id1.trigger_a_photo(snapshot)
    barrier.wait(100.1)
    barrier_mock.assert_called()
    assert barrier_mock.call_args.args[0].printer_uuid == snapshot.printer_uuid


def test_setting_conversions():

    configurator = CameraConfigurator(CameraController(Mock(), "", Mock()),
                                      ConfigParser(),
                                      "/dev/null",
                                      drivers=[GoodDriver])
    enormous = CameraDriver.make_hash("EnormousCamera")
    assert enormous in configurator.loaded
    driver = configurator.loaded[enormous]
    assert {"resolution", "name", "exposure", "rotation",
            "focus"}.issubset(driver.config)
    camera = configurator.camera_controller.get_camera(enormous)
    exported_settings = camera.get_settings()
    assert {"resolution", "name", "exposure", "rotation",
            "focus"}.issubset(exported_settings)
    json_settings = Camera.json_from_settings(exported_settings)
    back_from_json = Camera.settings_from_json(json_settings)
    assert exported_settings == back_from_json
    string_settings = Camera.string_from_settings(exported_settings)
    back_from_string = Camera.settings_from_string(string_settings)
    assert exported_settings == back_from_string


@pytest.fixture()
def snapshot():
    snapshot = Snapshot()
    snapshot.camera_fingerprint = "test_fingerprint"
    snapshot.camera_token = "test_token"
    snapshot.camera_id = "test_id"
    snapshot.timestamp = get_timestamp()
    snapshot.data = b'1010'
    return snapshot


def test_snapshot(printer, snapshot):
    camera_controller = printer.camera_controller

    camera_controller.photo_handler(snapshot)
    item = camera_controller.snapshot_queue.get_nowait()
    assert isinstance(item, Snapshot)
    assert item.camera_fingerprint == "test_fingerprint"
    assert item.camera_token == "test_token"
    assert item.data == snapshot.data


def test_snapshot_loop(requests_mock, printer, snapshot):
    camera_controller = printer.camera_controller

    requests_mock.put(SERVER + "/c/snapshot", status_code=204)

    camera_controller.photo_handler(snapshot)
    run_loop(camera_controller.snapshot_loop)
    req = requests_mock.request_history[0]
    assert (str(req) == f"PUT {SERVER}/c/snapshot")
    assert req.headers["Fingerprint"] == snapshot.camera_fingerprint
    assert req.headers["Token"] == snapshot.camera_token
    assert req.headers["Content-Length"] == str(len(snapshot.data))


def test_camera_register(printer):
    camera_controller = printer.camera_controller
    # Want to hold a reference but flake8 said NO
    CameraConfigurator(camera_controller,
                       ConfigParser(),
                       "/dev/null",
                       drivers=[DummyDriver])
    id1 = CameraDriver.make_hash("id1")
    camera_controller.register_camera(camera_id=id1)
    camera = camera_controller.get_camera(id1)

    item = printer.queue.get_nowait()
    assert isinstance(item, CameraRegister)
    data = item.to_payload()
    assert data["fingerprint"] == camera.fingerprint
    assert data["config"]["camera_id"] == camera.camera_id


def test_camera_register_loop(requests_mock, printer):
    requests_mock.post(SERVER + "/p/camera", status_code=200)

    camera_controller = printer.camera_controller
    # Want to hold a reference but flake8 said NO
    CameraConfigurator(camera_controller,
                       ConfigParser(),
                       "/dev/null",
                       drivers=[DummyDriver])
    id1 = CameraDriver.make_hash("id1")
    camera_controller.register_camera(camera_id=id1)

    run_loop(fct=printer.loop)

    req = requests_mock.request_history[0]
    assert (str(req) == f"POST {SERVER}/p/camera")

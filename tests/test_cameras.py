"""Implements test for the camera related modules"""
from configparser import ConfigParser
from threading import Event, Barrier
from unittest.mock import Mock, call

from _pytest.python_api import raises

from prusa.connect.printer.camera_driver import CameraDriver
from prusa.connect.printer.camera import Camera, Resolution, Snapshot
from prusa.connect.printer.camera_controller import CameraController
from prusa.connect.printer.camera_configurator import CameraConfigurator
from prusa.connect.printer.models import CameraRegister
from prusa.connect.printer.const import CapabilityType, TriggerScheme, \
    NotSupported, DriverError
from tests.util import run_loop, SERVER, printer

# Shut up flake8, I'm importing a fixture!
assert printer  # type: ignore


class DummyDriver(CameraDriver):
    """It's a dummy driver for testing"""

    name = "Humpty Dumpty"
    REQUIRES_SETTINGS = {"parameter": "A placeholder parameter for testing"}

    @classmethod
    def _scan(cls):
        return {
            "id1": {
                "name": "Bad Camera 1",
                "parameter": "very parametric"
            },
            "id2": {
                "name": "Bad Camera 2",
                # "parameter" missing
            },
            "id3": {
                # everything missing
            }
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.current_resolution = None
        self._supported_capabilities = ({
            CapabilityType.TRIGGER_SCHEME, CapabilityType.RESOLUTION,
            CapabilityType.IMAGING
        })
        self._available_resolutions = ({Resolution(3, 3), Resolution(5, 5)})
        self._config.update({"resolution": "3x3"})
        if self.config["parameter"] == "fall over":
            self.fall_over()
        elif self.config["parameter"] == "screw up":
            raise RuntimeError("I just don't know what went wrong.")
        else:
            self._connected = True

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
        super().disconnect()

    @property
    def is_registered(self):
        """Override the default for easier testing"""
        return True


class GoodDriver(CameraDriver):
    """It's a feature complete dummy driver"""

    name = "GigaChad"

    @classmethod
    def _scan(cls):
        return {
            "EnormousCamera": {
                "name": "The most muscular camera you've ever seen",
                "resolution": "12288x6480",
                "exposure": "0",
                "rotation": "0"
            },
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.current_resolution = None
        self._supported_capabilities = ({
            CapabilityType.TRIGGER_SCHEME, CapabilityType.RESOLUTION,
            CapabilityType.IMAGING, CapabilityType.ROTATION,
            CapabilityType.EXPOSURE
        })
        self._available_resolutions = ({Resolution(12288, 6480)})
        self._connected = True

    def set_resolution(self, resolution):
        self._config["resolution"] = resolution

    def set_rotation(self, rotation):
        self._config["rotation"] = rotation

    def set_exposure(self, exposure):
        self._config["exposure"] = exposure

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
    id1 = CameraDriver.hash_id("id1")
    id2 = CameraDriver.hash_id("id2")
    id3 = CameraDriver.hash_id("id3")
    assert id1 in available
    assert id2 not in available
    assert id3 not in available
    available = DummyDriver.scan()
    driver = DummyDriver(id1, available[id1], Mock())
    del driver._config[CapabilityType.RESOLUTION.value]
    with raises(AttributeError):
        camera = Camera(driver)
    driver = DummyDriver(id1, available[id1], Mock())
    camera = Camera(driver)
    expected = {
        CapabilityType.TRIGGER_SCHEME, CapabilityType.RESOLUTION,
        CapabilityType.IMAGING
    }
    differences = camera.supported_capabilities.symmetric_difference(expected)
    assert differences == set()
    assert camera.resolution == Resolution(3, 3)
    assert camera.trigger_scheme == TriggerScheme.TEN_MIN
    camera.resolution = sorted(camera.available_resolutions)[1]
    assert camera.resolution == Resolution(5, 5)
    assert driver.current_resolution == Resolution(5, 5)
    camera.photo_cb = EventSetMock()
    camera.trigger_a_photo()
    camera.photo_cb.event.wait(0.1)
    camera.photo_cb.assert_called_once()
    assert camera.photo_cb.call_args.args[0] == camera
    assert len(camera.photo_cb.call_args.args[1]) == 5
    assert len(camera.photo_cb.call_args.args[1][0]) == 5
    last = camera.last_photo
    assert len(last) == len(last[0]) == 5

    camera.scheme_cb = EventSetMock()
    camera.trigger_scheme = TriggerScheme.EACH_LAYER
    camera.scheme_cb.event.wait(0.1)
    camera.scheme_cb.assert_called_once()
    assert camera.scheme_cb.call_args.args[0] == camera
    assert camera.scheme_cb.call_args.args[1] == TriggerScheme.TEN_MIN
    assert camera.scheme_cb.call_args.args[2] == TriggerScheme.EACH_LAYER

    with raises(NotSupported):
        camera.rotation = 90

    driver.disconnected_cb.assert_not_called()
    driver.fall_over()
    driver.disconnected_cb.assert_called_with(driver)

    driver = DummyDriver(id1, available[id1], Mock())
    driver._supported_capabilities.add(CapabilityType.EXPOSURE)
    with raises(DriverError):
        camera = Camera(driver)
        camera.exposure = 1

    driver = DummyDriver(
        "id4", {
            "name": "Camera with errors",
            "driver": "Humpty Dumpty",
            "parameter": "Driver error"
        }, Mock())
    assert driver.is_connected
    with raises(RuntimeError):
        driver.set_resolution(Resolution(5, 5))

    driver = DummyDriver(
        "id5", {
            "name": "Camera-shy camera",
            "driver": "Humpty Dumpty",
            "parameter": "Camera shy"
        }, Mock())
    assert driver.is_connected
    driver._photo_taker()
    driver.disconnected_cb.assert_called_once()


def test_configurator_from_config():
    id1 = CameraDriver.hash_id("id1")
    config = ConfigParser()
    config.read_dict({
        f"camera::{id1}": {
            "name": "The best camera I own",
            "driver": "Humpty Dumpty",
            "parameter": "old value"
        },
        "camera::bar": {
            "name": "Disconnected Camera",
            "driver": "Humpty Dumpty",
            "parameter": "fall over"
        },
        "camera::derp": {
            "name": "Derpy is best pony",
            "driver": "Humpty Dumpty",
            "parameter": "screw up"
        },
        "camera::foo": {
            "name": "Missing required parameter Camera",
            "driver": "Humpty Dumpty",
        },
        "camera::asdf": {
            "name": "Made up Camera",
            "driver": "Non existent"
        },
    })
    configurator = CameraConfigurator(CameraController(Mock(), "", Mock()),
                                      config,
                                      "/dev/null",
                                      drivers=[DummyDriver])
    assert id1 in configurator.order_known
    assert id1 in configurator.camera_configs
    assert id1 in configurator.loaded_drivers
    assert id1 not in configurator.disconnected_cameras
    assert "bar" in configurator.order_known
    assert "bar" in configurator.camera_configs
    assert "bar" in configurator.loaded_drivers
    assert "bar" in configurator.disconnected_cameras
    assert "foo" in configurator.order_known
    assert "foo" in configurator.camera_configs
    assert "foo" not in configurator.loaded_drivers
    assert "foo" not in configurator.disconnected_cameras
    assert "asdf" not in configurator.order_known
    assert "asdf" not in configurator.camera_configs
    assert "asdf" not in configurator.loaded_drivers
    assert "asdf" not in configurator.disconnected_cameras

    driver_id1 = configurator.loaded_drivers[id1]
    assert driver_id1.is_connected

    driver_bar = configurator.loaded_drivers["bar"]
    assert not driver_bar.is_connected

    config.set(section="camera::bar",
               option="parameter",
               value="don't fall over")
    configurator.refresh()
    assert "bar" in configurator.order_known
    assert "bar" in configurator.camera_configs
    assert "bar" in configurator.loaded_drivers
    assert "bar" not in configurator.disconnected_cameras


def test_configurator_add():
    configurator = CameraConfigurator(CameraController(Mock(), "", Mock()),
                                      ConfigParser(),
                                      "/dev/null",
                                      drivers=[DummyDriver])
    id1 = CameraDriver.hash_id("id1")
    configurator.add_camera(id1)
    assert id1 in configurator.camera_configs
    assert id1 in configurator.camera_order
    assert id1 in configurator.order_known
    assert id1 in configurator.loaded_drivers

    configurator.add_camera(
        "abc", {
            "name": "Camera-shy camera",
            "driver": "Humpty Dumpty",
            "parameter": "Camera shy"
        })
    assert "abc" in configurator.camera_configs
    assert "abc" in configurator.camera_order
    assert "abc" in configurator.order_known
    assert "abc" in configurator.loaded_drivers
    # Do not use trigger, that creates a thread and we do not want to deal
    # with synchronization in the tests
    configurator.loaded_drivers["abc"]._photo_taker()
    assert "abc" not in configurator.camera_controller
    assert "abc" in configurator.disconnected_cameras


def test_configurator_remove():
    configurator = CameraConfigurator(CameraController(Mock(), "", Mock()),
                                      ConfigParser(),
                                      "/dev/null",
                                      drivers=[DummyDriver])
    configurator.add_camera(
        "def", {
            "name": "A camera",
            "driver": "Humpty Dumpty",
            "parameter": "Nothing special"
        })
    assert "def" in configurator.loaded_drivers
    assert "def" in configurator.camera_controller
    configurator.remove_camera("def")
    assert "def" not in configurator.loaded_drivers
    assert "def" not in configurator.order_known
    assert "def" not in configurator.camera_order
    assert "def" not in configurator.camera_configs
    assert "def" not in configurator.camera_controller

    configurator.add_camera(
        "ghi", {
            "name": "Another camera",
            "driver": "Humpty Dumpty",
            "parameter": "Bass cannon"
        })
    configurator.loaded_drivers["ghi"].disconnect()
    configurator.remove_camera("ghi")
    assert "ghi" not in configurator.disconnected_cameras


def test_configurator_new():
    configurator = CameraConfigurator(CameraController(Mock(), "", Mock()),
                                      ConfigParser(),
                                      "/dev/null",
                                      drivers=[DummyDriver])

    id1 = CameraDriver.hash_id("id1")
    assert id1 in configurator.get_new_cameras()
    configurator.add_camera(id1)
    assert id1 in configurator.loaded_drivers
    assert id1 not in configurator.get_new_cameras()


RES_BOTH = 3


def test_camera_controller():
    controller = CameraController(Mock(), "", Mock())
    configurator = CameraConfigurator(controller,
                                      ConfigParser(),
                                      "/dev/null",
                                      drivers=[DummyDriver])

    id1 = CameraDriver.hash_id("id1")
    configurator.add_camera(id1)
    configurator.add_camera(
        "abc", {
            "name": "Second camera",
            "driver": "Humpty Dumpty",
            "parameter": "my imagination ran out",
            "trigger_scheme": "MANUAL"
        })

    camera_id1 = controller.get_camera(id1)
    camera_abc = controller.get_camera("abc")

    assert camera_abc in controller._trigger_piles[TriggerScheme.MANUAL]

    camera_id1.trigger_scheme = TriggerScheme.TEN_MIN
    camera_id1.resolution = Resolution(RES_BOTH, RES_BOTH)
    camera_abc.trigger_scheme = TriggerScheme.TEN_MIN
    camera_abc.resolution = Resolution(RES_BOTH, RES_BOTH)

    assert camera_id1 in controller._trigger_piles[TriggerScheme.TEN_MIN]
    assert camera_abc in controller._trigger_piles[TriggerScheme.TEN_MIN]

    barrier = Barrier(3)
    barrier_mock = Mock()

    def barrier_handler(*args, **kwargs):
        """If the test and both cameras wait on this barrier, they'll get
        through. A timeout means fail of the test"""
        barrier_mock(*args, **kwargs)
        barrier.wait()

    camera_id1.photo_cb = barrier_handler
    camera_abc.photo_cb = barrier_handler
    barrier_mock.assert_not_called()
    controller.trigger_pile(TriggerScheme.TEN_MIN)
    # a broken barrier is a fail - the callback was not called
    barrier.wait(0.1)
    barrier_mock.assert_has_calls(calls=[
        call(camera_id1, [[1] * RES_BOTH] * RES_BOTH),
        call(camera_abc, [[1] * RES_BOTH] * RES_BOTH)
    ],
                                  any_order=True)

    controller.get_camera("abc").trigger_scheme = TriggerScheme.EACH_LAYER
    assert camera_abc in controller._trigger_piles[TriggerScheme.EACH_LAYER]


def test_setting_conversions():

    configurator = CameraConfigurator(CameraController(Mock(), "", Mock()),
                                      ConfigParser(),
                                      "/dev/null",
                                      drivers=[GoodDriver])
    enormous = CameraDriver.hash_id("EnormousCamera")
    configurator.add_camera(enormous)
    driver = configurator.loaded_drivers[enormous]
    assert {"resolution", "name", "exposure", "rotation",
            "driver"}.issubset(driver.config)
    camera = configurator.camera_controller.get_camera(enormous)
    exported_settings = camera.get_settings()
    assert {"resolution", "name", "exposure", "rotation",
            "driver"}.issubset(exported_settings)
    json_settings = Camera.json_from_settings(exported_settings)
    back_from_json = Camera.settings_from_json(json_settings)
    assert exported_settings == back_from_json
    string_settings = Camera.string_from_settings(exported_settings)
    back_from_string = Camera.settings_from_string(string_settings)
    assert exported_settings == back_from_string


class MockCamera:
    def __init__(self):
        self.is_registered = True
        self.name = "Mock camera"
        self.fingerprint = "test_fingerprint"
        self.token = "test_token"


def test_snapshot(printer):
    camera_controller = printer.camera_controller

    data = b'1010'

    camera_controller.photo_handler(MockCamera(), data)
    item = camera_controller.snapshot_queue.get_nowait()
    assert isinstance(item, Snapshot)
    assert item.camera.fingerprint == "test_fingerprint"
    assert item.camera.token == "test_token"
    assert item.data == data


def test_snapshot_loop(requests_mock, printer):
    camera_controller = printer.camera_controller

    requests_mock.put(SERVER + "/c/snapshot", status_code=204)
    data = b'1010'
    camera = MockCamera()

    camera_controller.photo_handler(camera, data)
    run_loop(camera_controller.snapshot_loop)
    req = requests_mock.request_history[0]
    assert (str(req) == f"PUT {SERVER}/c/snapshot")
    assert req.headers["Fingerprint"] == camera.fingerprint
    assert req.headers["Token"] == camera.token
    assert req.headers["Content-Length"] == str(len(data))


def test_camera_register(printer):
    camera_controller = printer.camera_controller
    configurator = CameraConfigurator(camera_controller,
                                      ConfigParser(),
                                      "/dev/null",
                                      drivers=[DummyDriver])
    id1 = CameraDriver.hash_id("id1")
    configurator.add_camera(id1)
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
    configurator = CameraConfigurator(camera_controller,
                                      ConfigParser(),
                                      "/dev/null",
                                      drivers=[DummyDriver])
    id1 = CameraDriver.hash_id("id1")
    configurator.add_camera(id1)
    camera_controller.register_camera(camera_id=id1)

    run_loop(fct=printer.loop)

    req = requests_mock.request_history[0]
    assert (str(req) == f"POST {SERVER}/p/camera")

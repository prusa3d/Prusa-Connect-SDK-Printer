"""Tests for the small remaining gaps in the coverage"""
from configparser import ConfigParser
from queue import Empty
from unittest.mock import Mock

import pytest
import requests

from prusa.connect.printer import const
from prusa.connect.printer.camera import Camera, Resolution, Snapshot
from prusa.connect.printer.camera_configurator import CameraConfigurator
from prusa.connect.printer.camera_controller import CameraController
from prusa.connect.printer.command import Command
from prusa.connect.printer.conditions import (
    Condition,
    ConditionTracker,
    CondState,
)
from prusa.connect.printer.const import (
    DEFAULT_CAMERA_SETTINGS,
    CameraBusy,
    CapabilityType,
    DriverError,
    NotSupported,
    ReadyTimeoutError,
)
from prusa.connect.printer.errors import ErrorState
from prusa.connect.printer.models import Event
from tests.test_cameras import DummyDriver, GoodDriver


def test_condition_misc():
    """Unknown state is refused, str shows the state"""
    cond = Condition("c", "long")
    with pytest.raises(ValueError):
        cond.state = CondState.UNKNOWN
    assert str(cond).startswith("c: ")


def test_condition_tracker_worst_is_cached():
    """The worst condition is cached and tracking can be queried"""
    cond = Condition("c", "long")
    tracker = ConditionTracker()
    tracker.add_tracked_condition(cond)
    assert tracker.is_tracked(cond)
    cond.state = CondState.NOK
    assert tracker.get_worst() is cond
    assert tracker.get_worst() is cond


def test_error_misc():
    """Error is falsy when unknown, str shows the value"""
    error = ErrorState("e", "long")
    assert not error
    assert str(error) == "e: None"


def test_event_repr():
    """The event has a readable representation"""
    event = Event(const.Event.INFO, const.Source.WUI)
    assert "Event::" in repr(event)


def test_command_states():
    """Rejected, resent and failed commands notify Connect"""
    event_cb = Mock()
    cmd = Command(event_cb)
    assert cmd() is None  # nothing accepted yet
    cmd.accept(1, "SEND_INFO")
    # a priority command waits for the previous one to stop
    cmd.cmd_end_evt.set()
    cmd.stop_cb = Mock()
    assert cmd.check_state(2, const.Command.RESET_PRINTER.value)
    cmd.stop_cb.assert_called_once()
    # another command while one is running
    assert not cmd.check_state(2, "SEND_INFO")
    cmd.failed(const.Source.WUI, "reason")
    assert cmd.last_state == const.Event.FAILED


def test_command_priority_timeout(monkeypatch):
    """A priority command does not wait forever"""
    monkeypatch.setattr("prusa.connect.printer.command.ONE_SECOND_TIMEOUT",
                        0.01)
    cmd = Command(Mock())
    assert cmd.check_state(1, const.Command.RESET_PRINTER.value)


def test_snapshot_send(requests_mock):
    """An incomplete snapshot is still sent, the printer uuid is a param"""
    requests_mock.put("http://s/c/snapshot", text="ok")
    snapshot = Snapshot()
    snapshot.printer_uuid = "uuid"
    snapshot.data = b""
    snapshot.timestamp = 1
    res = snapshot.send(requests.Session(), "http://s")
    assert res.text == "ok"
    assert requests_mock.last_request.qs["printer_uuid"] == ["uuid"]


def make_camera(driver_cls, config=None):
    """A connected camera"""
    camera_id, camera_config = next(iter(driver_cls.scan().items()))
    driver = driver_cls(camera_id, config or camera_config, Mock())
    driver.connect()
    return Camera(driver)


def test_camera_behaviour():
    """Busy, stuck, timeouts, tokens and image access"""
    camera = make_camera(GoodDriver)
    camera.store_cb = Mock()
    camera.set_token("tok")
    assert camera.is_registered
    camera.set_settings({"token": "other", "name": "renamed"}, store=False)
    assert camera.token == "other"
    camera.set_settings({"rotation": 0})
    assert not camera.is_stuck
    camera._busy_since = 1
    assert camera.is_stuck
    camera._busy_since = None
    camera._ready_event.clear()
    with pytest.raises(ReadyTimeoutError):
        camera.wait_ready(0.01)
    with pytest.raises(CameraBusy):
        camera.trigger_a_photo()
    camera._ready_event.set()
    assert camera.configurable_capabilities
    assert camera.available_resolutions
    assert camera.supports(CapabilityType.IMAGING)


def test_camera_take_a_photo():
    """take_a_photo triggers a photo when idle"""
    camera = make_camera(GoodDriver)
    camera.trigger_a_photo = Mock()
    camera.wait_ready = Mock()
    camera.take_a_photo()
    camera.trigger_a_photo.assert_called_once()
    camera._capabilities = frozenset()
    with pytest.raises(NotSupported):
        camera.take_a_photo()


def test_controller_loop_survives_exception():
    """A broken item does not stop the snapshot loop"""
    controller = CameraController(Mock(), "s", Mock())

    def fail(*_):
        controller.stop()
        raise ValueError

    controller.snapshot_queue.put(Mock(send=fail))
    controller.snapshot_loop()


def test_configurator_second_load():
    """Configs can be loaded only once"""
    configurator = CameraConfigurator(CameraController(Mock(), "", Mock()),
                                      ConfigParser(),
                                      "/dev/null",
                                      drivers=[DummyDriver])
    with pytest.raises(RuntimeError):
        configurator.load_cameras(load_configs=True)


def test_camera_setting_unsupported():
    """A capability the driver lacks cannot be set"""
    camera = make_camera(GoodDriver)
    camera._capabilities = camera._capabilities - {CapabilityType.FOCUS}
    with pytest.raises(NotSupported):
        camera.focus = 0.5


def test_camera_resolution_unavailable():
    """An unavailable resolution is refused"""
    camera = make_camera(GoodDriver)
    with pytest.raises(DriverError):
        camera.resolution = Resolution(1, 1)


def test_camera_output_resolution():
    """No resolution capability means none, rotation reverses it"""
    camera = make_camera(GoodDriver)
    assert camera.output_resolution == camera.resolution
    camera.rotation = 90
    assert camera.output_resolution == reversed(camera.resolution)
    camera._capabilities = frozenset({CapabilityType.TRIGGER_SCHEME})
    assert camera.output_resolution is None


def test_camera_requires_trigger_scheme():
    """A driver must declare the trigger scheme capability"""
    driver = Mock(capabilities=set())
    with pytest.raises(AttributeError):
        Camera(driver)


def test_camera_unknown_trigger_scheme():
    """An unknown trigger scheme falls back to the default"""
    default = DEFAULT_CAMERA_SETTINGS[CapabilityType.TRIGGER_SCHEME.value]
    key = CapabilityType.TRIGGER_SCHEME.value
    assert Camera.settings_from_string({key: "nonsense"})[key] == default
    assert Camera.settings_from_json({key: "nonsense"})[key] == default


def test_camera_photo_without_imaging():
    """A driver without imaging must not deliver photos"""
    camera = make_camera(GoodDriver)
    camera._capabilities = frozenset({CapabilityType.TRIGGER_SCHEME})
    with pytest.raises(RuntimeError):
        camera._photo_handler(Snapshot())


def test_configurator_reset_failure(monkeypatch):
    """A failing re-add after the reset is only logged"""
    configurator = CameraConfigurator(CameraController(Mock(), "", Mock()),
                                      ConfigParser(),
                                      "/dev/null",
                                      drivers=[DummyDriver])
    camera_id = next(iter(configurator.loaded))
    monkeypatch.setattr(configurator, "add_camera", Mock(side_effect=OSError))
    configurator.reset_to_defaults(camera_id)


def test_configurator_order_keeps_rest():
    """Cameras missing in the new order stay behind it"""
    configurator = CameraConfigurator(CameraController(Mock(), "", Mock()),
                                      ConfigParser(),
                                      "/dev/null",
                                      drivers=[DummyDriver])
    configurator.order = ["old"]
    configurator.set_order([])
    assert configurator.order == ["old"]


def test_controller_loop_idle():
    """An empty queue just continues the loop"""
    controller = CameraController(Mock(), "s", Mock())
    controller.snapshot_queue = Mock()

    def empty(**_):
        controller.stop()
        raise Empty

    controller.snapshot_queue.get = empty
    controller.snapshot_loop()

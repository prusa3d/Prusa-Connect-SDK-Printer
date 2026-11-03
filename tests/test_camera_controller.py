"""Tests for the CameraController"""
from queue import Queue
from unittest.mock import Mock

import pytest

from prusa.connect.printer.camera_controller import CameraController
from prusa.connect.printer.const import CameraBusy, TriggerScheme
from prusa.connect.printer.models import CameraRegister


def make_camera(camera_id="cam", scheme=TriggerScheme.EACH_LAYER):
    """A fake camera"""
    camera = Mock()
    camera.camera_id = camera_id
    camera.trigger_scheme = scheme
    camera.is_stuck = False
    return camera


@pytest.fixture(name="controller")
def fixture_controller():
    """Controller with a fake camera added"""
    controller = CameraController(Mock(), "server", Mock())
    controller.add_camera(make_camera())
    return controller


def test_add_remove_camera(controller):
    """Cameras can be added, found and removed"""
    assert "cam" in controller
    assert controller.get_camera("cam").camera_id == "cam"
    assert [c.camera_id for c in controller.cameras_in_order] == []
    controller.set_camera_order(["cam", "missing"])
    assert [c.camera_id for c in controller.cameras_in_order] == ["cam"]
    controller.remove_camera("missing")
    controller.remove_camera("cam")
    assert "cam" not in controller


def test_disconnect_stuck_cameras(controller):
    """Only stuck cameras are disconnected"""
    camera = controller.get_camera("cam")
    assert controller.disconnect_stuck_cameras() == 0
    camera.is_stuck = True
    assert controller.disconnect_stuck_cameras() == 1
    camera.disconnect.assert_called_once()


def test_register_camera(controller):
    """Registration is sent for known cameras only"""
    controller.register_camera("missing")
    controller.send_cb.assert_not_called()
    controller.register_camera("cam")
    assert isinstance(controller.send_cb.call_args[0][0], CameraRegister)


def test_layer_trigger(controller):
    """Every layer triggers, every fifth layer triggers the other scheme"""
    camera = controller.get_camera("cam")
    fifth = make_camera("fifth", TriggerScheme.FIFTH_LAYER)
    controller.add_camera(fifth)
    for _ in range(5):
        controller.layer_trigger()
    assert camera.trigger_a_photo.call_count == 5
    fifth.trigger_a_photo.assert_called_once()


def test_trigger_busy_camera(controller):
    """A busy camera is skipped"""
    camera = controller.get_camera("cam")
    camera.trigger_a_photo.side_effect = CameraBusy
    controller.trigger_pile(TriggerScheme.EACH_LAYER)
    camera.trigger_a_photo.assert_called_once()


def test_tick(controller, monkeypatch):
    """Time based triggers fire only when the interval elapsed"""
    camera = make_camera("timed", TriggerScheme.SIXTY_SEC)
    controller.add_camera(camera)
    controller.tick()
    camera.trigger_a_photo.assert_not_called()
    monkeypatch.setattr("prusa.connect.printer.camera_controller.time",
                        lambda: 10**10)
    controller.tick()
    camera.trigger_a_photo.assert_called_once()


def test_scheme_handler(controller):
    """A camera moves between the piles"""
    camera = controller.get_camera("cam")
    controller.scheme_handler(camera, TriggerScheme.EACH_LAYER,
                              TriggerScheme.MANUAL)
    controller.trigger_pile(TriggerScheme.EACH_LAYER)
    camera.trigger_a_photo.assert_not_called()
    controller.trigger_pile(TriggerScheme.MANUAL)
    camera.trigger_a_photo.assert_called_once()


def test_photo_handler(controller):
    """Only sendable snapshots are queued"""
    controller.photo_handler(Mock(is_sendable=Mock(return_value=False)))
    assert controller.snapshot_queue.empty()
    controller.photo_handler(Mock(is_sendable=Mock(return_value=True)))
    assert controller.snapshot_queue.qsize() == 1


@pytest.mark.parametrize("status", [200, 400, 401, 500])
def test_snapshot_loop(controller, status):
    """The loop sends a snapshot and resets the token when unauthorized"""
    item = Mock(camera_id="cam")
    item.send.return_value = Mock(status_code=status, text="t")
    controller.snapshot_queue = Queue()
    controller.snapshot_queue.put(item)

    def stop(*_):
        controller.stop()
        return Mock(status_code=status, text="t")

    item.send.side_effect = stop
    controller.snapshot_loop()
    if status == 401:
        controller.get_camera("cam").set_token.assert_called_with(None)


def test_snapshot_loop_exception(controller):
    """An unexpected error does not kill the loop"""
    item = Mock(camera_id="cam")

    def fail(*_):
        controller.stop()
        raise ValueError

    item.send.side_effect = fail
    controller.snapshot_queue.put(item)
    controller.snapshot_loop()

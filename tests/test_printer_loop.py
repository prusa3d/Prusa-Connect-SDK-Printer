"""Tests for the Printer loop and the parsing of Connect responses"""
from unittest.mock import Mock

import pytest
import requests
from requests.exceptions import ConnectionError as RequestsConnectionError
from urllib3.exceptions import ReadTimeoutError

from prusa.connect.printer import Printer, const, errors
from prusa.connect.printer.models import (
    CameraRegister,
    LoopObject,
    Register,
)
from tests.util import FINGERPRINT, SERVER, SN, TOKEN


class FakeItem(LoopObject):
    """An item whose send is controlled by the test"""
    endpoint = "/fake"
    method = "GET"

    def __init__(self, result=None, needs_token=True):
        super().__init__()
        self.result = result
        self.needs_token = needs_token

    def send(self, conn, server, headers):
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def response(status=200, headers=None, text=""):
    """Fake response"""
    res = requests.Response()
    res.status_code = status
    res.headers.update(headers or {})
    res._content = text.encode()  # pylint: disable=protected-access
    return res


@pytest.fixture(name="printer")
def fixture_printer():
    """A printer with the connection set"""
    printer = Printer(const.PrinterType.I3MK3S, SN, FINGERPRINT)
    printer.set_connection(SERVER, TOKEN)
    yield printer
    for cond in (errors.INTERNET, errors.TOKEN, errors.API, errors.HTTP):
        cond.ok = False


def step(printer, item):
    """Run one loop step with the item"""
    printer.queue.put(item)
    printer.loop_step()


def test_loop_step_skips(printer):
    """Nothing is sent when it cannot be"""
    printer.loop_step()  # empty queue
    step(printer, "not a loop object")
    printer.token = None
    item = FakeItem(response())
    item.send = Mock()
    step(printer, item)
    assert not errors.TOKEN.ok
    printer.token = TOKEN
    printer.server = None
    step(printer, item)
    item.send.assert_not_called()


@pytest.mark.parametrize(("error", "cond"), [
    (ReadTimeoutError(Mock(), "u", "m"), errors.HTTP),
    (RequestsConnectionError(), errors.HTTP),
    (requests.RequestException(), errors.INTERNET),
    (RuntimeError(), errors.INTERNET),
])
def test_loop_step_send_errors(printer, error, cond):
    """Communication problems set the conditions"""
    cond.ok = True
    step(printer, FakeItem(error))
    assert not cond.ok


def test_loop_step_register(printer):
    """Register stores the token or is retried"""
    printer.register_handler = Mock()
    item = Register("code")
    step(printer, FakeItem(response(200, {"Token": "new"})))  # no-op
    printer.queue.put(item)
    item.send = lambda *_: response(200, {"Token": "new"})
    printer.loop_step()
    assert printer.token == "new"
    printer.register_handler.assert_called_with("new")

    retry = Register("code")
    retry.send = lambda *_: response(202)
    printer.queue.put(retry)
    printer.loop_step()
    assert printer.queue.get_nowait() is retry


@pytest.mark.parametrize("status", [200, 400, 500])
def test_loop_step_camera_register(printer, status):
    """The camera gets its token or the failure is logged"""
    camera = Mock()
    item = CameraRegister.__new__(CameraRegister)
    LoopObject.__init__(item)
    item.camera = camera
    item.send = lambda *_: response(status, {"Token": "t"}, "text")
    step(printer, item)
    if status == 200:
        camera.set_token.assert_called_with("t")
    else:
        camera.set_token.assert_not_called()


@pytest.mark.parametrize(("status", "cond"), [(403, errors.TOKEN),
                                              (500, errors.API)])
def test_deduce_state(printer, status, cond):  # noqa: ARG001
    """Error codes switch the conditions off"""
    cond.ok = True
    Printer.deduce_state_from_code(status)
    assert not cond.ok


def test_loop(printer):
    """The loop survives exceptions and stops on request"""
    printer.camera_controller.tick = Mock(side_effect=RuntimeError)

    def fail():
        printer.stop_loop()
        raise RuntimeError

    printer.loop_step = Mock(side_effect=fail)
    printer.loop()
    assert not printer.running_loop
    printer.camera_controller.tick.assert_called_once()
    printer.loop_step.assert_called_once()


def queued_events(printer):
    """Payloads of all events waiting in the queue"""
    events = []
    while not printer.queue.empty():
        events.append(printer.queue.get_nowait().to_payload())
    return events


def test_parse_command_ignored(printer):
    """No command and unexpected statuses produce no event"""
    assert printer.parse_command(response(204)).status_code == 204
    printer.parse_command(response(500))
    assert queued_events(printer) == []


@pytest.mark.parametrize(("headers", "body", "event", "reason"), [
    ({
        "Command-Id": "x",
    }, "", "REJECTED", "Invalid Command-Id header"),
    ({
        "Command-Id": "1",
        "content-type": "image/png",
    }, "", "REJECTED", "Invalid command content type"),
    ({
        "Command-Id": "1",
        "content-type": "application/json",
    }, '{"command": "X"}', "ACCEPTED", None),
    ({
        "Command-Id": "1",
        "content-type": "text/x.gcode",
        "Force": "1",
    }, "G28", "ACCEPTED", None),
])
def test_parse_command(printer, headers, body, event, reason):
    """Valid commands are accepted, the invalid ones are rejected"""
    printer.parse_command(response(200, headers, body))
    events = queued_events(printer)
    assert [e["event"] for e in events] == [event]
    assert events[0].get("reason") == reason
    if event == "ACCEPTED":
        assert printer.command.command_id == 1


def test_properties_set_once(printer):
    """Fingerprint, serial number and type cannot be changed"""
    assert printer.printed_file_cb() is None
    for name in ("fingerprint", "sn", "type"):
        assert getattr(printer, name)
        with pytest.raises(RuntimeError):
            setattr(printer, name, "other")

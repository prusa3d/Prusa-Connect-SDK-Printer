"""Tests for the Printer loop and the parsing of Connect responses"""
from unittest.mock import Mock

import pytest
import requests
from requests.exceptions import ConnectionError as RequestsConnectionError
from urllib3.exceptions import ReadTimeoutError

from prusa.connect.printer import Printer, const, errors
from prusa.connect.printer.models import (
    CameraRegister,
    Event,
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
    step(printer, FakeItem())
    assert not errors.TOKEN.ok
    printer.server = None
    step(printer, FakeItem())


@pytest.mark.parametrize(("error", "cond"), [
    (ReadTimeoutError(None, "u", "m"), errors.HTTP),
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


@pytest.mark.parametrize(("status", "cond"), [(403, errors.TOKEN),
                                              (500, errors.API)])
def test_deduce_state(status, cond):
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

    printer.loop_step = fail
    printer.loop()
    assert not printer.running_loop


def test_parse_command(printer):
    """Responses with bad commands are rejected, no command is ignored"""
    assert printer.parse_command(response(204)).status_code == 204
    printer.parse_command(response(500))
    printer.parse_command(response(200, {"Command-Id": "x"}))
    cases = [
        {
            "Command-Id": "1",
            "content-type": "application/json",
        },
        {
            "Command-Id": "1",
            "content-type": "text/x.gcode",
            "Force": "1",
        },
        {
            "Command-Id": "1",
            "content-type": "image/png",
        },
    ]
    for headers in cases:
        printer.parse_command(response(200, headers, '{"command": "X"}'))
    events = []
    while not printer.queue.empty():
        events.append(printer.queue.get_nowait())
    assert all(isinstance(e, Event) for e in events)


def test_properties_set_once(printer):
    """Fingerprint, serial number and type cannot be changed"""
    assert printer.printed_file_cb() is None
    for name in ("fingerprint", "sn", "type"):
        assert getattr(printer, name)
        with pytest.raises(RuntimeError):
            setattr(printer, name, "other")

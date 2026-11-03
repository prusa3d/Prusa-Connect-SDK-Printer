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
    Telemetry,
)
from tests.util import (
    FINGERPRINT,
    SERVER,
    SN,
    TOKEN,
    FakeWS,
    ws_force_gcode_command,
    ws_gcode_command,
    ws_json_command,
)


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
    printer.ws = FakeWS(printer.handle_ws_message)
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


@pytest.mark.parametrize("message", ["", "J0000002"])
def test_ws_message_too_short(printer, message):
    """A frame without the type and 8 digits of id is dropped"""
    printer.handle_ws_message(message)
    assert queued_events(printer) == []


def test_ws_message_file_transfer_ignored(printer):
    """File transfer blocks are not supported and silently dropped"""
    printer.handle_ws_message(f"T{1:08x}data")
    assert queued_events(printer) == []


@pytest.mark.parametrize(("message", "reason"), [
    ("Jxxxxxxxx{}", "Invalid command_id"),
    (f"J{1:08x}{{not json", "Invalid JSON body"),
])
def test_ws_message_rejected(printer, message, reason):
    """Malformed frames are rejected"""
    printer.handle_ws_message(message)
    events = queued_events(printer)
    assert [e["event"] for e in events] == ["REJECTED"]
    assert events[0]["reason"] == reason


@pytest.mark.parametrize(("message", "force"), [
    (ws_gcode_command(1, "G28"), False),
    (ws_force_gcode_command(1, "G28"), True),
])
def test_ws_message_gcode(printer, message, force):
    """GCode frames are accepted, F prefix means force"""
    printer.handle_ws_message(message)
    events = queued_events(printer)
    assert [e["event"] for e in events] == ["ACCEPTED"]
    assert printer.command.command_id == 1
    assert printer.command.force is force
    assert printer.command.kwargs == {"gcode": "G28"}


def test_ws_message_json(printer):
    """JSON frames are accepted with their args and kwargs"""
    printer.handle_ws_message(
        ws_json_command(1, {
            "command": "SEND_INFO",
            "args": [1],
            "kwargs": {
                "a": 2,
            },
        }))
    events = queued_events(printer)
    assert [e["event"] for e in events] == ["ACCEPTED"]
    assert printer.command.command_id == 1
    assert printer.command.args == [1]
    assert printer.command.kwargs == {"a": 2}


@pytest.mark.parametrize("message", [
    ws_json_command(2, {"command": "SEND_INFO"}),
    ws_gcode_command(2, "G28"),
])
def test_ws_message_busy(printer, message):
    """A command arriving while another one runs is rejected"""
    printer.handle_ws_message(ws_json_command(1, {"command": "SEND_INFO"}))
    queued_events(printer)
    printer.handle_ws_message(message)
    events = queued_events(printer)
    assert [e["event"] for e in events] == ["REJECTED"]
    assert events[0]["command_id"] == 2
    assert printer.command.command_id == 1


def test_ws_message_accept_error(printer):
    """An exception in accepting the command rejects it"""
    printer.command.check_state = Mock(side_effect=RuntimeError("boom"))
    for message in (ws_json_command(1, {"command": "X"}),
                    ws_gcode_command(2, "G28")):
        printer.handle_ws_message(message)
    events = queued_events(printer)
    assert [e["event"] for e in events] == ["REJECTED", "REJECTED"]
    assert [e["reason"] for e in events] == ["boom", "boom"]


def test_ws_url(printer):
    """The WS URL follows the scheme of the server"""
    printer.server = "https://connect.example"
    assert printer.ws_url() == "wss://connect.example/p/ws"
    printer.server = "http://localhost:8000"
    assert printer.ws_url() == "ws://localhost:8000/p/ws"


def test_ensure_ws_connected(printer):
    """The connection is opened once and reopened with a new token"""
    printer.ensure_ws_connected()
    printer.ensure_ws_connected()
    assert len(printer.ws.connect_calls) == 1
    assert printer.ws.connect_calls[0][0] == f"{printer.ws_url()}"
    printer.token = "other"
    printer.ensure_ws_connected()
    assert len(printer.ws.connect_calls) == 2

    printer.ws.disconnect()
    printer.token = None
    printer.ensure_ws_connected()
    assert len(printer.ws.connect_calls) == 2
    printer.token = "other"
    printer.server = None
    printer.ensure_ws_connected()
    assert len(printer.ws.connect_calls) == 2


@pytest.mark.parametrize("item", [
    Telemetry(const.State.IDLE, temp_nozzle=1),
    Event(const.Event.INFO, const.Source.WUI),
])
def test_loop_step_ws(printer, item):
    """Telemetry and events go over the WebSocket"""
    errors.API.ok = False
    step(printer, item)
    assert printer.ws.sent == [item.to_payload()]
    assert errors.API.ok


def test_loop_step_ws_no_connection(printer):
    """Items are dropped and INTERNET is set when WS cannot connect"""
    printer.ws.fail_connect = True
    errors.INTERNET.ok = True
    step(printer, Telemetry(const.State.IDLE))
    assert printer.ws.sent == []
    assert not errors.INTERNET.ok


def test_loop_step_ws_send_failed(printer):
    """HTTP is set when the connected socket fails to send"""
    printer.ws.fail_send = True
    errors.HTTP.ok = True
    step(printer, Telemetry(const.State.IDLE))
    assert not errors.HTTP.ok


def test_properties_set_once(printer):
    """Fingerprint, serial number and type cannot be changed"""
    assert printer.printed_file_cb() is None
    for name in ("fingerprint", "sn", "type"):
        assert getattr(printer, name)
        with pytest.raises(RuntimeError):
            setattr(printer, name, "other")

from typing import Optional, List, Any
from time import time

import pytest   # type: ignore
import requests # noqa

from prusa.connect.printer import Printer, Telemetry, Event, const, \
    Notifications
from prusa.connect.printer.connection import Connection


FINGERPRINT = "__fingerprint__"
SERVER = "http://server"
SN = "SN001002XP003"
MAC = "00:01:02:03:04:05"
FIRMWARE = "3.9.0rc2"
IP = "192.168.1.101"


@pytest.fixture()
def connection():
    return Connection(SERVER, FINGERPRINT)


class TestPrinter():
    def test_init(self, requests_mock, connection):
        requests_mock.post(SERVER+"/p/telemetry", status_code=204)

        printer = Printer(const.Printer.I3MK3S,
                          SN, MAC, FIRMWARE, IP, connection)
        printer.telemetry(Telemetry(const.State.READY))

        assert (str(requests_mock.request_history[0])
                == f"POST {SERVER}/p/telemetry")

    def test_set_handler(self):
        printer = Printer(const.Printer.I3MK3,
                          SN, MAC, FIRMWARE, IP, connection)

        def send_info(prn: Printer, args: Optional[List[Any]]) -> Any:
            pass
        printer.set_handler(const.Command.SEND_INFO, send_info)
        assert printer.handlers[const.Command.SEND_INFO] == send_info

    def test_decorator(self):
        printer = Printer(const.Printer.I3MK3,
                          SN, MAC, FIRMWARE, IP, connection)

        @printer.handler(const.Command.GCODE)
        def gcode(prn: Printer, gcode: str) -> None:
            pass
        assert printer.handlers[const.Command.GCODE] == gcode

    def test_send_info(self, requests_mock, connection):
        requests_mock.post(
            SERVER+"/p/telemetry",
            text='{"command":"SEND_INFO"}',
            headers={"Command-Id": "1", "Content-Type": "application/json"},
            status_code=200)

        printer = Printer(const.Printer.I3MK3S,
                          SN, MAC, FIRMWARE, IP, connection)

        printer.test_ok = False

        @printer.handler(const.Command.SEND_INFO)
        def send_info(prn, args):
            prn.test_ok = True

        printer.telemetry(Telemetry(const.State.READY))

        assert printer.test_ok

    def test_buildin_send_info(self, requests_mock, connection):
        requests_mock.post(
            SERVER+"/p/telemetry",
            text='{"command":"SEND_INFO"}',
            headers={"Command-Id": "1", "Content-Type": "application/json"},
            status_code=200)
        requests_mock.post(SERVER+"/p/events", status_code=204)

        printer = Printer(const.Printer.I3MK3,
                          SN, MAC, FIRMWARE, IP, connection)
        printer.telemetry(Telemetry(const.State.READY))

        assert (str(requests_mock.request_history[1])
                == f"POST {SERVER}/p/events")
        info = requests_mock.request_history[1].json()
        assert info["event"] == "INFO", info

    def test_unknown(self, requests_mock, connection):
        requests_mock.post(
            SERVER+"/p/telemetry",
            text='{"command": "STANDUP"}',
            headers={"Command-Id": "1", "Content-Type": "application/json"},
            status_code=200)
        requests_mock.post(SERVER+"/p/events", status_code=204)

        printer = Printer(const.Printer.I3MK3,
                          SN, MAC, FIRMWARE, IP, connection)
        printer.telemetry(Telemetry(const.State.READY))

        assert (str(requests_mock.request_history[1])
                == f"POST {SERVER}/p/events")
        info = requests_mock.request_history[1].json()
        assert info["event"] == "REJECTED", info
        assert info["data"]["reason"] == "Unknown command"

    def test_gcode(self, requests_mock, connection):
        requests_mock.post(
            SERVER+"/p/telemetry",
            text='G1 X10.0',
            headers={"Command-Id": "1", "Content-Type": "text/x.gcode"},
            status_code=200)
        requests_mock.post(SERVER+"/p/events", status_code=204)

        printer = Printer(const.Printer.I3MK3,
                          SN, MAC, FIRMWARE, IP, connection)

        @printer.handler(const.Command.GCODE)
        def gcode(prn: Printer, args: str):
            prn.event(Event(const.Event.ACCEPTED, const.Source.CONNECT,
                      int(time()), prn.command_id))

        printer.telemetry(Telemetry(const.State.READY))

        assert (str(requests_mock.request_history[1])
                == f"POST {SERVER}/p/events")
        info = requests_mock.request_history[1].json()
        assert info["event"] == "ACCEPTED", info

    def test_register(self, requests_mock, connection):
        mock_tmp_code = "f4c8996fb9"
        printer = Printer(const.Printer.I3MK3,
                          SN, MAC, FIRMWARE, IP, connection)
        requests_mock.post(
            SERVER+"/p/register",
            headers={"Temporary-Code": mock_tmp_code},
            status_code=200)

        tmp_code = printer.register()
        assert tmp_code == mock_tmp_code

    def test_register_400_no_mac(self, requests_mock, connection):
        printer = Printer(const.Printer.I3MK3,
                          SN, None, FIRMWARE, IP, connection)
        requests_mock.post(
            SERVER+"/p/register",
            status_code=400)

        with pytest.raises(RuntimeError):
            printer.register()

    def test_get_token(self, requests_mock, connection):
        tmp_code = "f4c8996fb9"
        token = "9TKC0M6mH7WNZTk4NbHG"
        printer = Printer(const.Printer.I3MK3,
                          SN, MAC, FIRMWARE, IP, connection)
        requests_mock.get(
            SERVER+"/p/register",
            headers={"Token": token},
            status_code=200)

        token_ = printer.get_token(tmp_code)
        assert token == token_

    def test_get_token_202(self, requests_mock, connection):
        """202 - `tmp_code` is fine but the printer has not yet been added to
        Connect."""
        tmp_code = "f4c8996fb9"
        printer = Printer(const.Printer.I3MK3,
                          SN, MAC, FIRMWARE, IP, connection)
        requests_mock.get(
            SERVER+"/p/register",
            status_code=202)

        assert printer.get_token(tmp_code) is None

    def test_get_token_invalid_code(self, requests_mock, connection):
        tmp_code = "invalid_tmp_code"
        printer = Printer(const.Printer.I3MK3,
                          SN, MAC, FIRMWARE, IP, connection)
        requests_mock.get(
            SERVER+"/p/register",
            status_code=400)

        with pytest.raises(RuntimeError):
            printer.get_token(tmp_code)


def test_notification_handler():
    code = "SERVICE_UNAVAILABLE"
    msg = "Service is unavailable at this moment."

    def cb(code, msg):
        return (code, msg)

    Notifications.handler = cb

    res = Notifications.handler(code, msg)
    assert res == (code, msg)

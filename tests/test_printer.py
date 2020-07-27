from typing import Optional, List, Any
from time import time

import pytest   # type: ignore
import requests # noqa

from prusa.connect.printer import Printer, Telemetry, Event, const
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

    def test_set_command(self):
        printer = Printer(const.Printer.I3MK3,
                          SN, MAC, FIRMWARE, IP, connection)

        def send_info(prn: Printer, args: Optional[List[Any]]) -> Any:
            pass
        printer.set_command(const.Command.SEND_INFO, send_info)
        assert printer.handlers[const.Command.SEND_INFO] == send_info

    def test_decorator(self):
        printer = Printer(const.Printer.I3MK3,
                          SN, MAC, FIRMWARE, IP, connection)

        @printer.command(const.Command.GCODE)
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

        @printer.command(const.Command.SEND_INFO)
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

        @printer.command(const.Command.GCODE)
        def gcode(prn: Printer, args: str):
            prn.event(Event(const.Event.ACCEPTED, const.Source.CONNECT,
                      int(time()), prn.command_id))

        printer.telemetry(Telemetry(const.State.READY))

        assert (str(requests_mock.request_history[1])
                == f"POST {SERVER}/p/events")
        info = requests_mock.request_history[1].json()
        assert info["event"] == "ACCEPTED", info

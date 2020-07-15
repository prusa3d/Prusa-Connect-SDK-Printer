import pytest   # type: ignore
import requests # noqa

from prusa.connect.printer import Printer, Telemetry, types
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

        printer = Printer(types.Printer.I3, types.Version.MK3S,
                          SN, MAC, FIRMWARE, IP, connection)
        printer.telemetry(Telemetry(types.State.READY))

        assert (str(requests_mock.request_history[0])
                == f"POST {SERVER}/p/telemetry")

    def test_send_info(self, requests_mock, connection):
        requests_mock.post(
            SERVER+"/p/telemetry",
            text='{"command":"SEND_INFO"}',
            headers={"Command-Id": "1"},
            status_code=200)
        requests_mock.post(SERVER+"/p/events", status_code=204)

        printer = Printer(types.Printer.I3, types.Version.MK3S,
                          SN, MAC, FIRMWARE, IP, connection)
        printer.telemetry(Telemetry(types.State.READY))

        assert (str(requests_mock.request_history[1])
                == f"POST {SERVER}/p/events")

"""Test for Printer object."""
import tempfile
from typing import Optional, List, Any

import pytest  # type: ignore
import requests  # noqa pylint: disable=unused-import

from prusa.connect.printer import Printer, Telemetry, const, \
    Notifications
from prusa.connect.printer.connection import Connection

# pylint: disable=missing-function-docstring
# pylint: disable=no-self-use
# pylint: disable=redefined-outer-name

FINGERPRINT = "__fingerprint__"
SN = "SN001002XP003"
MAC = "00:01:02:03:04:05"
FIRMWARE = "3.9.0rc2"
IP = "192.168.1.101"
TOKEN = "a44b552a12d96d3155cb"
CONNECT_HOST = "server"
CONNECT_PORT = 8000
SERVER = f"http://{CONNECT_HOST}:{CONNECT_PORT}"


@pytest.fixture()
def connection():
    """Connectinon fixture."""
    return Connection(SERVER, FINGERPRINT)


@pytest.fixture(scope="session")
def lan_settings_ini():
    """Temporary lan_settings.ini file fixture."""
    tmpf = tempfile.NamedTemporaryFile(mode="w", delete=False)
    tmpf.write(f"""
[lan_ip4]
type=DHCP
hostname=MINI
address={IP}
mask=0.0.0.0
gateway=0.0.0.0
dns1=0.0.0.0
dns2=0.0.0.0

[connect]
address={CONNECT_HOST}
port={CONNECT_PORT}
token={TOKEN}
tls=False
""")
    tmpf.close()
    return tmpf.name


class TestPrinter:
    """Tests for Printer class."""
    def test_init(self, requests_mock, connection):
        requests_mock.post(SERVER + "/p/telemetry", status_code=204)

        printer = Printer(const.Printer.I3MK3S, SN, connection)
        Telemetry(const.State.READY)(printer.conn)

        assert (str(
            requests_mock.request_history[0]) == f"POST {SERVER}/p/telemetry")

    def test_set_handler(self):
        printer = Printer(const.Printer.I3MK3, SN, connection)

        def send_info(args: Optional[List[Any]]) -> Any:
            assert args

        printer.set_handler(const.Command.SEND_INFO, send_info)
        # pylint: disable=comparison-with-callable
        assert printer.command.handlers[const.Command.SEND_INFO] == send_info

    def test_decorator(self):
        printer = Printer(const.Printer.I3MK3, SN, connection)

        @printer.handler(const.Command.GCODE)
        def gcode(gcode: str) -> None:
            assert gcode

        # pylint: disable=comparison-with-callable
        assert printer.command.handlers[const.Command.GCODE] == gcode

    def test_send_info(self, requests_mock, connection):
        requests_mock.post(SERVER + "/p/telemetry",
                           text='{"command":"SEND_INFO"}',
                           headers={
                               "Command-Id": "1",
                               "Content-Type": "application/json"
                           },
                           status_code=200)

        printer = Printer(const.Printer.I3MK3S, SN, connection)

        # pylint: disable=unused-variable,unused-argument
        @printer.handler(const.Command.SEND_INFO)
        def send_info(args):
            return dict(source=const.Source.MARLIN)

        res = Telemetry(const.State.READY)(printer.conn)
        printer.parse_command(res)

        assert printer.command.state == const.Event.ACCEPTED
        assert not printer.events.empty()
        event = printer.events.get_nowait()
        assert event.event == const.Event.ACCEPTED

        printer.command()  # run the command
        assert not printer.events.empty()
        event = printer.events.get_nowait()
        assert event.source == const.Source.MARLIN
        assert event.event == const.Event.FINISHED, event

    def test_buildin_send_info(self, requests_mock, connection):
        requests_mock.post(SERVER + "/p/telemetry",
                           text='{"command":"SEND_INFO"}',
                           headers={
                               "Command-Id": "1",
                               "Content-Type": "application/json"
                           },
                           status_code=200)
        requests_mock.post(SERVER + "/p/events", status_code=204)

        printer = Printer(const.Printer.I3MK3, SN, connection)
        res = Telemetry(const.State.READY)(printer.conn)
        printer.parse_command(res)

        assert printer.command.state == const.Event.ACCEPTED
        assert not printer.events.empty()
        event = printer.events.get_nowait()
        assert event.event == const.Event.ACCEPTED
        event(connection)  # send info to mock

        printer.command()  # run the command
        assert not printer.events.empty()
        event = printer.events.get_nowait()
        assert event.event == const.Event.INFO, event
        event(connection)  # send info to mock

        assert (str(
            requests_mock.request_history[1]) == f"POST {SERVER}/p/events")
        info = requests_mock.request_history[2].json()
        assert info["event"] == "INFO", info

    def test_unknown(self, requests_mock, connection):
        requests_mock.post(SERVER + "/p/telemetry",
                           text='{"command": "STANDUP"}',
                           headers={
                               "Command-Id": "1",
                               "Content-Type": "application/json"
                           },
                           status_code=200)
        requests_mock.post(SERVER + "/p/events", status_code=204)

        printer = Printer(const.Printer.I3MK3, SN, connection)
        res = Telemetry(const.State.READY)(printer.conn)
        printer.parse_command(res)

        assert printer.command.state == const.Event.ACCEPTED
        assert not printer.events.empty()
        event = printer.events.get_nowait()
        assert event.event == const.Event.ACCEPTED
        event(connection)  # send info to mock

        printer.command()  # run the command
        assert not printer.events.empty()
        event = printer.events.get_nowait()
        assert event.event == const.Event.REJECTED, event
        event(connection)  # send answer to mock

        assert (str(
            requests_mock.request_history[2]) == f"POST {SERVER}/p/events")
        info = requests_mock.request_history[2].json()
        assert info["event"] == "REJECTED", info
        assert info["data"]["reason"] == "Unknown command"

    def test_gcode(self, requests_mock, connection):
        requests_mock.post(SERVER + "/p/telemetry",
                           text='G1 X10.0',
                           headers={
                               "Command-Id": "1",
                               "Content-Type": "text/x.gcode"
                           },
                           status_code=200)
        requests_mock.post(SERVER + "/p/events", status_code=204)

        printer = Printer(const.Printer.I3MK3, SN, connection)

        # pylint: disable=unused-variable, unused-argument
        @printer.handler(const.Command.GCODE)
        def gcode(args: List[str]):
            return dict(source=const.Source.MARLIN)

        res = Telemetry(const.State.READY)(printer.conn)
        printer.parse_command(res)
        printer.events.get_nowait()(connection)  # send accepted to mock
        printer.command()  # run the command
        printer.events.get_nowait()(connection)  # send finisged to mock

        assert (str(
            requests_mock.request_history[1]) == f"POST {SERVER}/p/events")
        info = requests_mock.request_history[1].json()
        assert info["event"] == "ACCEPTED", info
        assert (str(
            requests_mock.request_history[2]) == f"POST {SERVER}/p/events")
        info = requests_mock.request_history[2].json()
        assert info["event"] == "FINISHED", info

    def test_register(self, requests_mock, connection):
        mock_tmp_code = "f4c8996fb9"
        printer = Printer(const.Printer.I3MK3, SN, connection)
        requests_mock.post(SERVER + "/p/register",
                           headers={"Temporary-Code": mock_tmp_code},
                           status_code=200)

        tmp_code = printer.register()
        assert tmp_code == mock_tmp_code

    def test_register_400_no_mac(self, requests_mock, connection):
        printer = Printer(const.Printer.I3MK3, SN, connection)
        requests_mock.post(SERVER + "/p/register", status_code=400)

        with pytest.raises(RuntimeError):
            printer.register()

    def test_get_token(self, requests_mock, connection):
        tmp_code = "f4c8996fb9"
        token = "9TKC0M6mH7WNZTk4NbHG"
        printer = Printer(const.Printer.I3MK3, SN, connection)
        requests_mock.get(SERVER + "/p/register",
                          headers={"Token": token},
                          status_code=200)

        token_ = printer.get_token(tmp_code)
        assert token == token_

    def test_get_token_202(self, requests_mock, connection):
        """202 - `tmp_code` is fine but the printer has not yet been added to
        Connect."""
        tmp_code = "f4c8996fb9"
        printer = Printer(const.Printer.I3MK3, SN, connection)
        requests_mock.get(SERVER + "/p/register", status_code=202)

        assert printer.get_token(tmp_code) is None

    def test_get_token_invalid_code(self, requests_mock, connection):
        tmp_code = "invalid_tmp_code"
        printer = Printer(const.Printer.I3MK3, SN, connection)
        requests_mock.get(SERVER + "/p/register", status_code=400)

        with pytest.raises(RuntimeError):
            printer.get_token(tmp_code)

    def test_load_lan_settings(self, lan_settings_ini):
        printer = Printer.from_config(lan_settings_ini, FINGERPRINT,
                                      const.Printer.I3MK3, SN)
        assert printer.conn.token == TOKEN
        assert printer.conn.fingerprint == FINGERPRINT
        assert printer.conn.server == f"http://{CONNECT_HOST}:{CONNECT_PORT}"

    def test_from_lan_settings_not_found(self):
        with pytest.raises(FileNotFoundError):
            Printer.from_config("some_non-existing_file", FINGERPRINT,
                                const.Printer.I3MK3, SN)


def test_notification_handler():
    code = "SERVICE_UNAVAILABLE"
    msg = "Service is unavailable at this moment."

    def cb(code, msg):
        return (code, msg)

    Notifications.handler = cb

    # pylint: disable=assignment-from-no-return
    res = Notifications.handler(code, msg)
    assert res == (code, msg)

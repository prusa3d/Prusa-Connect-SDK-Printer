"""Test for Printer object."""
import tempfile

from typing import Optional, List, Any

import pytest  # type: ignore
import requests  # noqa pylint: disable=unused-import
from func_timeout import func_timeout, FunctionTimedOut  # type: ignore

from prusa.connect.printer import Printer, const, Notifications
from prusa.connect.printer.models import Telemetry, Event

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


@pytest.fixture()
def printer():
    """Printer object as fixture."""
    printer = Printer(const.PrinterType.I3MK3S, SN, SERVER, TOKEN)
    return printer


class TestPrinter:
    """Tests for Printer class."""
    def test_init(self, printer):
        assert printer

    def test_telemetry(self, printer):
        printer.telemetry(const.State.READY)
        item = printer.queue.get_nowait()

        assert isinstance(item, Telemetry)
        assert item.to_payload() == {'state': 'READY'}

    def test_event(self, printer):
        printer.event_cb(const.Event.INFO, const.Source.WUI)
        item = printer.queue.get_nowait()
        assert isinstance(item, Event)
        assert item.event == const.Event.INFO
        assert item.source == const.Source.WUI

    def test_loop(self, requests_mock, printer):
        requests_mock.post(SERVER + "/p/events", status_code=204)
        printer.event_cb(const.Event.INFO, const.Source.WUI)

        try:
            func_timeout(0.1, printer.loop)
        except FunctionTimedOut:
            pass

        assert (str(
            requests_mock.request_history[0]) == f"POST {SERVER}/p/events")
        info = requests_mock.request_history[0].json()
        assert info["event"] == "INFO"
        assert info["source"] == "WUI"

    def test_set_handler(self, printer):
        def send_info(args: Optional[List[Any]]) -> Any:
            assert args

        printer.set_handler(const.Command.SEND_INFO, send_info)
        # pylint: disable=comparison-with-callable
        assert printer.command.handlers[const.Command.SEND_INFO] == send_info

    def test_decorator(self, printer):
        @printer.handler(const.Command.GCODE)
        def gcode(gcode: str) -> None:
            assert gcode

        # pylint: disable=comparison-with-callable
        assert printer.command.handlers[const.Command.GCODE] == gcode

    def test_send_info(self, requests_mock, printer):
        """Test parsing telemetry and call builtin handler."""
        requests_mock.post(SERVER + "/p/telemetry",
                           text='{"command":"SEND_INFO"}',
                           headers={
                               "Command-Id": "42",
                               "Content-Type": "application/json"
                           },
                           status_code=200)
        requests_mock.post(SERVER + "/p/events", status_code=204)

        printer.telemetry(const.State.READY)

        try:
            func_timeout(0.1, printer.loop)
        except FunctionTimedOut:
            pass

        assert printer.command.state == const.Event.ACCEPTED
        assert (str(
            requests_mock.request_history[1]) == f"POST {SERVER}/p/events")
        info = requests_mock.request_history[1].json()
        assert info["event"] == "ACCEPTED"
        assert info["source"] == "CONNECT"
        assert info["command_id"] == 42

        printer.command()

        try:
            func_timeout(0.1, printer.loop)
        except FunctionTimedOut:
            pass

        assert (str(
            requests_mock.request_history[2]) == f"POST {SERVER}/p/events")
        info = requests_mock.request_history[2].json()
        assert info["event"] == "INFO"
        assert info["source"] == "CONNECT"
        assert info["command_id"] == 42

    def test_gcode(self, requests_mock, printer):
        """Test parsing telemetry and call GCODE handler."""
        requests_mock.post(SERVER + "/p/telemetry",
                           text='G1 X10.0',
                           headers={
                               "Command-Id": "1",
                               "Content-Type": "text/x.gcode"
                           },
                           status_code=200)
        requests_mock.post(SERVER + "/p/events", status_code=204)

        # pylint: disable=unused-variable, unused-argument
        @printer.handler(const.Command.GCODE)
        def gcode(args: List[str]):
            return dict(source=const.Source.MARLIN)

        printer.telemetry(const.State.READY)

        try:
            func_timeout(0.1, printer.loop)
        except FunctionTimedOut:
            pass

        assert (str(
            requests_mock.request_history[1]) == f"POST {SERVER}/p/events")
        info = requests_mock.request_history[1].json()
        assert info["event"] == "ACCEPTED", info

        printer.command()

        try:
            func_timeout(0.1, printer.loop)
        except FunctionTimedOut:
            pass

        assert (str(
            requests_mock.request_history[2]) == f"POST {SERVER}/p/events")
        info = requests_mock.request_history[2].json()
        assert info["event"] == "FINISHED", info

    def test_register(self, requests_mock):
        mock_tmp_code = "f4c8996fb9"
        requests_mock.post(SERVER + "/p/register",
                           headers={"Temporary-Code": mock_tmp_code},
                           status_code=200)
        printer = Printer(const.PrinterType.I3MK3, SN, SERVER)
        tmp_code = printer.register()
        assert tmp_code == mock_tmp_code

    def test_register_400_no_mac(self, requests_mock, printer):
        requests_mock.post(SERVER + "/p/register", status_code=400)

        with pytest.raises(RuntimeError):
            printer.register()

    def test_get_token(self, requests_mock, printer):
        tmp_code = "f4c8996fb9"
        token = "9TKC0M6mH7WNZTk4NbHG"
        requests_mock.get(SERVER + "/p/register",
                          headers={"Token": token},
                          status_code=200)

        token_ = printer.get_token(tmp_code)
        assert token == token_

    def test_get_token_202(self, requests_mock, printer):
        """202 - `tmp_code` is fine but the printer has not yet been added to
        Connect."""
        tmp_code = "f4c8996fb9"
        requests_mock.get(SERVER + "/p/register", status_code=202)

        assert printer.get_token(tmp_code) is None

    def test_get_token_invalid_code(self, requests_mock, printer):
        tmp_code = "invalid_tmp_code"
        requests_mock.get(SERVER + "/p/register", status_code=400)

        with pytest.raises(RuntimeError):
            printer.get_token(tmp_code)

    def test_load_lan_settings(self, lan_settings_ini):
        printer = Printer.from_config(lan_settings_ini, const.PrinterType.I3MK3,
                                      SN)
        assert printer.token == TOKEN
        assert printer.server == f"http://{CONNECT_HOST}:{CONNECT_PORT}"

    def test_from_lan_settings_not_found(self):
        with pytest.raises(FileNotFoundError):
            Printer.from_config("some_non-existing_file", const.PrinterType.I3MK3,
                                SN)


def test_notification_handler():
    code = "SERVICE_UNAVAILABLE"
    msg = "Service is unavailable at this moment."

    def cb(code, msg):
        return (code, msg)

    Notifications.handler = cb

    # pylint: disable=assignment-from-no-return
    res = Notifications.handler(code, msg)
    assert res == (code, msg)

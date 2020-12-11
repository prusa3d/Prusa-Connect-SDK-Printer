"""Test for Printer object."""
import queue
import tempfile
from typing import Any

import pytest  # type: ignore
import requests
from func_timeout import func_timeout, FunctionTimedOut  # type: ignore

from prusa.connect.printer import Printer, const, Notifications, Command, \
    Register
from prusa.connect.printer.models import Telemetry, Event
from prusa.connect.printer.errors import SDKServerError, SDKConnectionError

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
    printer = Printer(const.PrinterType.I3MK3S, SN, FINGERPRINT)
    printer.server = SERVER
    printer.token = TOKEN
    return printer


@pytest.fixture()
def printer_no_fp():
    """Printer without fingerprint."""
    printer = Printer(const.PrinterType.I3MK3S, SN)
    printer.server = SERVER
    printer.token = TOKEN
    return printer


class TestPrinter:
    """Tests for Printer class."""
    def test_init(self, printer):
        assert printer

        assert printer.is_initialised()
        with pytest.raises(RuntimeError):
            printer.fingerprint = "foo"

    def test_no_fingerprint(self, printer_no_fp):
        """Create a use a printer with no fingerprint"""
        assert printer_no_fp.is_initialised() is False

        # setting fingerprint one time is allowed
        printer_no_fp.fingerprint = "foo"
        assert printer_no_fp.fingerprint == "foo"

        # twice is not
        with pytest.raises(RuntimeError):
            printer_no_fp.fingerprint = "bar"

        assert printer_no_fp.is_initialised() is True

    def test_telemetry(self, printer):
        printer.telemetry(const.State.READY)
        item = printer.queue.get_nowait()

        assert isinstance(item, Telemetry)
        assert item.to_payload() == {'state': 'READY'}

    def test_telemetry_no_fingerprint(self, printer_no_fp):
        printer_no_fp.telemetry(const.State.READY, temp_bed=1, temp_nozzle=2)
        item = printer_no_fp.queue.get_nowait()
        assert isinstance(item, Telemetry)
        assert item.to_payload() == {'state': 'READY'}

    def test_parse_command_no_fingerprint(self, printer_no_fp):
        res_mock = requests.Response()
        res_mock.status_code = 200
        res_mock.headers['Command-Id'] = 42

        printer_no_fp.parse_command(res_mock)
        item = printer_no_fp.queue.get_nowait()
        assert isinstance(item, Event)
        event_obj = item.to_payload()
        assert event_obj['event'] == 'REJECTED'
        assert event_obj['source'] == 'WUI'
        assert event_obj['data']['reason'] == \
               'Printer has not been initialized properly'

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

    def test_loop_exception(self, requests_mock, printer):
        requests_mock.post(SERVER + "/p/events",
                           status_code=400,
                           json={'message': 'No Way'})
        printer.event_cb(const.Event.INFO, const.Source.WUI)
        with pytest.raises(SDKServerError):
            printer.loop()

        requests_mock.post(SERVER + "/p/events",
                           exc=requests.exceptions.ConnectTimeout)
        printer.event_cb(const.Event.INFO, const.Source.WUI)
        with pytest.raises(SDKConnectionError):
            printer.loop()

    def test_set_handler(self, printer):
        def send_info(caller: Command) -> Any:
            assert caller.args

        printer.set_handler(const.Command.SEND_INFO, send_info)
        # pylint: disable=comparison-with-callable
        assert printer.command.handlers[const.Command.SEND_INFO] == send_info

    def test_decorator(self, printer):
        @printer.handler(const.Command.GCODE)
        def gcode(caller: Command) -> None:
            assert caller.args

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

    def test_loop_no_server(self, requests_mock, printer):
        printer.server = None

        # put an item to queue
        printer.telemetry(const.State.READY)

        try:
            func_timeout(0.1, printer.loop)
        except FunctionTimedOut:
            pass

        # check that no request has been made while server is not set
        assert not requests_mock.request_history

    def test_gcode(self, requests_mock, printer):
        """Test parsing telemetry and call GCODE handler."""
        requests_mock.post(SERVER + "/p/telemetry",
                           text='G1 X10.0',
                           headers={
                               "Command-Id": "1",
                               "Content-Type": "text/x.gcode",
                               "Force": "1"
                           },
                           status_code=200)
        requests_mock.post(SERVER + "/p/events", status_code=204)

        # pylint: disable=unused-variable, unused-argument
        @printer.handler(const.Command.GCODE)
        def gcode(caller: Command):
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
        assert printer.command.force

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
        printer = Printer(const.PrinterType.I3MK3, SN, FINGERPRINT)
        printer.server = SERVER
        tmp_code = printer.register()
        assert tmp_code == mock_tmp_code

        item = printer.queue.get()
        assert item.code == tmp_code

    def test_register_400_no_mac(self, requests_mock, printer):
        requests_mock.post(SERVER + "/p/register", status_code=400)

        with pytest.raises(RuntimeError):
            printer.register()

    def test_register_400_no_server(self, printer):
        printer.server = None

        with pytest.raises(RuntimeError):
            printer.register()

    def test_get_token(self, requests_mock, printer):
        tmp_code = "f4c8996fb9"
        token = "9TKC0M6mH7WNZTk4NbHG"
        requests_mock.get(SERVER + "/p/register",
                          headers={"Token": token},
                          status_code=200)

        res = printer.get_token(tmp_code)
        assert res.status_code == 200
        assert res.headers["Token"] == token

    def test_get_token_loop(self, requests_mock, printer):
        tmp_code = "f4c8996fb9"
        token = "9TKC0M6mH7WNZTk4NbHG"
        requests_mock.get(SERVER + "/p/register",
                          headers={"Token": token},
                          status_code=200)

        def register_handler(value):
            assert value == token
            register_handler.call = 1

        printer.register_handler = register_handler
        printer.queue.put(Register(tmp_code))
        try:
            func_timeout(0.1, printer.loop)
        except FunctionTimedOut:
            pass

        assert (str(
            requests_mock.request_history[0]) == f"GET {SERVER}/p/register")
        assert printer.token == token
        assert register_handler.call == 1

    def test_get_token_202(self, requests_mock):
        """202 - `tmp_code` is fine but the printer has not yet been added to
        Connect."""
        printer = Printer(const.PrinterType.I3MK3S, SN, FINGERPRINT)
        printer.server = SERVER

        tmp_code = "f4c8996fb9"
        requests_mock.get(SERVER + "/p/register", status_code=202)

        printer.queue.put(Register(tmp_code))
        try:
            func_timeout(1.1, printer.loop)
        except FunctionTimedOut:
            pass

        assert (str(
            requests_mock.request_history[0]) == f"GET {SERVER}/p/register")
        assert printer.token is None
        item = printer.queue.get_nowait()
        assert item.code == tmp_code

    def test_get_token_invalid_code(self, requests_mock, printer):
        tmp_code = "invalid_tmp_code"
        requests_mock.get(SERVER + "/p/register", status_code=400)

        printer.queue.put(Register(tmp_code))
        with pytest.raises(SDKServerError):
            printer.loop()

    def test_load_lan_settings(self, lan_settings_ini):
        printer = Printer(const.PrinterType.I3MK3, SN, FINGERPRINT)
        printer.set_connection(lan_settings_ini)

        assert printer.token == TOKEN
        assert printer.server == f"http://{CONNECT_HOST}:{CONNECT_PORT}"

    def test_from_lan_settings_not_found(self):
        printer = Printer(const.PrinterType.I3MK3, SN, FINGERPRINT)

        with pytest.raises(FileNotFoundError):
            printer.set_connection("some_non-existing_file")

    def test_inotify(self, printer):
        # create two dirs. This will test if recreating the InotifyHandler
        # in mount/unmount has side effects of creating multiple events
        # for the same thing
        dir1 = tempfile.TemporaryDirectory()
        open(f"{dir1.name}/before1.txt", "w").close()
        printer.mount(dir1.name, "data1")

        dir2 = tempfile.TemporaryDirectory()
        printer.mount(dir2.name, "data2")

        open(f"{dir1.name}/after1.txt", "w").close()
        open(f"{dir2.name}/after2.txt", "w").close()

        printer.inotify_handler()  # process inotify events

        # mount of dir1
        event = printer.queue.get_nowait()
        assert event.event == const.Event.MEDIUM_INSERTED

        # mount of dir2
        event = printer.queue.get_nowait()
        assert event.event == const.Event.MEDIUM_INSERTED

        # after1.txt
        event = printer.queue.get_nowait()
        assert event.event == const.Event.FILE_CHANGED
        assert event.data['old_path'] is None
        assert event.data['new_path'] == '/data1/after1.txt'

        # after2.txt
        event = printer.queue.get_nowait()
        assert event.event == const.Event.FILE_CHANGED
        assert event.data['old_path'] is None
        assert event.data['new_path'] == '/data2/after2.txt'

        # make sure there is no more events
        with pytest.raises(queue.Empty):
            printer.queue.get_nowait()

    @staticmethod
    def _send_file_info(dirpath, filename, requests_mock, printer):
        printer.mount(dirpath, "test")
        # MEDIUM_INSERTED event resulting from mounting
        requests_mock.post(SERVER + "/p/events", status_code=204)

        requests_mock.post(SERVER + "/p/telemetry",
                           text='{"command":"SEND_FILE_INFO", '
                           f'"args": ["{filename}"]}}',
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

        assert str(requests_mock.request_history[2]) == \
               f"POST {SERVER}/p/events"
        info = requests_mock.request_history[2].json()
        assert info["event"] == "ACCEPTED"
        assert info["source"] == "CONNECT"
        assert info["command_id"] == 42

        printer.command()  # exec SEND_FILE_INFO

        try:
            func_timeout(0.1, printer.loop)
        except FunctionTimedOut:
            pass

        requests_mock.post(SERVER + "/p/events", status_code=204)
        assert (str(
            requests_mock.request_history[3]) == f"POST {SERVER}/p/events")
        info = requests_mock.request_history[3].json()
        assert info['command_id'] == 42
        return info

    def test_send_file_info(self, requests_mock, printer):
        # create directory to be mounted with some content
        dir = tempfile.TemporaryDirectory()
        with open(f"{dir.name}/hello.gcode", "w") as f:
            f.write("Hello World!")

        filename = '/test/hello.gcode'
        info = self._send_file_info(dir.name, filename, requests_mock, printer)
        assert info["event"] == "FILE_INFO"
        assert info["source"] == "CONNECT"
        assert info["data"]['path'] == filename
        assert info["data"]['size'] == 12
        assert "m_time" in info['data']

    def test_send_file_info_does_not_exist(self, requests_mock, printer):
        dir = tempfile.TemporaryDirectory()
        filename = '/N/A/file.txt'
        info = self._send_file_info(dir.name, filename, requests_mock, printer)
        assert info['event'] == 'REJECTED'
        assert info['source'] == 'WUI'
        assert info['data'] == {
            'reason': 'Command error',
            'error': 'File does not exist: /N/A/file.txt'
        }


def test_notification_handler():
    code = "SERVICE_UNAVAILABLE"
    msg = "Service is unavailable at this moment."

    def cb(code, msg):
        return (code, msg)

    Notifications.handler = cb

    # pylint: disable=assignment-from-no-return
    res = Notifications.handler(code, msg)
    assert res == (code, msg)

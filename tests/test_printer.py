"""Test for Printer object."""
import io
import os
import queue
import tempfile
import time
from typing import Any

import pytest  # type: ignore
from func_timeout import FunctionTimedOut, func_timeout  # type: ignore

from prusa.connect.printer import Command, Printer, Register, const, errors
from prusa.connect.printer.conditions import API, HTTP, INTERNET, CondState
from prusa.connect.printer.models import Event, Telemetry
from tests.util import (
    CONNECT_HOST,
    CONNECT_PORT,
    FINGERPRINT,
    SERVER,
    SN,
    TOKEN,
    FakeWS,
    run_loop,
    ws_force_gcode_command,
    ws_gcode_command,
    ws_json_command,
)

# pylint: disable=missing-function-docstring
# pylint: disable=too-many-lines

MAC = "00:01:02:03:04:05"
FIRMWARE = "3.9.0rc2"
IP = "192.168.1.101"
TYPE = const.TransferType.FROM_WEB


@pytest.fixture(scope="session")
def lan_settings_ini():
    """Temporary lan_settings.ini file fixture."""
    tmpf = tempfile.NamedTemporaryFile(mode="w", delete=False)
    tmpf.write(f"""

[printer]
name = SDK UnitTest
location = space
type = MK3S

[network]
hostname = MK3S

[service::local]
enable = 1
username =
password =
api_key =

[service::connect]
hostname = {CONNECT_HOST}
tls = False
port = {CONNECT_PORT}
token = {TOKEN}
""")
    tmpf.close()
    return tmpf.name


def remove_m_time(file_data):
    """Remove 'm_timestamp' and 'children'
    keys from file structure."""
    for key in list(file_data):
        if key == "m_timestamp":
            del file_data[key]
            continue
        if key == 'children':
            for i in file_data['children']:
                remove_m_time(i)


@pytest.fixture()
def printer_no_fp():
    """Printer without fingerprint."""
    printer = Printer(const.PrinterType.I3MK3S, SN)
    printer.server = SERVER
    printer.token = TOKEN
    printer.ws = FakeWS(printer.handle_ws_message)
    return printer


@pytest.fixture
def printer_sdcard():
    """Printer with sdcard attached"""
    printer = Printer(const.PrinterType.I3MK3S, SN, FINGERPRINT)
    printer.server = SERVER
    printer.token = TOKEN
    printer.ws = FakeWS(printer.handle_ws_message)
    tmp_dir = tempfile.TemporaryDirectory()
    printer.attach(tmp_dir.name, "sdcard")
    printer.queue.get_nowait()  # consume MEDIUM_INSERTED event
    yield printer


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
        printer.telemetry()
        item = printer.queue.get_nowait()

        assert isinstance(item, Telemetry)
        assert item.to_payload() == {'state': 'BUSY'}

    def test_telemetry_headers(self, printer):
        headers = printer.make_headers()
        assert headers["Fingerprint"] == printer.fingerprint
        assert headers["User-Agent"].startswith("Prusa-Connect-SDK-Printer/")
        assert headers["User-Agent-Printer"] == str(printer.type)
        assert headers["User-Agent-Version"] == printer.software
        assert headers["Token"] == printer.token

        # WS connect must use the same headers
        printer.ensure_ws_connected()
        assert printer.ws.connect_calls
        _, ws_headers = printer.ws.connect_calls[0]
        assert ws_headers["Fingerprint"] == printer.fingerprint
        assert ws_headers["Token"] == printer.token

    def test_telemetry_no_fingerprint(self, printer_no_fp):
        printer_no_fp.telemetry(temp_bed=1, temp_nozzle=2)
        item = printer_no_fp.queue.get_nowait()
        assert isinstance(item, Telemetry)
        assert item.to_payload() == {'state': 'BUSY'}

    def test_handle_ws_message_no_fingerprint(self, printer_no_fp):
        printer_no_fp.ws.simulate_message(
            ws_json_command(42, {"command": "SEND_INFO"}))
        item = printer_no_fp.queue.get_nowait()
        assert isinstance(item, Event)
        event_obj = item.to_payload()
        assert event_obj['event'] == 'REJECTED'
        assert event_obj['source'] == 'WUI'
        assert event_obj['reason'] == \
               'Printer has not been initialized properly'

    def test_event(self, printer):
        printer.event_cb(const.Event.INFO, const.Source.WUI)
        item = printer.queue.get_nowait()
        assert isinstance(item, Event)
        assert item.event == const.Event.INFO
        assert item.source == const.Source.WUI

    def test_set_state(self, printer):
        printer.set_state(const.State.ATTENTION, const.Source.WUI)
        item = printer.queue.get_nowait()
        assert isinstance(item, Event)
        event_obj = item.to_payload()
        assert event_obj['event'] == 'STATE_CHANGED'
        assert event_obj['state'] == 'ATTENTION'
        assert event_obj['source'] == 'WUI'

        printer.set_state(const.State.IDLE, const.Source.HW, ready=True)
        item = printer.queue.get_nowait()
        assert isinstance(item, Event)
        event_obj = item.to_payload()
        assert event_obj['state'] == 'IDLE'

        printer.set_state(const.State.PRINTING, const.Source.SERIAL)
        item = printer.queue.get_nowait()
        assert isinstance(item, Event)
        event_obj = item.to_payload()
        assert event_obj['state'] == 'PRINTING'

        printer.set_state(const.State.FINISHED, const.Source.FIRMWARE)
        item = printer.queue.get_nowait()
        assert isinstance(item, Event)
        event_obj = item.to_payload()
        assert event_obj['state'] == 'FINISHED'

    def test_loop(self, printer):
        printer.event_cb(const.Event.INFO, const.Source.WUI)

        try:
            func_timeout(0.1, printer.loop)
        except FunctionTimedOut:
            pass

        assert printer.ws.sent
        sent = printer.ws.sent[0]
        assert sent["event"] == "INFO"
        assert sent["source"] == "WUI"

    def test_loop_exception(self, printer):
        printer.event_cb(const.Event.INFO, const.Source.WUI)

        try:
            func_timeout(0.1, printer.loop)
        except FunctionTimedOut:
            pass

        # Simulate WS send failure (connected but send fails) → HTTP error
        printer.ws.fail_send = True
        printer.event_cb(const.Event.INFO, const.Source.WUI)

        run_loop(printer.loop)

        assert errors.HTTP.ok is False
        assert HTTP.state is CondState.NOK

    def test_ws_not_connected(self, printer):
        """WS connection not established at all → INTERNET error."""
        printer.ws.fail_connect = True  # simulate unreachable server
        printer.ws.disconnect()
        printer.event_cb(const.Event.INFO, const.Source.WUI)

        run_loop(printer.loop)

        assert errors.INTERNET.ok is False
        assert INTERNET.state is CondState.NOK

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
        """Test parsing WS command and call builtin handler."""
        requests_mock.post(SERVER + "/p/events", status_code=204)

        printer.telemetry()

        run_loop(printer.loop)

        # Simulate server pushing SEND_INFO command over WS
        printer.ws.simulate_message(
            ws_json_command(42, {"command": "SEND_INFO"}))

        run_loop(printer.loop)  # flush ACCEPTED event from queue to WS

        assert printer.command.state == const.Event.ACCEPTED

        # ACCEPTED event goes over WS
        accepted = printer.ws.sent[-1]
        assert accepted["event"] == "ACCEPTED"
        assert accepted["source"] == "CONNECT"
        assert accepted["command_id"] == 42

        printer.command()

        run_loop(printer.loop)

        info = printer.ws.sent[-1]
        assert info["event"] == "INFO"
        assert info["source"] == "CONNECT"
        assert info["command_id"] == 42

    def test_call_delete_directory(self, requests_mock, printer):
        tmp_dir = tempfile.TemporaryDirectory()

        printer.attach(tmp_dir.name, "test")

        # get storage for test purpose
        storage = printer.inotify_handler.fs.storage_dict["test"]

        # create temp dir in storage
        path = os.path.join(tmp_dir.name, "test_dir")
        os.makedirs(path)

        # check file structure
        file_system = storage.tree.to_dict()
        remove_m_time(file_system)
        assert file_system == {
            'type': 'FOLDER',
            'name': 'test',
            'read_only': False,
            'size': 0,
            'children': [],
        }

        # MEDIUM_INSERTED event resulting from attaching goes over WS
        run_loop(printer.loop)

        cmd = {
            "command": "DELETE_DIRECTORY",
            "kwargs": {
                "path": "/test/test_dir",
            },
        }

        printer.telemetry()
        printer.inotify_handler()

        run_loop(printer.loop)

        # Simulate server pushing DELETE_DIRECTORY command
        printer.ws.simulate_message(ws_json_command(42, cmd))

        assert printer.command.state == const.Event.ACCEPTED

        accepted = printer.ws.sent[-1]
        assert accepted["event"] == "FILE_CHANGED"
        assert accepted["source"] == "WUI"

        # check file structure without children
        file_system = storage.tree.to_dict(include_children=False)
        remove_m_time(file_system)
        assert file_system == {
            'type': 'FOLDER',
            'name': 'test',
            'size': 0,
            'read_only': False,
        }

        # check file structure
        file_system = storage.tree.to_dict()
        remove_m_time(file_system)
        assert file_system == {
            'type':
            'FOLDER',
            'name':
            'test',
            'size':
            0,
            'read_only':
            False,
            'children': [{
                'type': 'FOLDER',
                'name': 'test_dir',
                'read_only': False,
                'size': 0,
            }],
        }

        printer.command()  # exec DELETE_DIRECTORY
        printer.inotify_handler()

        run_loop(printer.loop)

        # check file structure
        file_system = storage.tree.to_dict()
        remove_m_time(file_system)
        assert file_system == {
            'type': 'FOLDER',
            'name': 'test',
            'read_only': False,
            'size': 0,
            'children': [],
        }
        # directory is removed
        assert os.path.exists(path) is False

    def test_call_delete_file(self, requests_mock, printer):
        tmp_dir = tempfile.TemporaryDirectory()
        tmp_file = "test-file.hex"

        printer.attach(tmp_dir.name, "test")

        # get storage for test purpose
        storage = printer.inotify_handler.fs.storage_dict["test"]

        # create temp file in storage
        file_path = os.path.join(tmp_dir.name, tmp_file)
        with open(file_path, 'wb') as file_tmp:
            file_tmp.write(os.urandom(1))

        # check file structure
        file_system = storage.tree.to_dict()
        remove_m_time(file_system)
        assert file_system == {
            'type': 'FOLDER',
            'name': 'test',
            'read_only': False,
            'size': 0,
            'children': [],
        }

        # MEDIUM_INSERTED event goes over WS
        run_loop(printer.loop)

        cmd = {
            "command": "DELETE_FILE",
            "kwargs": {
                "path": "/test/test-file.hex",
            },
        }

        printer.telemetry()
        printer.inotify_handler()

        run_loop(printer.loop)

        accepted = printer.ws.sent[-1]
        assert accepted["event"] == "FILE_CHANGED"
        assert accepted["source"] == "WUI"

        # Simulate server pushing DELETE_FILE command
        printer.ws.simulate_message(ws_json_command(42, cmd))

        # check file structure
        file_system = storage.tree.to_dict()
        remove_m_time(file_system)
        assert file_system == {
            'type':
            'FOLDER',
            'name':
            'test',
            'read_only':
            False,
            'size':
            1,
            'children': [{
                'type': 'FIRMWARE',
                'name': 'test-file.hex',
                'read_only': False,
                'size': 1,
            }],
        }

        printer.command()  # exec DELETE_FILE
        printer.inotify_handler()

        run_loop(printer.loop)

        # check file structure
        file_system = storage.tree.to_dict()
        remove_m_time(file_system)
        assert file_system == {
            'type': 'FOLDER',
            'name': 'test',
            'read_only': False,
            'size': 0,
            'children': [],
        }
        assert os.path.exists(file_path) is False

    def test_call_create_folder(self, requests_mock, printer):
        tmp_dir = tempfile.TemporaryDirectory()
        printer.attach(tmp_dir.name, "test")

        # get storage for test purpose
        storage = printer.inotify_handler.fs.storage_dict["test"]

        # MEDIUM_INSERTED event goes over WS
        run_loop(printer.loop)

        dir_name = "test_dir"
        path = os.path.join(tmp_dir.name, dir_name)

        # check file structure
        file_system = storage.tree.to_dict()
        remove_m_time(file_system)
        assert file_system == {
            'type': 'FOLDER',
            'name': 'test',
            'size': 0,
            'read_only': False,
            'children': [],
        }

        cmd = {
            "command": "CREATE_FOLDER",
            "kwargs": {
                "path": "/test/test_dir",
            },
        }

        printer.telemetry()
        printer.inotify_handler()

        run_loop(printer.loop)

        # Simulate server pushing CREATE_FOLDER command
        printer.ws.simulate_message(ws_json_command(42, cmd))

        run_loop(printer.loop)  # flush ACCEPTED from queue to WS

        assert printer.command.state == const.Event.ACCEPTED

        accepted = printer.ws.sent[-1]
        assert accepted["event"] == "ACCEPTED"
        assert accepted["source"] == "CONNECT"

        # check file structure
        file_system = storage.tree.to_dict()
        remove_m_time(file_system)
        assert file_system == {
            'type': 'FOLDER',
            'name': 'test',
            'size': 0,
            'read_only': False,
            'children': [],
        }

        printer.command()  # exec CREATE_FOLDER
        printer.inotify_handler()

        run_loop(printer.loop)

        # check file structure without children
        file_system = storage.tree.to_dict(include_children=False)
        remove_m_time(file_system)
        assert file_system == {
            'type': 'FOLDER',
            'name': 'test',
            'size': 0,
            'read_only': False,
        }
        assert os.path.exists(path) is True

        # check file structure
        file_system = storage.tree.to_dict()
        remove_m_time(file_system)
        assert file_system == {
            'type':
            'FOLDER',
            'name':
            'test',
            'size':
            0,
            'read_only':
            False,
            'children': [{
                'type': 'FOLDER',
                'name': 'test_dir',
                'read_only': False,
                'size': 0,
            }],
        }
        assert os.path.exists(path) is True

    def test_loop_no_server(self, printer):
        printer.server = None

        # put an item to queue
        printer.telemetry()

        run_loop(printer.loop)

        # no WS messages sent while server is not set
        assert not printer.ws.sent

    def test_gcode(self, printer):
        """Test parsing WS GCODE command."""

        # pylint: disable=unused-variable, unused-argument
        @printer.handler(const.Command.GCODE)
        def gcode(caller: Command):
            return dict(source=const.Source.MARLIN)

        printer.telemetry()
        run_loop(printer.loop)

        # Simulate server pushing GCODE command (low-level, G prefix)
        printer.ws.simulate_message(ws_gcode_command(1, "G1 X10.0"))

        run_loop(printer.loop)  # flush ACCEPTED from queue to WS

        accepted = printer.ws.sent[-1]
        assert accepted["event"] == "ACCEPTED", accepted

        printer.command()

        run_loop(printer.loop)

        finished = printer.ws.sent[-1]
        assert finished["event"] == "FINISHED", finished

    def test_gcode_force(self, printer):
        """Test WS forced GCode command (F prefix → command.force=True)."""

        # pylint: disable=unused-variable, unused-argument
        @printer.handler(const.Command.GCODE)
        def gcode(caller: Command):
            return dict(source=const.Source.MARLIN)

        printer.telemetry()
        run_loop(printer.loop)

        # Simulate server pushing forced GCODE command (F prefix)
        printer.ws.simulate_message(ws_force_gcode_command(1, "G1 X10.0"))

        run_loop(printer.loop)  # flush ACCEPTED from queue to WS

        accepted = printer.ws.sent[-1]
        assert accepted["event"] == "ACCEPTED", accepted
        assert printer.command.force

        printer.command()

        run_loop(printer.loop)

        finished = printer.ws.sent[-1]
        assert finished["event"] == "FINISHED", finished

    def test_register(self, requests_mock):
        mock_tmp_code = "f4c8996fb9"
        requests_mock.post(SERVER + "/p/register",
                           headers={"Code": mock_tmp_code},
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

        assert errors.HTTP.ok is True
        assert HTTP.state is CondState.OK
        assert errors.API.ok is False
        assert API.state is CondState.NOK

    def test_register_400_no_server(self, printer):
        printer.server = None

        with pytest.raises(RuntimeError):
            printer.register()

        assert errors.HTTP.ok is True
        assert HTTP.state is CondState.OK
        assert errors.API.ok is False
        assert API.state is CondState.NOK

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
        run_loop(printer.loop)

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
        run_loop(printer.loop, timeout=1.1)

        assert (str(
            requests_mock.request_history[0]) == f"GET {SERVER}/p/register")
        assert printer.token is None
        item = printer.queue.get_nowait()
        assert item.code == tmp_code

    def test_load_lan_settings(self, lan_settings_ini):
        printer = Printer(const.PrinterType.I3MK3, SN, FINGERPRINT)
        printer.connection_from_config(lan_settings_ini)

        assert printer.token == TOKEN
        assert printer.server == f"http://{CONNECT_HOST}:{CONNECT_PORT}"

    def test_from_lan_settings_not_found(self):
        printer = Printer(const.PrinterType.I3MK3, SN, FINGERPRINT)

        with pytest.raises(FileNotFoundError):
            printer.connection_from_config("some_non-existing_file")

    def test_inotify(self, printer):
        # create two dirs. This will test if recreating the InotifyHandler
        # in attach/detach has side effects of creating multiple events
        # for the same thing
        dir1 = tempfile.TemporaryDirectory()
        open(f"{dir1.name}/before1.txt", "w").close()
        printer.attach(dir1.name, "data1")

        dir2 = tempfile.TemporaryDirectory()
        printer.attach(dir2.name, "data2")

        open(f"{dir1.name}/after1.txt", "w").close()
        open(f"{dir2.name}/after2.txt", "w").close()

        printer.inotify_handler()  # process inotify events

        # attach of dir1
        event = printer.queue.get_nowait()
        assert event.event == const.Event.MEDIUM_INSERTED

        # attach of dir2
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

    def test_send_file_info(self, printer):
        # create directory to be attached with some content
        dir = tempfile.TemporaryDirectory()
        with open(f"{dir.name}/hello.gcode", "w") as f:
            # noqa: E501
            f.write("""
; thumbnail begin 16x16 524
; iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAYAAAAf8/9hAAABUElEQVR4AZ2Sy0rDUBRF8w2Z+6ji
; B0iUtuQdkpAnCSSEDIKjqvigIBRHDvwg5w4cqSD4VUf3CTdEGjXthU3a3LvX2eeeSNLAUhSFIGnb
; BbOqqqRp2uYgGHRdZ7MQ/o8yCoNhGGSaJj/7sF/TYOP1akpLbW9Nb9czWpkTfgK2BhEXZlkWua5L
; QRBQHMeUpikrSRJ6cA95D2cAQpoOgJjYEKAsy6goCqqqilWWJeV5ziCY0ZqAcBrEchyHK6MizHVd
; 06N/RHfGPj0vTujl4pRNQjCurEmbBAAk8H2fq6MiAE3TMEBImG3b7tK+38xbAGKhxzAMO0i/BbyL
; oojOpztsBOjp7LhtAcR7+6CDiEuECS3h9+Lb2L/EwUl4nsdzBxBjgz6XKhs+bud0OdsdHqNYsixz
; FSTBQSFAodGfNQ4B1P+Uf8x9zAIEd/Fn5LGg/858AcjHJAfMY3ljAAAAAElFTkSuQmCC
; thumbnail end\n""")
            f.write("\n")
            f.write("; temperature = 250\n")
            f.write("; thin_walls = 0\n")

        filename = '/test/hello.gcode'

        # Attach first so MEDIUM_INSERTED is flushed before the command arrives
        printer.attach(dir.name, "test")
        run_loop(printer.loop)  # flush MEDIUM_INSERTED

        # Simulate server pushing SEND_FILE_INFO command
        printer.ws.simulate_message(
            ws_json_command(42, {
                "command": "SEND_FILE_INFO",
                "kwargs": {
                    "path": filename,
                },
            }))

        run_loop(printer.loop)  # flush ACCEPTED

        assert printer.command.state == const.Event.ACCEPTED

        accepted = printer.ws.sent[-1]
        assert accepted["event"] == "ACCEPTED"
        assert accepted["source"] == "CONNECT"
        assert accepted["command_id"] == 42

        printer.command()  # exec SEND_FILE_INFO
        run_loop(printer.loop)

        info = printer.ws.sent[-1]
        assert info["event"] == "FILE_INFO"
        assert info["source"] == "CONNECT"
        assert info["data"]['path'] == filename
        assert info["data"]['size'] == 628
        assert "m_timestamp" in info['data']

        # metadata and thumbnail
        assert info['data']['temperature'] == 250
        assert len(info['data']['preview']) == 524

    def test_send_file_info_does_not_exist(self, printer):
        filename = '/N/A/file.txt'
        directory = tempfile.TemporaryDirectory()

        printer.attach(directory.name, "test")
        run_loop(printer.loop)  # flush MEDIUM_INSERTED

        printer.ws.simulate_message(
            ws_json_command(42, {
                "command": "SEND_FILE_INFO",
                "kwargs": {
                    "path": filename,
                },
            }))

        run_loop(printer.loop)  # flush ACCEPTED

        assert printer.command.state == const.Event.ACCEPTED

        printer.command()
        run_loop(printer.loop)

        info = printer.ws.sent[-1]
        assert info['event'] == 'FAILED'
        assert info['source'] == 'WUI'
        assert info['reason'] == 'Command error'
        assert info['data'] == {
            'error': "ValueError('File does not exist: "
            "/N/A/file.txt')",
        }

    def test_url_download(self, requests_mock, printer_sdcard):
        url = "http://prusaprinters.org/my.gcode"
        path = "/sdcard/my.gcode"
        kwargs = {
            "path": path,
            "url": url,
            "selecting": True,
            "printing": False,
        }
        requests_mock.get(url,
                          body=io.BytesIO(os.urandom(16)),
                          status_code=200)

        printer = printer_sdcard

        # Simulate server pushing START_URL_DOWNLOAD command
        printer.ws.simulate_message(
            ws_json_command(42, {
                "command": "START_URL_DOWNLOAD",
                "kwargs": kwargs,
            }))

        run_loop(printer.loop)

        # exec download
        printer.command()
        run_loop(printer.download_mgr.loop)

        # check the file is on the disk
        dir_ = printer.fs.storage_dict['sdcard'].path_storage
        downloaded_file = f'/{dir_}/my.gcode'
        assert os.path.exists(downloaded_file)
        os.remove(downloaded_file)

        run_loop(printer.loop)

        info = next(m for m in printer.ws.sent
                    if m.get("event") == "TRANSFER_INFO")
        assert info["source"] == "CONNECT"
        assert info["data"]["start_cmd_id"] == 42

    def test_connect_download(self, requests_mock, printer_sdcard):
        path = "/sdcard/my.gcode"
        kwargs = {"path": path, "team_id": 321, "hash": '0123456789abcdef'}
        uri = "/p/teams/{team_id}/files/{hash}/raw".format(**kwargs)
        requests_mock.get(SERVER + uri,
                          body=io.BytesIO(os.urandom(16)),
                          status_code=200)

        printer = printer_sdcard

        # Simulate server pushing START_CONNECT_DOWNLOAD command
        printer.ws.simulate_message(
            ws_json_command(42, {
                "command": "START_CONNECT_DOWNLOAD",
                "kwargs": kwargs,
            }))

        run_loop(printer.loop)

        # exec download
        printer.command()
        run_loop(printer.download_mgr.loop)

        # check the file is on the disk
        dir_ = printer.fs.storage_dict['sdcard'].path_storage
        downloaded_file = f'/{dir_}/my.gcode'
        assert os.path.exists(downloaded_file)
        os.remove(downloaded_file)

        run_loop(printer.loop)

        info = next(m for m in printer.ws.sent
                    if m.get("event") == "TRANSFER_INFO")
        assert info["source"] == "CONNECT"
        assert info["data"]["start_cmd_id"] == 42

    def test_transfer_info(self, printer_sdcard):
        printer = printer_sdcard
        path = '/sdcard/test-download-info.gcode'
        url = "http://prusaprinters.org/my.gcode"

        # Simulate server pushing SEND_TRANSFER_INFO command
        printer.ws.simulate_message(
            ws_json_command(42, {"command": "SEND_TRANSFER_INFO"}))

        run_loop(printer.loop)

        # mock printer.download_mgr.current
        now = time.time() - 1
        printer.download_mgr.start(TYPE,
                                   path,
                                   url,
                                   to_print=False,
                                   to_select=True)
        transfer = printer.download_mgr.transfer
        transfer.start_ts = now
        transfer.size = 1000
        transfer.transferred = 100
        assert not printer.download_mgr.transfer.stop_ts

        # exec download info
        printer.command()
        run_loop(printer.loop)

        info = printer.ws.sent[-1]
        assert info["event"] == "TRANSFER_INFO"
        assert info["source"] == "CONNECT"
        assert info["command_id"] == 42
        assert info["data"]['size'] == 1000
        assert info["data"]['transferred'] == 100
        assert info["data"]['time_remaining'] > 0
        assert info["data"]['to_print'] is False

    def test_transfer_info_id(self, printer_sdcard):
        printer = printer_sdcard
        path = '/sdcard/test-download-info.gcode'
        url = "http://prusaprinters.org/my.gcode"

        run_loop(printer.loop)

        # mock printer.download_mgr.current
        now = time.time() - 1
        event = printer.download_mgr.start(TYPE,
                                           path,
                                           url,
                                           to_print=False,
                                           to_select=True)
        printer.event_cb(**event)  # send initial TRANSFER_INFO

        transfer = printer.download_mgr.transfer
        transfer.start_ts = now
        transfer.size = 1000
        transfer.transferred = 100
        assert not printer.download_mgr.transfer.stop_ts

        run_loop(printer.loop)

        info = printer.ws.sent[0]
        transfer_id = info['transfer_id']

        # Simulate server pushing SEND_TRANSFER_INFO with transfer_id
        printer.ws.simulate_message(
            ws_json_command(42, {
                "command": "SEND_TRANSFER_INFO",
                "transfer_id": transfer_id,
            }))

        run_loop(printer.loop)

        # exec download info
        printer.command()
        run_loop(printer.loop)

        info = printer.ws.sent[-1]
        assert info["event"] == "TRANSFER_INFO"
        assert info["source"] == "CONNECT"
        assert info["command_id"] == 42
        assert info['transfer_id'] == transfer_id

    def test_transfer_info_failed(self, printer_sdcard):
        printer = printer_sdcard
        path = '/sdcard/test-download-info.gcode'
        url = "http://prusaprinters.org/my.gcode"

        run_loop(printer.loop)

        # mock printer.download_mgr.current
        now = time.time() - 1
        event = printer.download_mgr.start(TYPE,
                                           path,
                                           url,
                                           to_print=False,
                                           to_select=True)
        printer.event_cb(**event)  # send initial TRANSFER_INFO

        transfer = printer.download_mgr.transfer
        transfer.start_ts = now
        transfer.size = 1000
        transfer.transferred = 100
        assert not printer.download_mgr.transfer.stop_ts

        run_loop(printer.loop)

        # Simulate server pushing SEND_TRANSFER_INFO with wrong transfer_id
        printer.ws.simulate_message(
            ws_json_command(42, {
                "command": "SEND_TRANSFER_INFO",
                "transfer_id": -1,
            }))

        run_loop(printer.loop)

        # exec download info
        printer.command()
        run_loop(printer.loop)

        info = printer.ws.sent[-1]
        assert info["event"] == "TRANSFER_INFO"
        assert info["source"] == "CONNECT"
        assert info["command_id"] == 42

    def test_download_stop(self, printer_sdcard):
        printer = printer_sdcard
        path = '/sdcard/test-download-stop.gcode'
        url = "http://prusaprinters.org/my.gcode"

        # pretend we're downloading
        printer.download_mgr.start(TYPE,
                                   path,
                                   url,
                                   to_print=False,
                                   to_select=False)
        assert not printer.download_mgr.transfer.stop_ts

        # Simulate server pushing STOP_TRANSFER command
        printer.ws.simulate_message(
            ws_json_command(42, {"command": "STOP_TRANSFER"}))

        run_loop(printer.loop)

        # exec the command
        printer.command()
        assert printer.download_mgr.transfer.stop_ts

    def test_download_stop_with_id(self, printer_sdcard):
        printer = printer_sdcard
        path = '/sdcard/test-download-stop.gcode'
        url = "http://prusaprinters.org/my.gcode"

        # pretend we're downloading
        printer.download_mgr.start(TYPE,
                                   path,
                                   url,
                                   to_print=False,
                                   to_select=False)
        assert not printer.download_mgr.transfer.stop_ts

        printer.ws.simulate_message(
            ws_json_command(
                42, {
                    "command": "STOP_TRANSFER",
                    "kwargs": {
                        "transfer_id": printer.transfer.transfer_id,
                    },
                }))

        run_loop(printer.loop)

        # exec the command
        printer.command()
        assert printer.download_mgr.transfer.stop_ts

    def test_download_stop_wrong_id(self, printer_sdcard):
        printer = printer_sdcard
        path = '/sdcard/test-download-stop.gcode'
        url = "http://prusaprinters.org/my.gcode"

        # pretend we're downloading
        printer.download_mgr.start(TYPE,
                                   path,
                                   url,
                                   to_print=False,
                                   to_select=False)
        assert not printer.download_mgr.transfer.stop_ts

        printer.ws.simulate_message(
            ws_json_command(42, {
                "command": "STOP_TRANSFER",
                "kwargs": {
                    "transfer_id": 666,
                },
            }))

        run_loop(printer.loop)

        # exec the command
        printer.command()
        assert not printer.download_mgr.transfer.stop_ts

        item = printer.queue.get_nowait()
        assert isinstance(item, Event)
        event_obj = item.to_payload()
        print(event_obj)
        assert event_obj['event'] == 'FAILED'
        assert event_obj['source'] == 'WUI'
        assert event_obj['data']['error'] == \
               "RuntimeError('Wrong transfer_id')"

    def test_download_rejected(self, printer):
        tmp_dir = tempfile.TemporaryDirectory()
        printer.attach(tmp_dir.name, "sdcard")

        printer.queue.get_nowait()  # consume

        url = "http://prusaprinters.org/test-download-rejected.gcode"
        item = printer.download_mgr.start(
            TYPE,
            '/sdcard/test-download-rejected.gcode',
            url,
            to_print=False,
            to_select=False)
        assert item['event'] == const.Event.TRANSFER_INFO

        # 2nd will get rejected
        item = printer.download_mgr.start(
            TYPE,
            url,
            '/sdcard/test-download-rejected.gcode',
            to_print=False,
            to_select=False)

        assert item['event'] == const.Event.REJECTED
        assert item['source'] == const.Source.CONNECT

        with pytest.raises(queue.Empty):
            printer.queue.get_nowait()

    def test_download_aborted(self, printer_sdcard):
        printer = printer_sdcard
        url = "http://example.invalid/test-download-aborted.gcode"
        printer.download_mgr.start(TYPE,
                                   '/sdcard/test-download-aborted.gcode',
                                   url,
                                   to_print=False,
                                   to_select=False)

        run_loop(printer.download_mgr.loop, timeout=.5)

        item = printer.queue.get_nowait()
        assert isinstance(item, Event)
        assert item.event == const.Event.TRANSFER_ABORTED
        assert item.source == const.Source.CONNECT
        assert item.transfer_id == printer.transfer.transfer_id
        with pytest.raises(queue.Empty):
            printer.queue.get_nowait()

    def test_download_aborted_404(self, requests_mock, printer_sdcard):
        url = "http://example.net/test-download-aborted.gcode"
        requests_mock.get(url, status_code=404)
        printer = printer_sdcard
        printer.download_mgr.start(TYPE,
                                   '/sdcard/test-download-aborted.gcode',
                                   url,
                                   to_print=False,
                                   to_select=False)

        run_loop(printer.download_mgr.loop)

        item = printer.queue.get_nowait()
        assert isinstance(item, Event)
        assert item.event == const.Event.TRANSFER_ABORTED
        assert item.source == const.Source.CONNECT
        assert item.transfer_id == printer.transfer.transfer_id
        with pytest.raises(queue.Empty):
            printer.queue.get_nowait()

    def test_set_printer_ready(self, printer):
        printer.ws.simulate_message(
            ws_json_command(42, {"command": "SET_PRINTER_READY"}))

        run_loop(printer.loop)

        printer.command()
        run_loop(printer.loop, timeout=0.2)

        assert printer.state == const.State.READY
        state_evt = next(m for m in printer.ws.sent
                         if m.get("event") == "STATE_CHANGED")
        assert state_evt["state"] == "READY"

        finished = next(m for m in printer.ws.sent
                        if m.get("event") == "FINISHED")
        assert finished

    def test_cancel_printer_ready(self, printer):
        printer.ws.simulate_message(
            ws_json_command(42, {"command": "SET_PRINTER_READY"}))

        run_loop(printer.loop)

        printer.command()
        run_loop(printer.loop, timeout=0.2)

        assert printer.state == const.State.READY
        state_evt = next(m for m in printer.ws.sent
                         if m.get("event") == "STATE_CHANGED")
        assert state_evt["state"] == "READY"

        printer.ws.simulate_message(
            ws_json_command(43, {"command": "CANCEL_PRINTER_READY"}))

        run_loop(printer.loop)

        assert printer.state == const.State.READY

        printer.command()
        run_loop(printer.loop, timeout=0.2)

        assert printer.state == const.State.IDLE

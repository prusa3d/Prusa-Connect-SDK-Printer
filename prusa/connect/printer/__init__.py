"""Python printer library for PrusaConnect.

    Copyright (C) 2023 PrusaResearch
"""
import configparser
import os
import re
from logging import getLogger
from queue import Empty, Queue
from time import sleep, time
from typing import Any, Callable, Dict, List, Optional

from gcode_metadata import get_metadata
from requests import RequestException, Response, Session  # type: ignore

# pylint: disable=redefined-builtin
from requests.exceptions import ConnectionError  # type: ignore
from urllib3.exceptions import ReadTimeoutError  # type: ignore

from . import const, errors
from .camera_controller import CameraController
from .clock import ClockWatcher
from .command import Command, CommandFailed
from .conditions import API, HTTP, INTERNET, TOKEN, CondState
from .const import MMU_SLOT_COUNTS
from .download import DownloadMgr, Transfer
from .files import Filesystem, InotifyHandler, delete
from .models import (
    CameraRegister,
    Event,
    LoopObject,
    Register,
    Sheet,
    Telemetry,
)
from .util import RetryingSession, get_timestamp

__version__ = "0.8.0alpha1"
__date__ = "6 Dec 2023"  # version date
__copyright__ = "(c) 2023 Prusa 3D"
__author_name__ = "Prusa Link Developers"
__author_email__ = "link@prusa3d.cz"
__author__ = f"{__author_name__} <{__author_email__}>"
__description__ = "Python printer library for PrusaConnect"

__credits__ = "Ondřej Tůma, Martin Užák, Michal Zoubek, Tomáš Jozífek"
__url__ = "https://github.com/prusa3d/Prusa-Connect-SDK-Printer"

# pylint: disable=invalid-name
# pylint: disable=too-few-public-methods
# pylint: disable=too-many-arguments
# pylint: disable=too-many-instance-attributes
# NOTE: Temporary for pylint with python3.9
# pylint: disable=unsubscriptable-object

log = getLogger("connect-printer")
re_conn_reason = re.compile(r"] (.*)")

__all__ = ["Printer"]

CommandArgs = Optional[List[Any]]


def default_register_handler(token):
    """Default register handler.

    It blocks communication with Connect in loop method!
    """
    assert token


class Printer:
    """Printer representation object.

    To process inotify_handler, please create your own thread,
    calling printer.inotify_handler() in a loop.
    """
    # pylint: disable=too-many-public-methods

    queue: "Queue[LoopObject]"
    server: Optional[str] = None
    token: Optional[str] = None
    conn: Session

    NOT_INITIALISED_MSG = "Printer has not been initialized properly"

    def __init__(self,
                 type_: Optional[const.PrinterType] = None,
                 sn: Optional[str] = None,
                 fingerprint: Optional[str] = None,
                 max_retries: int = 1):
        self.__type = type_
        self.__sn = sn
        self.__fingerprint = fingerprint
        self.firmware = None
        self.network_info = {
            "lan_mac": None,
            "lan_ipv4": None,
            "lan_ipv6": None,
            "wifi_mac": None,
            "wifi_ipv4": None,
            "wifi_ipv6": None,
            "wifi_ssid": None,
            "hostname": None,
            "username": None,
            "digest": None,
        }
        self.api_key: Optional[str] = None
        self.code: Optional[str] = None

        self.__ready: bool = False
        self.__state: const.State = const.State.BUSY
        self.job_id: Optional[int] = None
        self.mbl: Optional[List[float]] = None
        self.sheet_settings: Optional[List[Sheet]] = None
        self.active_sheet: Optional[int] = None  # index
        self.mmu_enabled: bool = False
        self.mmu_fw: Optional[str] = None
        self.mmu_type: Optional[const.MMUType] = None

        if max_retries > 1:
            self.conn = RetryingSession(max_retries=max_retries)
        else:
            self.conn = Session()

        self.queue = Queue()

        self.command = Command(self.event_cb)
        self.set_handler(const.Command.SEND_INFO, self.send_info)
        self.set_handler(const.Command.SEND_FILE_INFO, self.get_file_info)
        self.set_handler(const.Command.CREATE_FOLDER, self.create_folder)
        self.set_handler(const.Command.CREATE_DIRECTORY, self.create_folder)
        self.set_handler(const.Command.DELETE_FILE, self.delete_file)
        self.set_handler(const.Command.DELETE_FOLDER, self.delete_folder)
        self.set_handler(const.Command.DELETE_DIRECTORY, self.delete_folder)
        self.set_handler(const.Command.START_URL_DOWNLOAD,
                         self.start_url_download)
        self.set_handler(const.Command.START_CONNECT_DOWNLOAD,
                         self.start_connect_download)
        self.set_handler(const.Command.STOP_TRANSFER, self.transfer_stop)
        self.set_handler(const.Command.SEND_TRANSFER_INFO, self.transfer_info)
        self.set_handler(const.Command.SET_PRINTER_READY,
                         self.set_printer_ready)
        self.set_handler(const.Command.CANCEL_PRINTER_READY,
                         self.cancel_printer_ready)

        self.fs = Filesystem(sep=os.sep, event_cb=self.event_cb)
        self.inotify_handler = InotifyHandler(self.fs)
        # Handler blocks communication with Connect in loop method!
        self.register_handler = default_register_handler
        self.__printed_file_cb = lambda: None
        self.download_finished_cb = lambda transfer: None  # noaq: ARG005

        self.clock_watcher = ClockWatcher()

        if self.token and not self.is_initialised():
            log.warning(self.NOT_INITIALISED_MSG)

        self.transfer = Transfer()
        self.download_mgr = DownloadMgr(self.fs, self.transfer,
                                        self.get_connection_details,
                                        self.event_cb, self.__printed_file_cb,
                                        self.download_finished_cb)
        self.camera_controller = CameraController(self.conn, self.server,
                                                  self.send_cb)
        self.__running_loop = False

    @staticmethod
    def connect_url(host: str, tls: bool, port: int = 0):
        """Format url from settings value.

        >>> Printer.connect_url('connect', True)
        'https://connect'
        >>> Printer.connect_url('connect', False)
        'http://connect'
        >>> Printer.connect_url('connect', False, 8000)
        'http://connect:8000'
        """
        protocol = 'https' if tls else 'http'
        if port:
            return f"{protocol}://{host}:{port}"
        return f"{protocol}://{host}"

    @property
    def printed_file_cb(self):
        """Returns path of currently printed file"""
        return self.__printed_file_cb

    @printed_file_cb.setter
    def printed_file_cb(self, value):
        """Sets path of currently printed file"""
        self.__printed_file_cb = value
        self.download_mgr.printed_file_cb = value

    @property
    def ready(self):
        """Returns ready flag.

        Ready flag can be set with set_state method. It is additional
        flag for IDLE state, which has info about user confirmation
        *ready to print*.
        """
        return self.__ready

    @property
    def state(self):
        """Returns printer state."""
        return self.__state

    @property
    def fingerprint(self):
        """Returns printer fingerprint."""
        return self.__fingerprint

    @fingerprint.setter
    def fingerprint(self, value):
        """Set fingerprint if is not set."""
        if self.__fingerprint is not None:
            raise RuntimeError("Fingerprint is already set.")
        self.__fingerprint = value

    @property
    def sn(self):
        """Returns printer serial number"""
        return self.__sn

    @sn.setter
    def sn(self, value):
        """Set serial number if is not set."""
        if self.__sn is not None:
            raise RuntimeError("Serial number is already set.")
        self.__sn = value

    @property
    def type(self):
        """Returns printer type"""
        return self.__type

    @type.setter
    def type(self, value):
        """Set the printer type if is not set."""
        if self.__type is not None:
            raise RuntimeError("Printer type is already set.")
        self.__type = value

    def is_initialised(self):
        """Returns True if the printer is initialised"""
        initialised = bool(self.__sn and self.__fingerprint
                           and self.__type is not None)
        if not initialised:
            errors.API.ok = False
            API.state = CondState.NOK
        return initialised

    def make_headers(self, timestamp: Optional[float] = None) -> dict:
        """Returns request headers from connection variables."""
        timestamp = get_timestamp(timestamp)

        headers = {
            "Fingerprint": self.fingerprint,
            "Timestamp": str(timestamp),
            "User-Agent": f"Prusa-Connect-SDK-Printer/{__version__}",
        }
        if self.token:
            headers['Token'] = self.token

        if self.clock_watcher.clock_adjusted():
            log.debug("Clock adjustment detected. Resetting watcher")
            headers['Clock-Adjusted'] = "1"
            self.clock_watcher.reset()

        return headers

    def set_state(self,
                  state: const.State,
                  source: const.Source,
                  ready: Optional[bool] = None,
                  **kwargs):
        """Set printer state and push event about that to queue.

        :source: the initiator of printer state
        :ready: If state is PRINTING, ready argument is ignored,
            and flag is set to False.
        """
        if state == const.State.PRINTING:
            self.__ready = False
        elif ready is not None:
            self.__ready = ready
        self.__state = state
        self.event_cb(const.Event.STATE_CHANGED, source, state=state, **kwargs)

    def event_cb(self,
                 event: const.Event,
                 source: const.Source,
                 timestamp: Optional[float] = None,
                 command_id: Optional[int] = None,
                 **kwargs) -> None:
        """Create event and push it to queue."""
        if not self.token:
            log.debug("Skipping event, no token: %s", event.value)
            return
        if self.job_id:
            kwargs['job_id'] = self.job_id
        if self.transfer.in_progress and self.transfer.start_ts:
            kwargs['transfer_id'] = self.transfer.transfer_id
        if 'state' not in kwargs:
            kwargs['state'] = self.state
        event_ = Event(event, source, timestamp, command_id, **kwargs)
        log.debug("Putting event to queue: %s", event_)
        if not self.is_initialised():
            log.warning("Printer fingerprint and/or SN is not set")
        self.queue.put(event_)

    def telemetry(self,
                  state: Optional[const.State] = None,
                  timestamp: Optional[float] = None,
                  **kwargs) -> None:
        """Create telemetry end push it to queue."""
        if state:
            log.warning("State argument is deprecated. Use set_state method.")
        if not self.token:
            log.debug("Skipping telemetry, no token.")
            return
        if self.command.state is not None:
            kwargs['command_id'] = self.command.command_id
        if self.job_id:
            kwargs['job_id'] = self.job_id
        if self.transfer.in_progress and self.transfer.start_ts:
            kwargs['transfer_id'] = self.transfer.transfer_id
            kwargs['transfer_progress'] = self.transfer.progress
            kwargs['transfer_time_remaining'] = self.transfer.time_remaining()
            kwargs['transfer_transferred'] = self.transfer.transferred
            kwargs['time_transferring'] = self.transfer.time_transferring()
        if self.is_initialised():
            telemetry = Telemetry(self.__state, timestamp, **kwargs)
        else:
            telemetry = Telemetry(self.__state, timestamp)
            log.warning("Printer fingerprint and/or SN is not set")
        self.queue.put(telemetry)

    def send_cb(self, loop_object: LoopObject):
        """Enqueues any supported loop object for sending,
        without modifying it"""
        self.queue.put(loop_object)

    def connection_from_config(self, path: str):
        """Loads connection details from config."""
        if not os.path.exists(path):
            raise FileNotFoundError(f"ini file: `{path}` doesn't exist")
        config = configparser.ConfigParser()
        config.read(path)

        host = config['service::connect']['hostname']
        tls = config['service::connect'].getboolean('tls')
        port = config['service::connect'].getint('port', fallback=0)

        server = Printer.connect_url(host, tls, port)
        token = config['service::connect']['token']
        self.set_connection(server, token)

    def set_connection(self, server, token):
        """Sets the connection details"""
        self.server = server
        self.token = token
        self.camera_controller.server = server
        errors.TOKEN.ok = True
        TOKEN.state = CondState.OK

    def get_connection_details(self):
        """Returns currently set server and headers"""
        return self.server, self.make_headers()

    def get_info(self) -> Dict[str, Any]:
        """Returns kwargs for Command.finish method as reaction
         to SEND_INFO."""
        # pylint: disable=unused-argument
        if self.__type is not None:
            type_, ver, sub = self.__type.value
        else:
            type_, ver, sub = (None, None, None)

        mmu: Dict[str, Any] = {"enabled": self.mmu_enabled}
        if self.mmu_fw is not None:
            mmu["version"] = self.mmu_fw

        data = {
            "source": const.Source.CONNECT,
            "event": const.Event.INFO,
            "state": self.__state,
            "type": type_,
            "version": ver,
            "subversion": sub,
            "firmware": self.firmware,
            "sdk": __version__,
            "network_info": self.network_info,
            "api_key": self.api_key,
            "files": self.fs.to_dict_legacy(),
            "sn": self.sn,
            "fingerprint": self.fingerprint,
            "mbl": self.mbl,
            "sheet_settings": self.sheet_settings,
            "active_sheet": self.active_sheet,
            "mmu": mmu,
        }
        if self.mmu_type is not None and self.mmu_enabled:
            data["slots"] = MMU_SLOT_COUNTS.get(self.mmu_type)
        return data

    def send_info(self, caller: Command) -> Dict[str, Any]:
        """Accept command arguments and adapt the call for the getter"""
        # pylint: disable=unused-argument
        return self.get_info()

    def start_url_download(self, caller: Command) -> Dict[str, Any]:
        """Download an URL specified by url, to_select and to_print flags
        in `caller`"""
        if not caller.kwargs:
            raise ValueError(
                f"{const.Command.START_URL_DOWNLOAD} requires kwargs")

        try:
            retval = self.download_mgr.start(
                const.TransferType.FROM_WEB,
                caller.kwargs["path"],
                caller.kwargs["url"],
                to_print=caller.kwargs.get("printing", False),
                to_select=caller.kwargs.get("selecting", False),
                start_cmd_id=caller.command_id)
            retval['source'] = const.Source.CONNECT
            return retval
        except KeyError as err:
            raise ValueError(f"{const.Command.START_URL_DOWNLOAD} requires "
                             f"kwarg {err}.") from None

    def start_connect_download(self, caller: Command) -> Dict[str, Any]:
        """Download a gcode from Connect, compose an URL using
        Connect config"""
        if not caller.kwargs:
            raise ValueError(
                f"{const.Command.START_CONNECT_DOWNLOAD} requires kwargs")

        if not self.server:
            raise RuntimeError("Printer.server must be set!")

        try:
            uri = "/p/teams/{team_id}/files/{hash}/raw".format(**caller.kwargs)
            retval = self.download_mgr.start(
                const.TransferType.FROM_CONNECT,
                caller.kwargs["path"],
                self.server + uri,
                to_print=caller.kwargs.get("printing", False),
                to_select=caller.kwargs.get("selecting", False),
                start_cmd_id=caller.command_id,
                hash_=caller.kwargs["hash"],
                team_id=caller.kwargs["team_id"])
            retval['source'] = const.Source.CONNECT
            return retval
        except KeyError as err:
            raise ValueError(
                f"{const.Command.START_CONNECT_DOWNLOAD} requires "
                f"kwarg {err}.") from None

    def transfer_stop(self, caller: Command) -> Dict[str, Any]:
        """Stop current transfer, if any"""
        # pylint: disable=unused-argument
        transfer_id = (caller.kwargs or {}).get("transfer_id")
        if transfer_id and transfer_id != self.transfer.transfer_id:
            raise RuntimeError("Wrong transfer_id")
        self.transfer.stop()
        return {"source": const.Source.CONNECT}

    def transfer_info(self, caller: Command) -> Dict[str, Any]:
        """Provide info of the running transfer"""
        kwargs = caller.kwargs or {}
        transfer_id = kwargs.get('transfer_id')
        if transfer_id and transfer_id != self.transfer.transfer_id:
            raise CommandFailed("Not current transfer.")
        info = self.download_mgr.info()
        info['source'] = const.Source.CONNECT
        info['event'] = const.Event.TRANSFER_INFO
        return info

    def set_printer_ready(self, caller: Command) -> Dict[str, Any]:
        """Set READY state"""
        # pylint: disable=unused-argument
        self.set_state(const.State.READY, const.Source.CONNECT, ready=True)
        return {'source': const.Source.CONNECT}

    def cancel_printer_ready(self, caller: Command) -> Dict[str, Any]:
        """Cancel READY state and switch printer back to IDLE"""
        # pylint: disable=unused-argument
        if self.ready:
            self.set_state(const.State.IDLE, const.Source.CONNECT, ready=False)
            return {'source': const.Source.CONNECT}
        raise ValueError("Can't cancel, printer isn't ready")

    def get_file_info(self, caller: Command) -> Dict[str, Any]:
        """Returns file info for a given file, if it exists."""
        # pylint: disable=unused-argument
        if not caller.kwargs or "path" not in caller.kwargs:
            raise ValueError("SEND_FILE_INFO requires kwargs")

        path = caller.kwargs["path"]
        node = self.fs.get(path)
        if node is None:
            raise ValueError(f"File does not exist: {path}")

        if node.is_dir:
            raise ValueError("FILE_INFO doesn't work for folders")

        info = {
            "source": const.Source.CONNECT,
            "event": const.Event.FILE_INFO,
            "path": path,
        }

        try:
            path_ = os.path.split(self.fs.get_os_path(path))
            if not path_[1].startswith("."):
                meta = get_metadata(self.fs.get_os_path(path))
                info.update(node.attrs)
                info.update(meta.data)

                # include the biggest thumbnail, if available
                if meta.thumbnails:
                    biggest = b""
                    for _, data in meta.thumbnails.items():
                        if len(data) > len(biggest):
                            biggest = data
                    info['preview'] = biggest.decode()
        except FileNotFoundError:
            log.debug("File not found: %s", path)

        return info

    def delete_file(self, caller: Command) -> Dict[str, Any]:
        """Handler for delete a file."""
        if not caller.kwargs or "path" not in caller.kwargs:
            raise ValueError(f"{caller.command_name} requires kwargs")

        if self.fs.get(caller.kwargs["path"]).to_dict()["read_only"]:
            raise ValueError("File is read only")

        if self.printed_file_cb() == caller.kwargs["path"]:
            raise ValueError("This file is currently printed")

        abs_path = self.inotify_handler.get_abs_os_path(caller.kwargs["path"])

        delete(abs_path, False)

        return {"source": const.Source.CONNECT}

    def delete_folder(self, caller: Command) -> Dict[str, Any]:
        """Handler for delete a folder."""
        if not caller.kwargs or "path" not in caller.kwargs:
            raise ValueError(f"{caller.command_name} requires kwargs")

        if self.fs.get(caller.kwargs["path"]).to_dict()["read_only"]:
            raise ValueError("Folder is read only")

        if self.printed_file_cb():
            if caller.kwargs["path"] in self.printed_file_cb():
                raise ValueError(
                    "The file inside of this folder is currently printed")

        abs_path = self.inotify_handler.get_abs_os_path(caller.kwargs["path"])

        delete(abs_path, True, force=caller.kwargs.get("force", False))

        return {"source": const.Source.CONNECT}

    def create_folder(self, caller: Command) -> Dict[str, Any]:
        """Handler for create a folder."""
        if not caller.kwargs or "path" not in caller.kwargs:
            raise ValueError(f"{caller.command_name} requires kwargs")

        relative_path_parameter = caller.kwargs["path"]
        abs_path = self.inotify_handler.get_abs_os_path(
            relative_path_parameter)

        os.makedirs(abs_path, exist_ok=True)
        return {"source": const.Source.CONNECT}

    def set_handler(self, command: const.Command,
                    handler: Callable[[Command], Dict[str, Any]]):
        """Set handler for the command.

        Handler must return **kwargs dictionary for Command.finish method,
        which means that source must be set at least.
        """
        self.command.handlers[command] = handler

    def handler(self, command: const.Command):
        """Wrap function to handle the command.

        Handler must return **kwargs dictionary for Command.finish method,
        which means that source must be set at least.

        .. code:: python

            @printer.command(const.GCODE)
            def gcode(prn, gcode):
                ...
        """
        def wrapper(handler: Callable[[Command], Dict[str, Any]]):
            self.set_handler(command, handler)
            return handler

        return wrapper

    def parse_command(self, res: Response):
        """Parse telemetry response.

        When response from connect is command (HTTP Status: 200 OK), it
        will set a command object, if the printer is initialized properly.
        """
        if res.status_code == 200:
            command_id: Optional[int] = None
            try:
                command_id_string = res.headers.get("Command-Id", default="")
                command_id = int(command_id_string)
            except (TypeError, ValueError):
                log.error("Invalid Command-Id header. Headers: %s",
                          res.headers)
                self.event_cb(const.Event.REJECTED,
                              const.Source.CONNECT,
                              reason="Invalid Command-Id header")
                return res
            if not self.is_initialised():
                self.event_cb(const.Event.REJECTED,
                              const.Source.WUI,
                              command_id=command_id,
                              reason=self.NOT_INITIALISED_MSG)
                return res
            content_type = res.headers.get("content-type", default="")
            log.debug("parse_command res: %s", res.text)
            try:
                if content_type.startswith("application/json"):
                    data = res.json()
                    command_name = data.get("command", "")
                    if self.command.check_state(command_id, command_name):
                        self.command.accept(command_id,
                                            command_name=command_name,
                                            args=data.get("args"),
                                            kwargs=data.get('kwargs'))
                elif content_type == "text/x.gcode":
                    command_name = const.Command.GCODE.value
                    if self.command.check_state(command_id, command_name):
                        force = ("Force" in res.headers
                                 and res.headers["Force"] == "1")
                        self.command.accept(command_id,
                                            command_name, [res.text],
                                            {"gcode": res.text},
                                            force=force)
                else:
                    raise ValueError("Invalid command content type")
            except Exception as e:  # pylint: disable=broad-except
                log.exception("")
                self.event_cb(const.Event.REJECTED,
                              const.Source.CONNECT,
                              command_id=command_id,
                              reason=str(e))
        elif res.status_code == 204:  # no cmd in telemetry
            pass
        else:
            log.info("Got unexpected telemetry response (%s): %s",
                     res.status_code, res.text)
        return res

    def register(self):
        """Register the printer with Connect and return a registration
        temporary code, or fail with a RuntimeError."""
        if not self.server:
            raise RuntimeError("Server is not set")

        # type-version-subversion is deprecated and replaced by printer_type
        data = {
            "sn": self.sn,
            "fingerprint": self.fingerprint,
            "printer_type": str(self.__type),
            "firmware": self.firmware,
        }
        res = self.conn.post(self.server + "/p/register",
                             headers=self.make_headers(),
                             json=data,
                             timeout=const.CONNECTION_TIMEOUT)

        if res.status_code != 200:
            errors.API.ok = False
            API.state = CondState.NOK
            if res.status_code >= 500:
                errors.HTTP.ok = False
                HTTP.state = CondState.NOK
            else:
                errors.HTTP.ok = True
                HTTP.state = CondState.OK
            log.debug("Status code: {res.status_code}")
            raise RuntimeError(res.text)

        self.code = res.headers["Code"]
        self.queue.put(Register(self.code))
        errors.API.ok = True
        API.state = CondState.OK
        return self.code

    def loop(self):
        """Calls loop_step in a loop. Handles any unexpected Exceptions"""
        self.__running_loop = True
        while self.__running_loop:
            try:
                self.camera_controller.tick()
            # pylint: disable=broad-except
            except Exception:
                log.exception(
                    "Unexpected exception from the camera module caught in"
                    " SDK loop!")
            try:
                self.loop_step()
            # pylint: disable=broad-except
            except Exception:
                log.exception("Unexpected exception caught in SDK loop!")

    def loop_step(self):
        """
        Gets an item LoopObject from queue, sends it and handles the response
        The LoopObject is either an Event - in which case it's just sent,
        a Telemetry, in which case the response might contain a command to
        execute, a Register object in which case the response contains the
        credentials for further communication.
        """
        # pylint: disable=too-many-branches
        # pylint: disable=too-many-statements
        try:
            # Get the item to send
            item = self.queue.get(timeout=const.TIMESTAMP_PRECISION)
        except Empty:
            return

        # Make sure we're able to send it
        if not self.server:
            log.warning("Server is not set, skipping item: %s", item)
            return
        if not issubclass(type(item), LoopObject):
            log.warning("Enqueued an unknown item: %s", item)
            return
        if item.needs_token and not self.token:
            errors.TOKEN.ok = False
            TOKEN.state = CondState.NOK
            log.warning("No token, skipping item: %s", item)
            return

        # Send it
        headers = self.make_headers(item.timestamp)
        try:
            res = item.send(self.conn, self.server, headers)
        except ReadTimeoutError as err:
            errors.HTTP.ok = False
            HTTP.state = CondState.NOK
            log.error("Experiencing connect communication problems - %s", err)
        except ConnectionError as err:
            errors.HTTP.ok = False
            HTTP.state = CondState.NOK
            log.error(err)
        except RequestException as err:
            errors.INTERNET.ok = False
            INTERNET.state = CondState.NOK
            log.error(err)
        except Exception:  # pylint: disable=broad-except
            errors.INTERNET.ok = False
            INTERNET.state = CondState.NOK
            log.exception('Unhandled error')
        else:
            # Handle the response
            if isinstance(item, Telemetry):
                self.parse_command(res)
            elif isinstance(item, Register):
                if res.status_code == 200:
                    self.token = res.headers["Token"]
                    errors.TOKEN.ok = True
                    TOKEN.state = CondState.OK
                    log.info("New token was set.")
                    self.register_handler(self.token)
                    self.code = None
                elif res.status_code == 202 and item.timeout > time():
                    self.queue.put(item)
                    sleep(1)
            elif isinstance(item, CameraRegister):
                camera = item.camera
                # pylint: disable=unused-argument
                if res.status_code == 200:
                    camera_token = res.headers["Token"]
                    camera.set_token(camera_token)
                else:
                    log.warning(res.text)

            self.deduce_state_from_code(res.status_code)
            if res.status_code > 400:
                log.warning(res.text)
            elif res.status_code == 400:
                log.debug(res.text)

    @staticmethod
    def deduce_state_from_code(status_code):
        """Deduce our state from the HTTP status code"""
        if 299 >= status_code >= 200:
            errors.API.ok = True
            API.state = CondState.OK

        elif status_code == 403:
            errors.TOKEN.ok = False
            TOKEN.state = CondState.NOK

        elif status_code > 400:
            errors.API.ok = False
            API.state = CondState.NOK

    def stop_loop(self):
        """Set internal variable, to stop the loop method."""
        self.__running_loop = False

    def attach(self, folderpath: str, storage: str):
        """Create a listing of `folderpath` and attach it under `storage`.

        This requires linux kernel with inotify support enabled to work.
        """
        self.fs.from_dir(folderpath, storage)
        self.inotify_handler = InotifyHandler(self.fs)

    def detach(self, storage: str):
        """Detach `storage`.

        This requires linux kernel with inotify support enabled to work.
        """
        self.fs.detach(storage)
        self.inotify_handler = InotifyHandler(self.fs)

"""Python printer library for Prusa Connect."""
from __future__ import annotations  # noqa

import configparser
import os
import re
from json import JSONDecodeError
from logging import getLogger
from queue import Queue, Empty
from time import time, sleep
from typing import Optional, List, Any, Callable, Dict, Union

from requests import Session
# pylint: disable=redefined-builtin
from requests.exceptions import ConnectionError

from . import const
from .command import Command
from .errors import SDKServerError, SDKConnectionError
from .files import Filesystem, InotifyHandler, delete
from .metadata import get_metadata
from .models import Event, Telemetry

__version__ = "0.3.0.dev0"
__date__ = "14 Dec 2020"  # version date
__copyright__ = "(c) 2020 Prusa 3D"
__author_name__ = "Ondřej Tůma"
__author_email__ = "ondrej.tuma@prusa3d.cz"
__author__ = f"{__author_name__} <{__author_email__}>"
__description__ = "Python printer library for Prusa Connect"

__credits__ = "Ondřej Tůma, Martin Užák, Jan Pilař"
__url__ = "https://github.com/prusa3d/Prusa-Connect-SDK-Printer"

# pylint: disable=invalid-name
# pylint: disable=too-few-public-methods
# pylint: disable=too-many-arguments
# pylint: disable=too-many-instance-attributes
# NOTE: Temporary for pylint with python3.9
# pylint: disable=unsubscriptable-object

CODE_TIMEOUT = 60 * 30  # 30 min

log = getLogger("connect-printer")
re_conn_reason = re.compile(r"] (.*)")

__all__ = ["Printer", "Notifications"]

CommandArgs = Optional[List[Any]]


class Register:
    """Item for get_token action."""
    def __init__(self, code):
        self.code = code
        self.timeout = int(time()) + CODE_TIMEOUT


def default_register_handler(token):
    """Default register handler.

    It blocks communication with Connect in loop method!
    """
    assert token


class Printer:
    """Printer representation object."""
    # pylint: disable=too-many-public-methods

    queue: "Queue[Union[Event, Telemetry, Register]]"
    server: Optional[str] = None
    token: Optional[str] = None

    NOT_INITIALISED_MSG = "Printer has not been initialized properly"

    def __init__(self,
                 type_: const.PrinterType,
                 sn: str = None,
                 fingerprint: str = None):
        self.type = type_
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
        }
        self.api_key = None

        self.__state = const.State.BUSY
        self.job_id = None

        self.conn = Session()
        self.queue = Queue()

        self.command = Command(self.event_cb)
        self.set_handler(const.Command.SEND_INFO, self.send_info)
        self.set_handler(const.Command.SEND_FILE_INFO, self.get_file_info)
        self.set_handler(const.Command.CREATE_DIRECTORY, self.create_directory)
        self.set_handler(const.Command.DELETE_FILE, self.delete_file)
        self.set_handler(const.Command.DELETE_DIRECTORY, self.delete_directory)

        self.fs = Filesystem(sep=os.sep, event_cb=self.event_cb)
        self.inotify_handler = InotifyHandler(self.fs)
        # Handler blocks communication with Connect in loop method!
        self.register_handler = default_register_handler

        if not self.is_initialised():
            log.warning(self.NOT_INITIALISED_MSG)

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
    def state(self):
        """Return printer state."""
        return self.__state

    @property
    def fingerprint(self):
        """Return printer fingerprint."""
        return self.__fingerprint

    @fingerprint.setter
    def fingerprint(self, value):
        """Set fingerprint if is not set."""
        if self.__fingerprint is not None:
            raise RuntimeError("Fingerprint is already set.")
        self.__fingerprint = value

    @property
    def sn(self):
        """Return printer serial number"""
        return self.__sn

    @sn.setter
    def sn(self, value):
        """Set serial number if is not set."""
        if self.__sn is not None:
            raise RuntimeError("Serial number is already set.")
        self.__sn = value

    def is_initialised(self):
        """Return True if the printer is initialised"""
        return bool(self.__sn and self.__fingerprint)

    def make_headers(self, timestamp: float = None) -> dict:
        """Return request headers from connection variables."""
        timestamp = timestamp or int(time() * 10) * const.TIMESTAMP_PRECISION

        headers = {
            "Fingerprint": self.fingerprint,
            "Timestamp": str(timestamp)
        }
        if self.token:
            headers['Token'] = self.token
        return headers

    def set_state(self, state: const.State, source: const.Source, **kwargs):
        """Set printer state and push event about that to queue.

        :source: the initiator of printer state
        """
        self.__state = state
        self.event_cb(const.Event.STATE_CHANGED,
                      source,
                      state=state.value,
                      **kwargs)

    def event_cb(self,
                 event: const.Event,
                 source: const.Source,
                 timestamp: float = None,
                 command_id: int = None,
                 **kwargs) -> None:
        """Create event and push it to queue."""
        if self.job_id:
            kwargs['job_id'] = self.job_id
        event_ = Event(event, source, timestamp, command_id, **kwargs)
        log.debug("Putting event to queue: %s", event_)
        if not self.is_initialised():
            log.warning("Printer fingerprint and/or SN is not set")
        self.queue.put(event_)

    def telemetry(self,
                  state: const.State,
                  timestamp: float = None,
                  **kwargs) -> None:
        """Create telemetry end push it to queue."""
        if self.job_id:
            kwargs['job_id'] = self.job_id
        if self.is_initialised():
            telemetry = Telemetry(state, timestamp, **kwargs)
        else:
            telemetry = Telemetry(state, timestamp)
            log.warning("Printer fingerprint and/or SN is not set")
        self.queue.put(telemetry)

    def set_connection(self, path: str):
        """Set connection from ini config."""
        if not os.path.exists(path):
            raise FileNotFoundError(f"ini file: `{path}` doesn't exist")
        config = configparser.ConfigParser()
        config.read(path)

        host = config['connect']['address']
        tls = config['connect'].getboolean('tls')
        port = config['connect'].getint('port', fallback=0)
        self.server = Printer.connect_url(host, tls, port)
        self.token = config['connect']['token']

    def get_info(self) -> Dict[str, Any]:
        """Return kwargs for Command.finish method as reaction to SEND_INFO."""
        # pylint: disable=unused-argument
        type_, ver, sub = self.type.value
        return dict(source=const.Source.CONNECT,
                    event=const.Event.INFO,
                    state=self.__state.value,
                    type=type_,
                    version=ver,
                    subversion=sub,
                    firmware=self.firmware,
                    sdk=__version__,
                    network_info=self.network_info,
                    api_key=self.api_key,
                    files=self.fs.to_dict(),
                    sn=self.sn)

    def send_info(self, caller: Command) -> Dict[str, Any]:
        """Accept command arguments and adapt the call for the getter"""
        # pylint: disable=unused-argument
        return self.get_info()

    def get_file_info(self, caller: Command) -> Dict[str, Any]:
        """Return file info for a given file, if it exists."""
        # pylint: disable=unused-argument
        if not caller.args:
            raise ValueError("SEND_FILE_INFO requires args")

        path = caller.args[0]
        node = self.fs.get(path)
        if node is None:
            raise ValueError(f"File does not exist: {path}")

        info = dict(
            source=const.Source.CONNECT,
            event=const.Event.FILE_INFO,
            path=path,
        )

        try:
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
        """Handler for delete file."""
        if not caller.args:
            raise ValueError(f"{caller.command} requires args")

        abs_path = self.inotify_handler.get_abs_os_path(caller.args[0])

        delete(abs_path, False)

        return dict(source=const.Source.CONNECT)

    def delete_directory(self, caller: Command) -> Dict[str, Any]:
        """Handler for delete directory."""
        if not caller.args:
            raise ValueError(f"{caller.command} requires args")

        abs_path = self.inotify_handler.get_abs_os_path(caller.args[0])

        delete(abs_path, True)

        return dict(source=const.Source.CONNECT)

    def create_directory(self, caller: Command) -> Dict[str, Any]:
        """Handler for create directory."""
        if not caller.args:
            raise ValueError(f"{caller.command} requires args")

        relative_path_parameter = caller.args[0]
        abs_path = self.inotify_handler.get_abs_os_path(
            relative_path_parameter)

        os.makedirs(abs_path)
        return dict(source=const.Source.CONNECT)

    def set_handler(self, command: const.Command,
                    handler: Callable[[Command], Dict[str, Any]]):
        """Set handler for command.

        Handler must return **kwargs dictionary for Command.finish method,
        which means that source must be set at least.
        """
        self.command.handlers[command] = handler

    def handler(self, command: const.Command):
        """Wrap function to handle command.

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

    def parse_command(self, res):
        """Parse telemetry response.

        When response from connect is command (HTTP Status: 200 OK), it
        will set command object, if the printer is initialized properly.
        """
        if res.status_code == 200:
            command_id: Optional[int] = None
            try:
                command_id = int(res.headers.get("Command-Id"))
            except (TypeError, ValueError):
                log.error("Invalid Command-Id header: %s",
                          res.headers.get("Command-Id"))
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

            content_type = res.headers.get("content-type")
            log.debug("parse_command res: %s", res.text)
            try:
                if content_type.startswith("application/json"):
                    data = res.json()
                    if self.command.check_state(command_id):
                        self.command.accept(command_id,
                                            data.get("command", ""),
                                            data.get("args"))
                elif content_type == "text/x.gcode":
                    if self.command.check_state(command_id):
                        force = ("Force" in res.headers
                                 and res.headers["Force"] == "1")
                        self.command.accept(command_id,
                                            const.Command.GCODE.value,
                                            [res.text],
                                            force=force)
                else:
                    raise ValueError("Invalid command content type")
            except Exception as e:  # pylint: disable=broad-except
                log.exception("")
                self.event_cb(const.Event.REJECTED,
                              const.Source.CONNECT,
                              command_id=command_id,
                              reason=str(e))
        return res

    def register(self):
        """Register the printer with Connect and return a registration
        temporary code, or fail with a RuntimeError."""
        if not self.server:
            raise RuntimeError("Server is not set")

        data = {
            "sn": self.sn,
            "type": self.type.value[0],
            "version": self.type.value[1],
            "subversion": self.type.value[2],
            "firmware": self.firmware
        }
        res = self.conn.post(self.server + "/p/register",
                             headers=self.make_headers(),
                             json=data)
        if res.status_code == 200:
            code = res.headers['Temporary-Code']
            self.queue.put(Register(code))
            return code

        log.debug("Status code: {res.status_code}")
        raise RuntimeError(res.text)

    def get_token(self, tmp_code):
        """Prepare request and return response for GET /p/register."""
        if not self.server:
            raise RuntimeError("Server is not set")

        headers = self.make_headers()
        headers["Temporary-Code"] = tmp_code
        return self.conn.get(self.server + "/p/register", headers=headers)

    def loop(self):
        """This method is responsible for communication with Connect.

        In a loop it gets an item (Event or Telemetry) from queue and sets
        Printer.command object, when the command is in the answer to telemetry.
        """
        # pylint: disable=too-many-branches
        while True:
            try:
                self.inotify_handler()

                item = self.queue.get(timeout=const.TIMESTAMP_PRECISION)
                if not self.server:
                    log.warning("Server is not set, skipping item from queue")
                    continue

                if isinstance(item, Telemetry):
                    headers = self.make_headers(item.timestamp)
                    log.debug("Sending telemetry: %s", item)
                    res = self.conn.post(self.server + '/p/telemetry',
                                         headers=headers,
                                         json=item.to_payload())
                    log.debug("Telemetry response: %s", res.text)
                    self.parse_command(res)
                elif isinstance(item, Event):
                    log.debug("Sending event: %s", item)
                    res = self.conn.post(self.server + '/p/events',
                                         headers=self.make_headers(
                                             item.timestamp),
                                         json=item.to_payload())
                    log.debug("Event response: %s", res.text)
                elif isinstance(item, Register):
                    log.debug("Getting token")
                    res = self.get_token(item.code)
                    log.debug("Get register response: (%d) %s",
                              res.status_code, res.text)
                    if res.status_code == 200:
                        self.token = res.headers["Token"]
                        log.info("New token was set.")
                        self.register_handler(self.token)
                    elif res.status_code == 202 and item.timeout > time():
                        self.queue.put(item)
                        sleep(1)
                else:
                    log.error("Unknown item: %s", str(item))

                if res.status_code >= 400:
                    try:
                        message = res.json()["message"]
                    except (JSONDecodeError, KeyError):
                        message = "Wrong Connect answer."
                    sdk_err = SDKServerError(res.status_code, message)
                    self.loop_exc_handler(sdk_err)
            except Empty:
                continue
            except ConnectionError as err:
                if err.args:
                    reason = err.args[0]
                    sdk_err = SDKConnectionError(reason)
                else:
                    sdk_err = SDKConnectionError()
                self.loop_exc_handler(sdk_err)

    def loop_exc_handler(self, err):
        """This method is called with the exception that happened
        in `self.loop` as its argument"""
        # pylint: disable=no-self-use
        Notifications.handler(599, str(err))

    def mount(self, dirpath: str, mountpoint: str):
        """Create a listing of `dirpath` and mount it under `mountpoint`.

        This requires linux kernel with inotify support enabled to work.
        """
        self.fs.from_dir(dirpath, mountpoint)
        self.inotify_handler = InotifyHandler(self.fs)

    def unmount(self, mountpoint: str):
        """unmount `mountpoint`.

        This requires linux kernel with inotify support enabled to work.
        """
        self.fs.unmount(mountpoint)
        self.inotify_handler = InotifyHandler(self.fs)


def default_notification_handler(code, msg) -> Any:
    """Library notification handler call print."""
    print(f"{code}: {msg}")


class Notifications:
    """Notification class."""
    handler: Callable[[str, str], Any] = default_notification_handler

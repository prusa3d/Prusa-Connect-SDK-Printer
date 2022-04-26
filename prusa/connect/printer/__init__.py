"""Python printer library for Prusa Connect.

    Copyright (C) 2022 PrusaResearch

    This program is free software: you can redistribute it and/or modify
    it under the terms of the GNU Affero General Public License as published
    by the Free Software Foundation, either version 3 of the License, or
    (at your option) any later version.

    This program is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
    GNU Affero General Public License for more details.

    You should have received a copy of the GNU Affero General Public License
    along with this program.  If not, see <https://www.gnu.org/licenses/>.
"""

import configparser
import os
import re
from logging import getLogger
from queue import Queue, Empty
from time import time, sleep
from typing import Optional, List, Any, Callable, Dict, Union

from requests import Session, RequestException
# pylint: disable=redefined-builtin
from requests.exceptions import ConnectionError

from . import const, errors
from .command import Command
from .files import Filesystem, InotifyHandler, delete
from .metadata import get_metadata
from .models import Event, Telemetry
from .clock import ClockWatcher
from .download import DownloadMgr, Transfer
from .util import RetryingSession

__version__ = "0.7.0.dev1"
__date__ = "22 Mar 2022"  # version date
__copyright__ = "(c) 2021 Prusa 3D"
__author_name__ = "Prusa Link Developers"
__author_email__ = "link@prusa3d.cz"
__author__ = f"{__author_name__} <{__author_email__}>"
__description__ = "Python printer library for Prusa Connect"

__credits__ = "Ondřej Tůma, Martin Užák, Michal Zoubek, Tomáš Jozífek"
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

__all__ = ["Printer"]

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
    """Printer representation object.

    To process inotify_handler, please create your own thread,
    calling printer.inotify_handler() in a loop.
    """
    # pylint: disable=too-many-public-methods

    queue: "Queue[Union[Event, Telemetry, Register]]"
    server: Optional[str] = None
    token: Optional[str] = None
    conn: Session

    NOT_INITIALISED_MSG = "Printer has not been initialized properly"

    def __init__(self,
                 type_: const.PrinterType = None,
                 sn: str = None,
                 fingerprint: str = None,
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
            "digest": None
        }
        self.api_key = None
        self.code = None

        self.__ready = False
        self.__state = const.State.BUSY
        self.job_id = None

        if max_retries > 1:
            self.conn = RetryingSession(max_retries=max_retries)
        else:
            self.conn = Session()

        self.queue = Queue()

        self.command = Command(self.event_cb)
        self.set_handler(const.Command.SEND_INFO, self.send_info)
        self.set_handler(const.Command.SEND_FILE_INFO, self.get_file_info)
        self.set_handler(const.Command.CREATE_DIRECTORY, self.create_directory)
        self.set_handler(const.Command.DELETE_FILE, self.delete_file)
        self.set_handler(const.Command.DELETE_DIRECTORY, self.delete_directory)
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
        self.printed_file_cb = lambda: None
        self.download_finished_cb = lambda Transfer: None

        self.clock_watcher = ClockWatcher()

        if self.token and not self.is_initialised():
            log.warning(self.NOT_INITIALISED_MSG)

        self.transfer = Transfer()
        self.download_mgr = DownloadMgr(self.fs, self.transfer,
                                        self.get_connection_details,
                                        self.event_cb, self.printed_file_cb,
                                        self.download_finished_cb)

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
        return initialised

    def make_headers(self, timestamp: float = None) -> dict:
        """Returns request headers from connection variables."""
        timestamp = timestamp or int(time() * 10) * const.TIMESTAMP_PRECISION

        headers = {
            "Fingerprint": self.fingerprint,
            "Timestamp": str(timestamp)
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
                  ready: bool = None,
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
        self.event_cb(const.Event.STATE_CHANGED,
                      source,
                      state=state,
                      **kwargs)

    def event_cb(self,
                 event: const.Event,
                 source: const.Source,
                 timestamp: float = None,
                 command_id: int = None,
                 **kwargs) -> None:
        """Create event and push it to queue."""
        if not self.token:
            log.debug("Skipping event, no token: %s", event.value)
            return
        if self.job_id:
            kwargs['job_id'] = self.job_id
        event_ = Event(event, source, timestamp, command_id, **kwargs)
        log.debug("Putting event to queue: %s", event_)
        if not self.is_initialised():
            log.warning("Printer fingerprint and/or SN is not set")
        self.queue.put(event_)

    def telemetry(self,
                  state: const.State = None,
                  timestamp: float = None,
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
            kwargs['transfer_progress'] = self.transfer.progress
            kwargs['transfer_time_remaining'] = self.transfer.time_remaining()
            kwargs['transfer_transferred'] = self.transfer.transferred
        if self.is_initialised():
            telemetry = Telemetry(self.__state, timestamp, **kwargs)
        else:
            telemetry = Telemetry(self.__state, timestamp)
            log.warning("Printer fingerprint and/or SN is not set")
        self.queue.put(telemetry)

    def set_connection(self, path: str):
        """Set connection from ini config."""
        if not os.path.exists(path):
            raise FileNotFoundError(f"ini file: `{path}` doesn't exist")
        config = configparser.ConfigParser()
        config.read(path)

        host = config['service::connect']['hostname']
        tls = config['service::connect'].getboolean('tls')
        port = config['service::connect'].getint('port', fallback=0)
        self.server = Printer.connect_url(host, tls, port)
        self.token = config['service::connect']['token']
        errors.TOKEN.ok = True

    def get_connection_details(self):
        """Returns currently set server and headers"""
        return (self.server, self.make_headers())

    def get_info(self) -> Dict[str, Any]:
        """Returns kwargs for Command.finish method as reaction
         to SEND_INFO."""
        # pylint: disable=unused-argument
        if self.__type is not None:
            type_, ver, sub = self.__type.value
        else:
            type_, ver, sub = (None, None, None)
        return dict(source=const.Source.CONNECT,
                    event=const.Event.INFO,
                    state=self.__state,
                    type=type_,
                    version=ver,
                    subversion=sub,
                    firmware=self.firmware,
                    sdk=__version__,
                    network_info=self.network_info,
                    api_key=self.api_key,
                    files=self.fs.to_dict(),
                    sn=self.sn,
                    fingerprint=self.fingerprint)

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
            self.download_mgr.start(
                const.TransferType.FROM_WEB,
                caller.kwargs["path"],
                caller.kwargs["url"],
                to_print=caller.kwargs.get("printing", False),
                to_select=caller.kwargs.get("selecting", False))
        except KeyError as err:
            raise ValueError(f"{const.Command.START_URL_DOWNLOAD} requires "
                             f"kwarg {err}.") from None

        return dict(source=const.Source.CONNECT)

    def start_connect_download(self, caller: Command) -> Dict[str, Any]:
        """Download a gcode from Connect, compose an URL using
        Connect config"""
        if not caller.kwargs:
            raise ValueError(
                f"{const.Command.START_CONNECT_DOWNLOAD} requires kwargs")

        try:
            self.download_mgr.start(
                const.TransferType.FROM_CONNECT,
                caller.kwargs["path"],
                self.server + caller.kwargs["source"],
                to_print=caller.kwargs.get("printing", False),
                to_select=caller.kwargs.get("selecting", False))

        except KeyError as err:
            raise ValueError(
                f"{const.Command.START_CONNECT_DOWNLOAD} requires "
                f"kwarg {err}.") from None

        return dict(source=const.Source.CONNECT)

    def transfer_stop(self, caller: Command) -> Dict[str, Any]:
        """Stop current transfer, if any"""
        # pylint: disable=unused-argument
        self.transfer.stop()
        return dict(source=const.Source.CONNECT)

    def transfer_info(self, caller: Command) -> Dict[str, Any]:
        """Provide info of the running transfer"""
        # pylint: disable=unused-argument
        info = self.download_mgr.info()
        info['source'] = const.Source.CONNECT
        info['event'] = const.Event.TRANSFER_INFO
        return info

    def set_printer_ready(self, caller: Command) -> Dict[str, Any]:
        """Set READY state"""
        # pylint: disable=unused-argument
        self.set_state(const.State.READY,
                       const.Source.CONNECT,
                       ready=True)
        return {'source': const.Source.CONNECT}

    def cancel_printer_ready(self, caller: Command) -> Dict[str, Any]:
        """Cancel PREPARED state and switch printer back to READY"""
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
            raise ValueError("FILE_INFO doesn't work for directories")

        info = dict(
            source=const.Source.CONNECT,
            event=const.Event.FILE_INFO,
            path=path,
        )

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
            raise ValueError(f"{caller.command} requires kwargs")

        abs_path = self.inotify_handler.get_abs_os_path(caller.kwargs["path"])

        delete(abs_path, False)

        return dict(source=const.Source.CONNECT)

    def delete_directory(self, caller: Command) -> Dict[str, Any]:
        """Handler for delete a directory."""
        if not caller.kwargs or "path" not in caller.kwargs:
            raise ValueError(f"{caller.command} requires kwargs")

        abs_path = self.inotify_handler.get_abs_os_path(caller.kwargs["path"])

        delete(abs_path, True)

        return dict(source=const.Source.CONNECT)

    def create_directory(self, caller: Command) -> Dict[str, Any]:
        """Handler for create a directory."""
        if not caller.kwargs or "path" not in caller.kwargs:
            raise ValueError(f"{caller.command} requires kwargs")

        relative_path_parameter = caller.kwargs["path"]
        abs_path = self.inotify_handler.get_abs_os_path(
            relative_path_parameter)

        os.makedirs(abs_path, exist_ok=True)
        return dict(source=const.Source.CONNECT)

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

    def parse_command(self, res):
        """Parse telemetry response.

        When response from connect is command (HTTP Status: 200 OK), it
        will set a command object, if the printer is initialized properly.
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
                                            data.get("command",
                                                     ""), data.get("args"),
                                            data.get('kwargs'))
                elif content_type == "text/x.gcode":
                    if self.command.check_state(command_id):
                        force = ("Force" in res.headers
                                 and res.headers["Force"] == "1")
                        self.command.accept(command_id,
                                            const.Command.GCODE.value,
                                            [res.text], {"gcode": res.text},
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
            "printer_type": self.__type.__str__(),
            "firmware": self.firmware
        }
        res = self.conn.post(self.server + "/p/register",
                             headers=self.make_headers(),
                             json=data,
                             timeout=const.CONNECTION_TIMEOUT)
        if res.status_code == 200:
            code = res.headers["Code"]
            self.code = code
            self.queue.put(Register(code))
            errors.API.ok = True
            return code

        errors.HTTP.ok = True
        errors.API.ok = False
        if res.status_code >= 500:
            errors.HTTP.ok = False
        log.debug("Status code: {res.status_code}")
        raise RuntimeError(res.text)

    def get_token(self, tmp_code):
        """Prepare request and return response for GET /p/register."""
        if not self.server:
            raise RuntimeError("Server is not set")

        headers = self.make_headers()
        headers["Code"] = tmp_code
        return self.conn.get(self.server + "/p/register",
                             headers=headers,
                             timeout=const.CONNECTION_TIMEOUT)

    def loop(self):
        """This method is responsible for communication with Connect.

        In a loop it gets an item (Event or Telemetry) from queue and sets
        Printer.command object, when the command is in the answer to telemetry.
        """
        # pylint: disable=too-many-branches
        # pylint: disable=too-many-statements
        self.__running_loop = True
        while self.__running_loop:
            try:
                item = self.queue.get(timeout=const.TIMESTAMP_PRECISION)
                if not self.server:
                    log.warning("Server is not set, skipping item from queue")
                    continue

                if isinstance(item, Telemetry) and self.token:
                    headers = self.make_headers(item.timestamp)
                    log.debug("Sending telemetry: %s", item)
                    res = self.conn.post(self.server + '/p/telemetry',
                                         headers=headers,
                                         json=item.to_payload(),
                                         timeout=const.CONNECTION_TIMEOUT)
                    log.debug("Telemetry response: %s", res.text)
                    self.parse_command(res)
                elif isinstance(item, Event) and self.token:
                    log.debug("Sending event: %s", item)
                    headers = self.make_headers(item.timestamp)
                    res = self.conn.post(self.server + '/p/events',
                                         headers=headers,
                                         json=item.to_payload(),
                                         timeout=const.CONNECTION_TIMEOUT)
                    log.debug("Event response: %s", res.text)
                elif isinstance(item, Register):
                    log.debug("Getting token")
                    res = self.get_token(item.code)
                    log.debug("Get register response: (%d) %s",
                              res.status_code, res.text)
                    if res.status_code == 200:
                        self.token = res.headers["Token"]
                        errors.TOKEN.ok = True
                        log.info("New token was set.")
                        self.register_handler(self.token)
                        self.code = None
                    elif res.status_code == 202 and item.timeout > time():
                        self.queue.put(item)
                        sleep(1)
                else:
                    log.debug("Item `%s` not sent, probably token isn't set.",
                              item)
                    continue  # No token - no communication

                errors.API.ok = True

                if res.status_code >= 400:
                    errors.API.ok = False
                    if res.status_code == 401:
                        errors.TOKEN.ok = False
            except Empty:
                continue
            except ConnectionError as err:
                errors.HTTP.ok = False
                log.error(err)
            except RequestException as err:
                errors.INTERNET.ok = False
                log.error(err)
            except Exception:  # pylint: disable=broad-except
                errors.INTERNET.ok = False
                log.exception('Unhandled error')

    def stop_loop(self):
        """Set internal variable, to stop the loop method."""
        self.__running_loop = False

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

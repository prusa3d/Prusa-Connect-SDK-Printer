"""Python printer library for Prusa Connect."""
from __future__ import annotations  # noqa

import configparser
import os
import re

from logging import getLogger
from time import time
from queue import Queue, Empty
from json import JSONDecodeError
from typing import Optional, List, Any, Callable, Dict, Union, Type

from requests import Session
from requests.exceptions import ConnectTimeout

from . import const
from .models import Event, Telemetry
from .files import Filesystem, InotifyHandler
from .command import Command
from .errors import SDKServerError, SDKConnectionError

__version__ = "0.1.2"
__date__ = "23 Nov 2020"  # version date
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

log = getLogger("connect-printer")
re_conn_reason = re.compile(r"] (.*)")

__all__ = ["Printer", "Notifications"]

CommandArgs = Optional[List[Any]]


class Printer:
    """Printer representation object."""
    queue: "Queue[Union[Event, Telemetry]]"
    server: Optional[str] = None
    token: Optional[str] = None

    def __init__(self,
                 type_: const.PrinterType,
                 sn: str = None,
                 fingerprint: str = None,
                 command_class: Type[Command] = Command):
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

        self.__state = const.State.BUSY
        self.job_id = None

        self.conn = Session()
        self.queue = Queue()

        self.command = command_class(self.event_cb)
        self.set_handler(const.Command.SEND_INFO, self.send_info)
        self.set_handler(const.Command.SEND_FILE_INFO, self.get_file_info)

        self.fs = Filesystem(sep=os.sep, event_cb=self.event_cb)
        self.inotify_handler = InotifyHandler(self.fs)

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
        if tls:
            protocol = 'https'
            port = 443
        else:
            protocol = 'http'
            port = 80
        port = config['connect'].getint('port', fallback=port)
        self.server = f"{protocol}://{host}:{port}"
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
                    network_info=self.network_info,
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
        info.update(node.attrs)
        return info

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
        will set command object.
        """
        if not self.is_initialised():
            msg = "Printer has not been initialized properly"
            log.warning(msg)
            self.event_cb(const.Event.REJECTED, const.Source.WUI, reason=msg)
            return res

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

            content_type = res.headers.get("content-type")
            log.debug("parse_command res: %s", res.text)
            try:
                if content_type == "application/json":
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
            return res.headers['Temporary-Code']

        log.debug("Status code: {res.status_code}")
        raise RuntimeError(res.text)

    # pylint: disable=inconsistent-return-statements
    def get_token(self, tmp_code):
        """If the printer has already been added, return printer token."""
        if not self.server:
            raise RuntimeError("Server is not set")

        headers = self.make_headers()
        headers["Temporary-Code"] = tmp_code
        res = self.conn.get(self.server + "/p/register", headers=headers)
        log.debug("get_token: %s", res.text)
        if res.status_code == 200:
            self.token = res.headers["Token"]
            return self.token
        if res.status_code == 202:
            return  # printer was not created yet by `/app/printers`

        log.debug("Status code: {res.status_code}")
        raise RuntimeError(res.text)

    def loop(self):
        """This method is responsible for communication with Connect.

        In a loop it gets an item (Event or Telemetry) from queue and sets
        Printer.command object, when the command is in the answer to telemetry.
        """
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
                else:
                    log.debug("Sending event: %s", item)
                    res = self.conn.post(self.server + '/p/events',
                                         headers=self.make_headers(
                                             item.timestamp),
                                         json=item.to_payload())
                    log.debug("Event response: %s", res.text)

                if res.status_code >= 400:
                    try:
                        message = res.json()["message"]
                        raise SDKServerError(message)
                    except (JSONDecodeError, KeyError) as err:
                        raise SDKConnectionError("Wrong Connect answer.") \
                            from err
            except Empty:
                continue

            except ConnectTimeout as err:
                raise SDKConnectionError(err) from err

            except ConnectionError as err:
                reason = err.args[0].reason  # pylint: disable=no-member
                reason = re_conn_reason.search(str(reason)).groups()[0]
                raise SDKConnectionError(reason) from err

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

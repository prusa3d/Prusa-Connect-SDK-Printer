"""Python printer library for Prusa Connect."""
from __future__ import annotations          # noqa

import configparser
import os
from logging import getLogger
from time import time, sleep
from queue import Queue
from typing import Optional, List, Any, Callable, Dict

from . import const
from .connection import Connection
from .events import Event
from .command import Command

__version__ = "0.1.0"
__date__ = "13 Aug 2020"        # version date
__copyright__ = "(c) 2020 Prusa 3D"
__author_name__ = "Ondřej Tůma"
__author_email__ = "ondrej.tuma@prusa3d.cz"
__author__ = f"{__author_name__} <{__author_email__}>"
__description__ = "Python printer library for Prusa Connect"

__credits__ = "Ondřej Tůma, Martin Užák, Jan Pilař"
__url__ = "https://github.com/prusa3d/Prusa-Connect-SDK-Printer"

# pylint: disable=invalid-name
# pylint: disable=too-few-public-methods
# pylint: disable=too-many-instance-attributes

log = getLogger("connect-printer")

__all__ = ["Printer", "Telemetry", "Event", "Notifications"]


class Telemetry:
    """Telemetry object must contain Printer state, at a minimum."""
    timestamp: float

    def __init__(self, state: const.State, timestamp: float = None, **kwargs):
        """
        timestamp : float
            If not set int(time.time()*10)/10 is used.
        """
        self.timestamp = timestamp or int(time()*10)*const.TIMESTAMP_PRECISSION
        self.__data = kwargs
        self.__data['state'] = state.value

    def __call__(self, conn: Connection):
        return conn.post("/p/telemetry",
                         conn.make_headers(self.timestamp),
                         self.__data)


CommandArgs = Optional[List[Any]]


class Printer:
    """Printer representation object."""
    events: "Queue[Event]"
    telemetry: "Queue[Telemetry]"

    def __init__(self, type_: const.Printer, sn: str, conn: Connection):
        self.type = type_
        self.sn = sn
        self.ip = None
        self.mac = None
        self.firmware = None

        self.conn = conn
        self.events = Queue()
        self.telemetry = Queue()
        self.run = False

        self.command = Command(self.events)
        self.set_handler(const.Command.SEND_INFO, self.get_info)

    @classmethod
    def from_config(cls, path: str, fingerprint: str,
                    type_: const.Printer, sn: str):
        """Load lan_settings.ini config from `path` and create from it
        and from `fingerprint` a Connection and set it on `self`"""
        if not os.path.exists(path):
            raise FileNotFoundError(f"ini file: `{path}` doesn't exist")
        config = configparser.ConfigParser()
        config.read(path)
        connect_host = config['connect']['address']
        connect_port = config['connect'].getint('port')
        token = config['connect']['token']
        protocol = "http"
        if config['connect'].getboolean('tls'):
            protocol = "https"
        server = f"{protocol}://{connect_host}:{connect_port}"
        conn = Connection(server, fingerprint, token)
        printer = cls(type_, sn, conn)
        return printer

    def get_info(self, args: CommandArgs) -> Dict[str, Any]:
        """Return kwargs for Command.finish method as raction to SEND_INFO."""
        # pylint: disable=unused-argument
        type_, ver, sub = self.type.value
        return dict(source=const.Source.CONNECT, state=const.Event.INFO,
                    type=type_, version=ver, subversion=sub,
                    firmware=self.firmware, ip_address=self.ip,
                    mac=self.mac, sn=self.sn)

    def set_handler(self, command: const.Command,
                    handler: Callable[[CommandArgs], Dict[str, Any]]):
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
        def wrapper(handler: Callable[[CommandArgs], Dict[str, Any]]):
            self.set_handler(command, handler)
            return handler
        return wrapper

    def parse_command(self, res):
        """Parse telemetry response.

        When response from connect is command (HTTP Status: 200 OK), it
        will set command object.
        """
        if res.status_code == 200:
            command_id: Optional[int] = None
            try:
                command_id = int(res.headers.get("Command-Id"))
            except (TypeError, ValueError):
                log.error("Invalid Command-Id header: %s",
                          res.headers.get("Command-Id"))
                event = Event(const.Event.REJECTED, const.Source.CONNECT,
                              reason="Invalid Command-Id header")
                self.events.put(event)
                return res

            content_type = res.headers.get("content-type")
            try:
                if content_type == "application/json":
                    data = res.json()
                    if self.command.check_state(command_id):
                        self.command.accept(
                            command_id, data.get("command", ""),
                            data.get("args"))
                elif content_type == "text/x.gcode":
                    if self.command.check_state(command_id):
                        self.command.accept(
                            command_id, const.Command.GCODE.value, [res.text])
                else:
                    raise ValueError("Invalid command content type")
            except Exception as e:  # pylint: disable=broad-except
                log.exception("")
                event = Event(const.Event.REJECTED, const.Source.CONNECT,
                              command_id=command_id, reason=str(e))
                self.events.put(event)
        return res

    def register(self):
        """Register the printer with Connect and return a registration
        temporary code, or fail with a RuntimeError."""
        data = {
            "mac": self.mac,
            "sn": self.sn,
            "type": self.type.value[0],
            "version": self.type.value[1],
            "firmware": self.firmware
        }
        headers = {
            'Content-Type': 'application/json'
        }
        res = self.conn.post("/p/register", headers=headers, data=data)
        if res.status_code == 200:
            return res.headers['Temporary-Code']

        log.debug("Status code: {res.status_code}")
        raise RuntimeError(res.text)

    # pylint: disable=inconsistent-return-statements
    def get_token(self, tmp_code):
        """If the printer has already been added, return printer token."""
        headers = {
            "Temporary-Code": tmp_code
        }
        res = self.conn.get("/p/register", headers=headers)
        if res.status_code == 200:
            return res.headers["Token"]
        if res.status_code == 202:
            return            # printer was not created yet by `/app/printers`

        log.debug("Status code: {res.status_code}")
        raise RuntimeError(res.text)

    def loop(self):
        """This method is reponsible /to communication with Connect.

        While Printer.run is True, which is set by this method, it fetches
        events and telemetry from the queue. When Connect responsed with
        command, Printer.command will be set.
        """
        self.run = True
        while self.run:
            while not self.events.empty():  # fetch events frist
                event = self.events.get_nowait()
                event(self.conn)
            if not self.telemetry.empty():
                telemetry = self.telemetry.get_nowait()
                res = telemetry(self.conn)
                self.parse_command(res)
            sleep(const.TIMESTAMP_PRECISSION)


def default_notification_handler(code, msg) -> Any:
    """Library notification handler call print."""
    print(f"{code}: {msg}")


class Notifications:
    """Notification class."""
    handler: Callable[[str, str], Any] = default_notification_handler

from __future__ import annotations          # noqa

from time import time
from typing import Optional, List, Any, Callable, Dict
from logging import getLogger

from . import const
from .connection import Connection

__version__ = "0.1.0"
log = getLogger("connect-printer")


class Telemetry:
    """Telemetry object must contain Printer state at minimum."""
    timestamp: int

    def __init__(self, state: const.State, timestamp: int = None, **kwargs):
        """
        timestamp : int
            If not set int(time.time()) is used.
        """
        self.timestamp = timestamp or int(time())    # TODO: int(time()*10)/10
        self.__data = kwargs
        self.__data['state'] = state.value

    def __call__(self, conn: Connection):
        return conn.post("/p/telemetry",
                         conn.make_headers(self.timestamp),
                         self.__data)


class Event:
    """Event object must contain Event type at minimum.

    timestamp : int
        If not set int(time.time()) is used.
    command_id : int
        Must be set for answer to Connect command.
    """
    timestamp: int

    def __init__(self, event: const.Event, source: const.Source,
                 timestamp: int = None, command_id: int = None, **kwargs):
        self.timestamp = timestamp or int(time())    # TODO: int(time()*10)/10
        self.event = event
        self.source = source
        self.command_id = command_id
        self.data = kwargs

    def __call__(self, conn: Connection):
        data = {"event": self.event.value,
                "source": self.source.value,
                "data": self.data}
        if self.command_id:
            data["command_id"] = self.command_id

        return conn.post("/p/events",
                         conn.make_headers(self.timestamp),
                         data)


CommandArgs = Optional[List[Any]]


class Printer:
    command_id: Optional[int] = None
    handlers: Dict[const.Command, Callable[[Printer, CommandArgs], Any]]

    def __init__(self, type_: const.Printer,
                 sn: str, mac: str, firmware: str, ip: str, conn: Connection):
        self.type = type_
        self.sn = sn
        self.mac = mac
        self.firmware = firmware
        self.ip = ip

        self.conn = conn
        self.handlers = {
            const.Command.SEND_INFO: Printer.send_info
        }

    @staticmethod
    def send_info(prn: Printer, args: CommandArgs) -> Any:
        type_, ver, sub = prn.type.value
        Event(
            const.Event.INFO, const.Source.CONNECT, int(time()),
            prn.command_id,
            type=type_, version=ver, subversion=sub,
            firmware=prn.firmware, ip_address=prn.ip,
            mac=prn.mac, sn=prn.sn)(prn.conn)
        prn.command_id = None

    def set_handler(self, command: const.Command,
                    handler: Callable[[Printer, CommandArgs], Any]):
        """Set handler for command."""
        self.handlers[command] = handler

    def handler(self, command: const.Command):
        """Wrap function to handle command.

        .. code:: python

            @printer.command(const.GCODE)
            def gcode(prn, gcode):
                ...
        """
        def wrapper(handler: Callable[[Printer, Optional[List[Any]]], Any]):
            self.set_handler(command, handler)
            return handler
        return wrapper

    def __execute(self, cmd: str, args: Optional[List[Any]] = None):
        log.debug("Try to handle %s command.", cmd)
        handler = None
        try:
            cmd_ = const.Command(cmd)
            handler = self.handlers[cmd_]
        except ValueError:
            log.error("Unknown printer command %s.", cmd)
            Event(const.Event.REJECTED, const.Source.WUI, int(time()),
                  self.command_id, reason="Unknown command")(self.conn)
            self.command_id = None
            return
        except KeyError:
            log.error("Not implemented printer command %s.", cmd)
            Event(const.Event.REJECTED, const.Source.WUI, int(time()),
                  self.command_id, reason="Not Implemented")(self.conn)
            self.command_id = None
            return
        try:
            handler(self, args)
        except Exception as e:
            log.exception("")
            Event(const.Event.REJECTED, const.Source.WUI, int(time()),
                  self.command_id, reason="Command error",
                  error=str(e))(self.conn)
            self.command_id = None
            return

    def event(self, event: Event):
        """Send event to Connect."""
        event(self.conn)

    def telemetry(self, telemetry: Telemetry):
        """Send telemetry to Connect.

        When response from connect is command (HTTP Status: 200 OK), it
        will parse response and call handler from handler table. See
        Printer.set_handler or Printer.handler.
        """
        res = telemetry(self.conn)
        if res.status_code == 200:
            command_id: Optional[int] = None
            try:
                command_id = int(res.headers.get("Command-Id"))
            except (TypeError, ValueError):
                log.error("Invalid Command-Id header: %s",
                          res.headers.get("Command-Id"))
                Event(const.Event.REJECTED, const.Source.CONNECT,
                      int(time()),
                      reason="Invalid Command-Id header")(self.conn)
                return res

            if self.command_id and self.command_id != command_id:
                log.error("Another command is running: %d", self.command_id)
                Event(const.Event.REJECTED, const.Source.CONNECT, int(time()),
                      command_id, reason="Another command is running",
                      actual_command_id=self.command_id)(self.conn)
            else:
                self.command_id = command_id
                content_type = res.headers.get("content-type")
                try:
                    if content_type == "application/json":
                        data = res.json()
                        self.__execute(data.get("command", ""),
                                       data.get("args"))
                    elif content_type == "text/x.gcode":
                        self.__execute(const.Command.GCODE.value,
                                       res.text)
                    else:
                        raise ValueError("Invalid command content type")
                except Exception as e:
                    log.exception("")
                    Event(const.Event.REJECTED, const.Source.CONNECT,
                          int(time()), self.command_id,
                          reason=str(e))(self.conn)
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
        else:
            log.debug("Status code: {res.status_code}")
            raise RuntimeError(res.text)

    def get_token(self, tmp_code):
        """If the printer has already been added, return printer token."""
        headers = {
            "Temporary-Code": tmp_code
        }
        res = self.conn.get("/p/register", headers=headers)
        if res.status_code == 200:
            return res.headers["Token"]
        elif res.status_code == 202:
            return            # printer was not created yet by `/app/printers`
        else:
            log.debug("Status code: {res.status_code}")
            raise RuntimeError(res.text)

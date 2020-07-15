from time import time
from typing import Optional

from . import types
from .connection import Connection

__version__ = "0.1.0"


class Telemetry:
    """Telemetry object must contain Printer state at minimum."""
    timestamp: int

    def __init__(self, state: types.State, timestamp: int = None, **kwargs):
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

    def __init__(self, event: types.Event, source: types.Source,
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


class Printer:
    command_id: Optional[int] = None

    def __init__(self, type_: types.Printer, version: types.Version,
                 sn: str, mac: str, firmware: str, ip: str, conn: Connection):
        self.type = type_
        self.version = version
        self.sn = sn
        self.mac = mac
        self.firmware = firmware
        self.ip = ip

        self.conn = conn
        self.handlers = {
            types.HighLevelCommand.SEND_INFO: self.send_info
        }

    def send_info(prn):
        ver, sub = prn.version.value
        Event(
            types.Event.INFO, types.Source.CONNECT, int(time()),
            prn.command_id,
            type=prn.type.value, version=ver, subversion=sub,
            firmware=prn.firmware, ip_address=prn.ip,
            mac=prn.mac, sn=prn.sn)(prn.conn)
        prn.command_id = None

    def do_command(self, cmd):
        handler = None
        try:
            cmd_ = types.HighLevelCommand(cmd)
            handler = self.handlers[cmd_]
        except ValueError:
            Event(types.Event.REJECTED, types.Source.WUI, int(time()),
                  self.command_id, reason="Unknown command")(self.conn)
            self.command_id = None
            return
        except KeyError:
            Event(types.Event.REJECTED, types.Source.WUI, int(time()),
                  self.command_id, reason="Not Implemented")(self.conn)
            self.command_id = None
            return
        try:
            handler(self)
        except Exception as e:
            Event(types.Event.REJECTED, types.Source.WUI, int(time()),
                  self.command_id, reason="Command error",
                  error=str(e))(self.conn)
            self.command_id = None
            return

    def telemetry(self, telemetry: Telemetry):
        res = telemetry(self.conn)
        if res.status_code == 200:
            command_id: Optional[int] = None
            try:
                command_id = int(res.headers.get("Command-Id"))
            except ValueError:
                pass

            if self.command_id and self.command_id != command_id:
                Event(types.Event.REJECTED, types.Source.CONNECT, int(time()),
                      self.command_id, reason="Another command is running",
                      actual_command_id=self.command_id)(self.conn)
            else:
                self.command_id = command_id
                # TODO: HighLVL vs LowLVL command
                # TODO: args support
                command = res.json().get("command")
                self.do_command(command)
        return res

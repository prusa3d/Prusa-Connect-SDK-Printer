import configparser
import os
from hashlib import sha256
from logging import getLogger
from queue import Queue, Empty
from time import time
from typing import Dict, Any, Callable, Optional, Union, List

from requests import Session

from prusa.connect.printer import const, Command, Filesystem, InotifyHandler, Event, Telemetry

log = getLogger("printer")

CommandArgs = Optional[List[Any]]


class Printer:
    """Printer representation object."""
    queue: "Queue[Union[Event, Telemetry]]"

    def __init__(self,
                 type_: const.PrinterType,
                 sn: str,
                 server: str,
                 token: str = None):
        self.type = type_
        self.__sn = sn
        self.__fingerprint = sha256(sn.encode()).hexdigest()
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
        self.running = False

        self.server = server
        self.token = token
        self.conn = Session()
        self.queue = Queue()

        self.command = Command(self.event_cb)
        self.set_handler(const.Command.SEND_INFO, self.get_info)
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

    @property
    def sn(self):
        """Return printer serial number"""
        return self.__sn

    def make_headers(self, timestamp: float = None) -> dict:
        """Return request headers from connection variables."""
        timestamp = timestamp or int(time() * 10) * const.TIMESTAMP_PRECISION

        headers = {
            "Fingerprint": self.__fingerprint,
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
        self.event_cb(const.Event.STATE_CHANGED, source, **kwargs)

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
        self.queue.put(event_)

    def telemetry(self,
                  state: const.State,
                  timestamp: float = None,
                  **kwargs) -> None:
        """Create telemetry end push it to queue."""
        if self.job_id:
            kwargs['job_id'] = self.job_id
        telemetry = Telemetry(state, timestamp, **kwargs)
        self.queue.put(telemetry)

    @classmethod
    def from_config(cls, path: str, type_: const.PrinterType, sn: str):
        """Load lan_settings.ini config from `path` and create Printer instance
           from it.
        """
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
        printer = cls(type_, sn, server, token)
        return printer

    def get_info(self, args: CommandArgs) -> Dict[str, Any]:
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
                    sn=self.__sn)

    def get_file_info(self, args: CommandArgs) -> Dict[str, Any]:
        """Return file info for a given file, if it exists."""
        # pylint: disable=unused-argument
        if not args:
            raise ValueError("SEND_FILE_INFO requires args")

        path = args[0]
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
                        self.command.accept(command_id,
                                            const.Command.GCODE.value,
                                            [res.text])
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
        data = {
            "sn": self.__sn,
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

        self.running = True
        while self.running:
            try:
                self.inotify_handler()

                item = self.queue.get(timeout=const.TIMESTAMP_PRECISION)
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
            except Empty:
                continue

    def mount(self, dirpath: str, mountpoint: str):
        """Create a listing of `dirpath` and mount it under `mountpoint`.

        This requires linux kernel with inotify support enabled to work.
        """
        self.fs.from_dir(dirpath, mountpoint)
        self.inotify_handler = InotifyHandler(self.fs)

    def umount(self, mountpoint: str):
        """Umount `mountpoint`.

        This requires linux kernel with inotify support enabled to work.
        """
        self.fs.umount(mountpoint)
        self.inotify_handler = InotifyHandler(self.fs)

    def stop(self):
        """Stop the `loop()`, release its thread"""
        self.running = False

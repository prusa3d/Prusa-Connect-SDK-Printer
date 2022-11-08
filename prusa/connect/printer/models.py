"""Connect printer data models."""
from logging import getLogger
from time import time
from typing import Dict, Any, Callable, Optional, TypedDict
from mypy_extensions import Arg, DefaultArg, KwArg
from requests import Session

from . import const
from .camera import Camera
from .util import get_timestamp

# NOTE: Temporary for pylint with python3.9
# pylint: disable=unsubscriptable-object

log = getLogger("connect-printer")

CODE_TIMEOUT = 60 * 30  # 30 min

EventCallback = Callable[[
    Arg(const.Event, 'event'),  # noqa
    Arg(const.Source, 'source'),  # noqa
    DefaultArg(Optional[float], 'timestamp'),  # noqa
    DefaultArg(Optional[int], 'command_id'),  # noqa
    KwArg(Any)
], None]

TelemetryCallback = Callable[[
    Arg(const.State, 'state'),  # noqa
    DefaultArg(Optional[float], 'timestamp'),  # noqa
    KwArg(Any)
], None]


def filter_null(obj):
    """Returns object (dict, list, etc.) without null values recursively.

    >>> filter_null({'one': 1, 'none': None})
    {'one': 1}
    >>> filter_null([1, None])
    [1]
    >>> filter_null({'set': {1, None}, 'dict': {'one': 1, 'none': None}})
    {'set': {1}, 'dict': {'one': 1}}
    """
    if isinstance(obj, dict):
        return dict((key, filter_null(val)) for key, val in obj.items()
                    if val is not None)
    if isinstance(obj, (list, tuple, set)):
        cls = obj.__class__
        return cls(filter_null(val) for val in obj if val is not None)
    return obj


class LoopObject:
    """A common object that can be sent out"""
    endpoint: str
    method: str
    needs_token: bool = True

    timestamp: float

    def __init__(self, timestamp: Optional[float] = None):
        self.timestamp = get_timestamp(timestamp)

    def send(self, conn: Session, server, headers):
        """A universal send function"""
        name = self.__class__.__name__
        log.debug("Sending %s: %s", name, self)
        res = conn.request(method=self.method,
                           url=server + self.endpoint,
                           headers=headers,
                           json=self.to_payload(),
                           timeout=const.CONNECTION_TIMEOUT)

        log.debug("%s response: %s", name, res.text)
        return res

    def to_payload(self):
        """By default, LoopObjects don't send any payload"""
        return None


class Register(LoopObject):
    """A request to Connect to register the printer
    does not need the token, this one is needed to get it"""

    endpoint = "/p/register"
    method = "GET"
    needs_token = False

    def __init__(self, code):
        super().__init__()
        self.code = code
        self.timeout = int(time()) + CODE_TIMEOUT

    def send(self, conn: Session, server, headers):
        """Register needs an extra code in the headers, this adds it"""
        headers["Code"] = self.code
        return super().send(conn, server, headers)


# pylint: disable=too-many-instance-attributes
class Event(LoopObject):
    """Event object must contain at least Event type and source.

    timestamp : float
        If not set int(time.time()*10)/10 is used.
    command_id : int
        Must be set for answer to Connect command.
    **kwargs : dict
        Any other name attributes will be stored in data structure.
    """

    endpoint = "/p/events"
    method = "POST"
    needs_token = True
    data: Dict[str, Any]

    # pylint: disable=too-many-arguments
    def __init__(self,
                 event: const.Event,
                 source: const.Source,
                 timestamp: Optional[float] = None,
                 command_id: Optional[int] = None,
                 job_id: Optional[int] = None,
                 reason: Optional[str] = None,
                 state: Optional[const.State] = None,
                 **kwargs):
        super().__init__(timestamp=timestamp)
        self.event = event
        self.source = source
        self.command_id = command_id
        self.job_id = job_id
        self.reason = reason
        self.state = state
        self.data = kwargs

    def to_payload(self):
        """Send event to connect."""
        data = {
            "event": self.event.value,
            "source": self.source.value,
            "data": filter_null(self.data)
        }
        for attr in ('command_id', 'job_id', 'reason'):
            value = getattr(self, attr)
            if value:
                data[attr] = value
        if self.state:
            data["state"] = self.state.value

        return data

    def __repr__(self):
        data = self.to_payload()
        return (f"<Event::{self.event} at {id(self)}>"
                f" [{self.source}], {data}")


class Telemetry(LoopObject):
    """Telemetry object must contain at least Printer state"""

    endpoint = "/p/telemetry"
    method = "POST"
    needs_token = True

    def __init__(self,
                 state: const.State,
                 timestamp: Optional[float] = None,
                 **kwargs):
        """
        timestamp : float
            If not set int(time.time()*10)/10 is used.
        """
        super().__init__(timestamp=timestamp)
        self.__data = kwargs
        self.__data['state'] = state.value

    def to_payload(self):
        """Returns telemetry payload data"""
        return filter_null(self.__data)

    def __repr__(self):
        return f"<Telemetry:: at {id(self)}> {self.__data}"


class CameraRegister(LoopObject):
    """A request to Connect to register the camera"""
    endpoint = "/p/camera"
    method = "POST"

    def __init__(self, camera: Camera):
        super().__init__()
        self.camera = camera

    def to_payload(self):
        """Converts the camera to data for registration"""
        obj_config = self.camera.get_settings()
        config = self.camera.json_from_settings(obj_config)
        config["camera_id"] = self.camera.camera_id
        available_resolutions = [
            dict(res) for res in self.camera.available_resolutions
        ]
        setting_options = dict(available_resolutions=available_resolutions)
        supported_capabilities = [
            cap.value for cap in self.camera.supported_capabilities
        ]

        data = dict(config=config,
                    setting_options=setting_options,
                    supported_capabilities=supported_capabilities,
                    fingerprint=self.camera.fingerprint)
        return data


class Sheet(TypedDict):
    """A model for type hinting the sheet settings list"""
    name: str
    z_offset: float

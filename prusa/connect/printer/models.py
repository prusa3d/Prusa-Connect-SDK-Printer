"""Connect printer data models."""
from __future__ import annotations
from time import time
from typing import Dict, Any, Callable, Optional
from mypy_extensions import Arg, DefaultArg, KwArg

from . import const

# NOTE: Temporary for pylint with python3.9
# pylint: disable=unsubscriptable-object

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


class Event:
    """Event object must contain at least Event type and source.

    timestamp : float
        If not set int(time.time()*10)/10 is used.
    command_id : int
        Must be set for answer to Connect command.
    **kwargs : dict
        Any other name attributes will be stored in data structure.
    """
    timestamp: float
    data: Dict[str, Any]

    def __init__(self,
                 event: const.Event,
                 source: const.Source,
                 timestamp: float = None,
                 command_id: int = None,
                 **kwargs):
        self.timestamp = timestamp or int(
            time() * 10) * const.TIMESTAMP_PRECISION
        self.event = event
        self.source = source
        self.command_id = command_id
        self.data = kwargs

    def to_payload(self):
        """Send event to connect."""
        data = {
            "event": self.event.value,
            "source": self.source.value,
            "data": filter_null(self.data)
        }
        if self.command_id:
            data["command_id"] = self.command_id

        return data

    def __repr__(self):
        return (f"<Event::{self.event} at {id(self)}>::{self.command_id}"
                f" [{self.source}], {self.data}")


class Telemetry:
    """Telemetry object must contain Printer state, at a minimum."""
    timestamp: float

    def __init__(self, state: const.State, timestamp: float = None, **kwargs):
        """
        timestamp : float
            If not set int(time.time()*10)/10 is used.
        """
        self.timestamp = timestamp or int(
            time() * 10) * const.TIMESTAMP_PRECISION
        self.__data = kwargs
        self.__data['state'] = state.value

    def to_payload(self):
        """Return telemetry payload data"""
        return filter_null(self.__data)

    def __repr__(self):
        return f"<Telemetry:: at {id(self)}> {self.__data}"

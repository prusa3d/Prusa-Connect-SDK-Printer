"""Events workflow and support part."""

from __future__ import annotations
from time import time
from typing import Dict, Any

from . import const
from .connection import Connection
from .util import filter_null

# pylint: disable=too-few-public-methods


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

    def __init__(self, event: const.Event, source: const.Source,
                 timestamp: float = None, command_id: int = None, **kwargs):
        self.timestamp = timestamp or int(time()*10)*const.TIMESTAMP_PRECISSION
        self.event = event
        self.source = source
        self.command_id = command_id
        self.data = kwargs

    def __call__(self, conn: Connection):
        """Send event to connect."""
        data = {"event": self.event.value,
                "source": self.source.value,
                "data": filter_null(self.data)}
        if self.command_id:
            data["command_id"] = self.command_id

        return conn.post("/p/events",
                         conn.make_headers(self.timestamp),
                         data)

    def __repr__(self):
        return (f"<Event::{self.event} at {id(self)}>::{self.command_id}"
                f" [{self.source}], {self.data}")

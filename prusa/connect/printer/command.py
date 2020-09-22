"""Command class representation."""
from queue import Queue
from typing import Optional, List, Any, Dict, Callable
from logging import getLogger

from . import const
from .events import Event

log = getLogger("connect-printer")

CommandArgs = Optional[List[Any]]


class Command:
    """Command singleton/state like structure."""

    state: Optional[const.Event]
    command: Optional[str]
    args: Optional[List[Any]]
    handlers: Dict[const.Command, Callable[[CommandArgs], Dict[str, Any]]]

    def __init__(self, events: Queue):
        self.events = events
        self.state = None
        self.command_id = 0  # 0 mean that there was no command before
        self.command = None
        self.args = []
        self.handlers = {}

    def check_state(self, command_id: int):
        """Check, if Command has right state (None).

        :return:    True, if command can be accepted.

        Otherwise, put right event to queue.
        """

        if self.state is not None:  # here comes another command
            if self.command_id != command_id:
                event = Event(const.Event.REJECTED,
                              const.Source.CONNECT,
                              command_id=command_id,
                              reason="Another command is running",
                              actual_command_id=self.command_id)
                self.events.put(event)
            else:  # resend state of accepted command
                event = Event(self.state,
                              const.Source.CONNECT,
                              command_id=command_id)
                self.events.put(event)
            return False
        return True

    def accept(self,
               command_id: int,
               command: str,
               args: Optional[List[Any]] = None):
        """Accept command (add event to queue)."""
        self.state = const.Event.ACCEPTED
        self.command_id = self.command_id
        self.command = command
        self.args = args
        event = Event(self.state, const.Source.CONNECT, command_id=command_id)
        self.events.put(event)

    def reject(self, source: const.Source, reason: str, **kwargs):
        """Reject command with some reason"""
        event = Event(const.Event.REJECTED,
                      source,
                      command_id=self.command_id,
                      reason=reason,
                      **kwargs)
        self.events.put(event)
        self.state = None
        # don't clean data, which is history in fact

    def finish(self,
               source: const.Source,
               state: const.Event = None,
               **kwargs):
        """Finish command with optional other state and data."""
        state = state or const.Event.FINISHED
        event = Event(state, source, command_id=self.command_id, **kwargs)
        self.events.put(event)
        self.state = None

    def __call__(self):
        """Run handler command handler.

        Handler must return **kwargs dictionary for Command.finish method,
        which means that source must be set at least.
        """
        if self.state is None:
            return None

        log.debug("Try to handle %s command.", self.command)
        handler = None
        try:
            cmd = const.Command(self.command)
            handler = self.handlers[cmd]
        except ValueError:
            log.error("Unknown printer command %s.", self.command)
            return self.reject(const.Source.WUI, reason="Unknown command")
        except KeyError:
            log.error("Printer command %s not implemented.", self.command)
            return self.reject(const.Source.WUI, reason="Not Implemented")
        try:
            kwargs = handler(self.args)
            return self.finish(**kwargs)
        except Exception as err:  # pylint: disable=broad-except
            log.exception("")
            return self.reject(const.Source.WUI,
                               reason="command error",
                               error=str(err))

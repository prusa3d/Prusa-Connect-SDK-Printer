"""Command class representation."""
from multiprocessing import Event
from typing import Optional, List, Any, Dict, Callable
from logging import getLogger

from . import const
from .models import EventCallback

log = getLogger("connect-printer")

CommandArgs = Optional[List[Any]]


class Command:
    """Command singleton/state like structure."""

    state: Optional[const.Event]
    command: Optional[str]
    args: Optional[List[Any]]
    handlers: Dict[const.Command, Callable[["Command", CommandArgs], Dict[str, Any]]]

    def __init__(self, event_cb: EventCallback):
        self.event_cb = event_cb
        self.state = None
        self.last_state = const.Event.REJECTED
        self.command_id = 0  # 0 mean that there was no command before
        self.command = None
        self.args = []
        self.handlers = {}
        self.new_cmd_evt = Event()

    def check_state(self, command_id: int):
        """Check, if Command has right state (None).

        :return:    True, if command can be accepted.

        Otherwise, put right event to queue.
        """
        if self.state is not None:  # here comes another command
            if self.command_id != command_id:
                self.event_cb(const.Event.REJECTED,
                              const.Source.CONNECT,
                              command_id=command_id,
                              reason="Another command is running",
                              actual_command_id=self.command_id)
            else:  # resend state of accepted command
                # self.state can be changed in other thread, and command
                # can be FINISHED or REJECT at this time (theoretical).
                self.event_cb(self.state or self.last_state,
                              const.Source.CONNECT,
                              command_id=command_id)
            return False

        if self.command_id == command_id:  # resend last state of last_command
            self.event_cb(self.last_state,
                          const.Source.CONNECT,
                          command_id=command_id)
            return False
        return True

    def accept(self,
               command_id: int,
               command: str,
               args: Optional[List[Any]] = None):
        """Accept command (add event to queue)."""
        self.state = const.Event.ACCEPTED
        self.command_id = command_id
        self.command = command
        self.args = args
        self.event_cb(self.state, const.Source.CONNECT, command_id=command_id)
        self.new_cmd_evt.set()

    def reject(self, source: const.Source, reason: str, **kwargs):
        """Reject command with some reason"""
        self.last_state = const.Event.REJECTED
        self.event_cb(self.last_state,
                      source,
                      command_id=self.command_id,
                      reason=reason,
                      **kwargs)
        self.teardown()
        # don't clean data, which is history in fact

    def finish(self,
               source: const.Source,
               event: const.Event = None,
               **kwargs):
        """Finish command with optional other event and data."""
        event = event or const.Event.FINISHED
        self.last_state = const.Event.FINISHED
        self.event_cb(event, source, command_id=self.command_id, **kwargs)
        self.teardown()

    def teardown(self):
        self.state = None
        self.new_cmd_evt.clear()

    def __call__(self):
        """Run handler command handler.

        Handler must return **kwargs dictionary for Command.finish method,
        which means that source must be set at least.
        """
        if self.state != const.Event.ACCEPTED:
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
            kwargs = handler(self, self.args)
            return self.finish(**kwargs)
        except Exception as err:  # pylint: disable=broad-except
            log.exception("")
            return self.reject(const.Source.WUI,
                               reason="Command error",
                               error=str(err))

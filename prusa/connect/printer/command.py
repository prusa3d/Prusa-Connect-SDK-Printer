"""Command class representation."""
from multiprocessing import Event
from typing import Optional, List, Any, Dict, Callable
from logging import getLogger

from . import const
from .const import PRIORITY_COMMANDS, ONE_SECOND_TIMEOUT
from .models import EventCallback

log = getLogger("connect-printer")

# pylint: disable=too-many-instance-attributes
# NOTE: Temporary for pylint with python3.9
# pylint: disable=unsubscriptable-object

CommandArgs = Optional[List[Any]]


class CommandFailed(RuntimeError):
    """Exception class for signalling that a command has failed."""


class Command:
    """Command singleton/state like structure."""

    state: Optional[const.Event]
    command_name: Optional[str]
    args: Optional[List[Any]]
    kwargs: Optional[Dict[str, Any]]
    handlers: Dict[const.Command, Callable[["Command"], Dict[str, Any]]]

    def __init__(self, event_cb: EventCallback):
        self.event_cb = event_cb
        self.state = None
        self.last_state = const.Event.REJECTED
        self.command_id = 0  # 0 means that there was no command before
        self.command_name = None
        self.force = False
        self.args = []
        self.kwargs = {}
        self.handlers = {}
        self.new_cmd_evt = Event()
        self.cmd_end_evt = Event()
        self.stop_cb = lambda: None  # Called to stop the current command

    def check_state(self, command_id: int, command_name: str):
        """Check if we're ready for another command

        :return:    True, if command can be accepted.

        Otherwise, put right event to queue.
        """
        try:
            command_enum = const.Command(command_name)
            if command_enum in PRIORITY_COMMANDS:
                self.stop_cb()
                if not self.cmd_end_evt.wait(ONE_SECOND_TIMEOUT):
                    log.warning(
                        "Previous command didn't stop in time, "
                        "ignoring %s", command_name)
                return True
        except Exception:  # pylint: disable=broad-except
            pass

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

    def accept(  # pylint: disable=too-many-arguments
            self,
            command_id: int,
            command_name: str,
            args: Optional[List[Any]] = None,
            kwargs: Optional[Dict[str, Any]] = None,
            force=False):
        """Accept command (add event to queue)."""
        self.state = const.Event.ACCEPTED
        self.command_id = command_id
        self.command_name = command_name
        self.args = args
        self.kwargs = kwargs
        self.force = force
        self.event_cb(self.state, const.Source.CONNECT, command_id=command_id)
        self.new_cmd_evt.set()
        self.cmd_end_evt.clear()

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

    def failed(self,
               source: const.Source,
               reason,
               command_id: Optional[int] = None,
               **kwargs):
        """Notify Connect that a command has failed"""
        self.last_state = const.Event.FAILED
        if command_id is None:
            command_id = self.command_id
        self.event_cb(const.Event.FAILED,
                      source,
                      reason=reason,
                      command_id=command_id,
                      **kwargs)
        self.teardown()

    def finish(self,
               source: const.Source,
               event: Optional[const.Event] = None,
               command_id: Optional[int] = None,
               **kwargs):
        """Finish command with optional other event and data."""
        event = event or const.Event.FINISHED
        self.last_state = const.Event.FINISHED
        if command_id is None:
            command_id = self.command_id
        self.event_cb(event, source, command_id=command_id, **kwargs)
        self.teardown()

    def teardown(self):
        """Clear the last command state and prepare to accept a new one"""
        self.state = None
        self.new_cmd_evt.clear()
        self.cmd_end_evt.set()

    def __call__(self):
        """Run handler command handler.

        Handler must return **kwargs dictionary for Command.finish method,
        which means that source must be set at least.
        """
        if self.state != const.Event.ACCEPTED:
            return None

        log.debug("Try to handle %s command.", self.command_name)
        handler = None
        # Remember the current command id during this specific command's run
        command_id = self.command_id
        try:
            cmd = const.Command(self.command_name)
            handler = self.handlers[cmd]
        except ValueError:
            log.error("Unknown printer command %s", self.command_name)
            return self.reject(const.Source.WUI, reason="Unknown command")
        except KeyError:
            log.error("Printer command %s not implemented", self.command_name)
            return self.reject(const.Source.WUI, reason="Not Implemented")
        try:
            kwargs = handler(self)
            return self.finish(command_id=command_id, **kwargs)
        except Exception as err:  # pylint: disable=broad-except
            log.exception("")
            # Could have accepted another command,
            # ignore the result of this one
            return self.failed(const.Source.WUI,
                               reason="Command error",
                               error=repr(err),
                               command_id=command_id)

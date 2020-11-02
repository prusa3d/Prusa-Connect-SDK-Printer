"""Test Command interface"""
from queue import Queue

import pytest

from prusa.connect.printer import const
from prusa.connect.printer.command import Command
from prusa.connect.printer.models import Event

# pylint: disable=missing-function-docstring
# pylint: disable=redefined-outer-name


@pytest.fixture
def queue():
    return Queue()


@pytest.fixture
def command(queue):
    def create_event(event: const.Event,
                     source: const.Source,
                     timestamp: float = None,
                     command_id: int = None,
                     **kwargs) -> None:
        event_ = Event(event, source, timestamp, command_id, **kwargs)
        queue.put(event_)

    return Command(create_event)


def check_event(queue, event: const.Event):
    """Check right event is in queue."""
    assert not queue.empty()
    event_ = queue.get_nowait()
    assert event_.event == event, event
    return event_


def test_check_state(command, queue):
    # no conflict
    command.check_state(1)
    assert queue.empty()

    # last command_id is same
    command.command_id = 1
    command.last_state = const.Event.REJECTED
    command.check_state(1)
    check_event(queue, const.Event.REJECTED)

    # last command not finished yet
    command.state = const.Event.ACCEPTED
    command.check_state(1)
    check_event(queue, const.Event.ACCEPTED)


def test_accept(command, queue):
    command.accept(2, "TEST", ['x'])
    assert command.state == const.Event.ACCEPTED
    assert command.command_id == 2
    assert command.args == ['x']
    check_event(queue, const.Event.ACCEPTED)


def test_reject(command, queue):
    command.command_id = 3
    command.reject(const.Source.WUI, reason="No way")
    assert command.last_state == const.Event.REJECTED
    assert command.command_id == 3
    assert command.state is None
    check_event(queue, const.Event.REJECTED)


def test_finish(command, queue):
    command.command_id = 4
    command.finish(const.Source.MARLIN, const.Event.STATE_CHANGED)
    assert command.last_state == const.Event.FINISHED
    assert command.state is None
    assert command.command_id == 4
    check_event(queue, const.Event.STATE_CHANGED)


def test_call(command, queue):
    def handler(caller, args):
        assert len(args) == 0, args
        return dict(event=const.Event.INFO, source=const.Source.CONNECT, x='x')

    command.command = "SEND_INFO"
    command.state = const.Event.ACCEPTED
    command.handlers[const.Command.SEND_INFO] = handler
    command()
    event = check_event(queue, const.Event.INFO)
    assert event.data == {'x': 'x'}


def test_call_unknow_command(command, queue):
    command.command = "TEST"
    command.state = const.Event.ACCEPTED
    command()
    event = check_event(queue, const.Event.REJECTED)
    assert event.data['reason'] == "Unknown command"


def test_call_not_implemented(command, queue):
    command.command = "SEND_INFO"
    command.state = const.Event.ACCEPTED
    command()
    event = check_event(queue, const.Event.REJECTED)
    assert event.data['reason'] == "Not Implemented"


def test_call_exception(command, queue):
    def handler(caller, args):
        raise RuntimeError(str(args))

    command.command = "SEND_INFO"
    command.state = const.Event.ACCEPTED
    command.handlers[const.Command.SEND_INFO] = handler
    command()
    event = check_event(queue, const.Event.REJECTED)
    assert event.data['reason'] == "Command error"
    assert 'error' in event.data


def test_unknown(command, queue):
    command.accept(24, "STANDUP", [])
    check_event(queue, const.Event.ACCEPTED)

    command()
    check_event(queue, const.Event.REJECTED)

    command.check_state(24)
    check_event(queue, const.Event.REJECTED)


def test_command_recall(command, queue):
    def handler(caller, args):
        assert len(args) == 0, args
        return dict(event=const.Event.INFO, source=const.Source.CONNECT, x='x')

    command.handlers[const.Command.SEND_INFO] = handler
    command.accept(24, "SEND_INFO", [])
    check_event(queue, const.Event.ACCEPTED)

    command()
    check_event(queue, const.Event.INFO)

    command.check_state(24)
    check_event(queue, const.Event.FINISHED)

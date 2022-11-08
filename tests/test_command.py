"""Test Command interface"""
import threading
from queue import Queue

import pytest

from prusa.connect.printer import const
from prusa.connect.printer.command import Command
from prusa.connect.printer.models import Event
from typing import Optional

# pylint: disable=missing-function-docstring
# pylint: disable=redefined-outer-name


@pytest.fixture
def queue():
    return Queue()


@pytest.fixture
def command(queue):
    def create_event(event: const.Event,
                     source: const.Source,
                     timestamp: Optional[float] = None,
                     command_id: Optional[int] = None,
                     **kwargs) -> None:
        event_ = Event(event, source, timestamp, command_id, **kwargs)
        queue.put(event_)

    return Command(create_event)


def check_event(queue, event: const.Event, command_id=None, timeout=None):
    """Check right event is in queue."""
    if timeout is None:
        assert not queue.empty()
    event_ = queue.get(timeout=timeout)
    assert event_.event == event, event
    if command_id is not None:
        assert event_.command_id == command_id
    return event_


def test_check_state(command, queue):
    # no conflict
    command.check_state(1, const.Command.SEND_INFO.value)
    assert queue.empty()

    # last command_id is same
    command.command_id = 1
    command.last_state = const.Event.REJECTED
    command.check_state(1, const.Command.SEND_INFO.value)
    check_event(queue, const.Event.REJECTED)

    # last command not finished yet
    command.state = const.Event.ACCEPTED
    command.check_state(1, const.Command.SEND_INFO.value)
    check_event(queue, const.Event.ACCEPTED)


def test_accept(command, queue):
    command.accept(2, "TEST", ['x'], {"param": 'x'}, True)
    assert command.state == const.Event.ACCEPTED
    assert command.command_id == 2
    assert command.args == ['x']
    assert command.kwargs == {"param": 'x'}
    assert command.force
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
    def handler(caller):
        assert len(caller.args) == 0, caller.args
        return dict(event=const.Event.INFO, source=const.Source.CONNECT, x='x')

    command.command_name = "SEND_INFO"
    command.state = const.Event.ACCEPTED
    command.handlers[const.Command.SEND_INFO] = handler
    command()
    event = check_event(queue, const.Event.INFO)
    assert event.data == {'x': 'x'}


def test_call_unknown_command(command, queue):
    command.command_name = "TEST"
    command.state = const.Event.ACCEPTED
    command()
    event = check_event(queue, const.Event.REJECTED)
    assert event.reason == "Unknown command"


def test_call_not_implemented(command, queue):
    command.command_name = "SEND_INFO"
    command.state = const.Event.ACCEPTED
    command()
    event = check_event(queue, const.Event.REJECTED)
    assert event.reason == "Not Implemented"


def test_call_exception(command, queue):
    def handler(caller):
        raise RuntimeError(str(caller.args))

    command.command_name = "SEND_INFO"
    command.state = const.Event.ACCEPTED
    command.handlers[const.Command.SEND_INFO] = handler
    command()
    event = check_event(queue, const.Event.FAILED)
    assert event.reason == "Command error"
    assert 'error' in event.data


def test_unknown(command, queue):
    command.accept(24, "STANDUP", [])
    check_event(queue, const.Event.ACCEPTED)

    command()
    check_event(queue, const.Event.REJECTED)

    command.check_state(24, "STANDUP")
    check_event(queue, const.Event.REJECTED)


def test_command_recall(command, queue):
    def handler(caller):
        assert len(caller.args) == 0, caller.args
        return dict(event=const.Event.INFO, source=const.Source.CONNECT, x='x')

    command.handlers[const.Command.SEND_INFO] = handler
    command.accept(24, "SEND_INFO", [])
    check_event(queue, const.Event.ACCEPTED)

    command()
    check_event(queue, const.Event.INFO)

    command.check_state(24, const.Command.SEND_INFO.value)
    check_event(queue, const.Event.FINISHED)


def test_priority_command(command, queue):
    event = threading.Event()

    def handler(_):
        event.wait()
        raise RuntimeError("Failed!")

    def stop_cb():
        ti = threading.Timer(0.1, event.set)
        ti.start()

    command.stop_cb = stop_cb
    command.handlers[const.Command.SEND_INFO] = handler
    command.accept(24, "SEND_INFO", [])
    check_event(queue, const.Event.ACCEPTED)
    t = threading.Thread(target=command)
    t.start()
    assert command.check_state(25, const.Command.RESET_PRINTER.value)
    event.wait()
    check_event(queue, const.Event.FAILED, command_id=24, timeout=0.1)

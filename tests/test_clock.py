from func_timeout import FunctionTimedOut, func_timeout  # type: ignore

from prusa.connect.printer import const
from prusa.connect.printer.clock import ClockWatcher

from .util import printer

assert printer  # type: ignore


def adjust_clock(clock_watcher):
    """Helper to mock adjusting the clock in `clock_watcher`"""
    clock_watcher.delta += (ClockWatcher.TOLERANCE + 1)


def test_clock_adjusted():
    adj_watcher = ClockWatcher()
    assert not adj_watcher.clock_adjusted()

    adjust_clock(adj_watcher)
    assert adj_watcher.clock_adjusted()

    adj_watcher.reset()
    assert not adj_watcher.clock_adjusted()


def _test_loop(printer):
    orig_delta = printer.clock_watcher.delta
    adjust_clock(printer.clock_watcher)

    try:
        func_timeout(0.1, printer.loop)
    except FunctionTimedOut:
        pass

    assert abs(printer.clock_watcher.delta - orig_delta) < 0.01


def test_loop_telemetry(printer):
    printer.telemetry(const.State.IDLE)
    _test_loop(printer)


def test_loop_event(printer):
    printer.event_cb(const.Event.STATE_CHANGED, const.Source.WUI, data="data")
    _test_loop(printer)

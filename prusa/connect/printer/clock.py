"""This module checks for system clock adjustments"""

import time

# pylint: disable=too-many-instance-attributes


class ClockWatcher:
    """Check if the clock has been adjusted by comparing the
    system time to HW clock.

    It assumes that the system clock has been adjusted if
    ``start_time - hw_clock_time`` is different from current values outside the
    `ClockWatcher.TOLERANCE`.
    """

    TOLERANCE = 1  # seconds

    def __init__(self):
        self.delta = 0
        self.reset()

    def reset(self):
        """Reset the measured delta"""
        self.delta = time.time() - time.monotonic()

    def clock_adjusted(self):
        """Check if the clock has been adjusted on the system"""
        return abs(self.delta - self.current_delta()) >= self.TOLERANCE

    @staticmethod
    def current_delta():
        """Return the difference between the current time from EPOCH and
        HW clock. Both values are in seconds."""
        return time.time() - time.monotonic()

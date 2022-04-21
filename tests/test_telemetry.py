"""Tests for telemetry functionality"""
from prusa.connect.printer import Telemetry, const

# pylint: disable=missing-function-docstring


def test_telemetry():
    telemetry = Telemetry(const.State.IDLE)
    assert telemetry.timestamp > 1
    payload = telemetry.to_payload()
    assert payload['state'] == 'IDLE'

    telemetry = Telemetry(const.State.IDLE, 24)
    assert telemetry.timestamp == 24

    telemetry = Telemetry(const.State.BUSY, axis_x=3.1, fan=None)
    payload = telemetry.to_payload()
    assert payload == {'state': 'BUSY', 'axis_x': 3.1}

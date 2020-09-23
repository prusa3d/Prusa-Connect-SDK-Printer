"""Test for events functionality"""
from prusa.connect.printer import Event, const

# pylint: disable=missing-function-docstring


def test_event():
    event = Event(const.Event.STATE_CHANGED, const.Source.WUI)
    assert event.timestamp > 1
    payload = event.to_payload()
    assert payload['event'] == 'STATE_CHANGED'
    assert payload['source'] == 'WUI'

    event = Event(const.Event.STATE_CHANGED,
                  const.Source.WUI,
                  timestamp=24,
                  command_id=42,
                  data="data",
                  null=None)
    assert event.timestamp == 24
    payload = event.to_payload()
    assert payload['command_id'] == 42
    assert payload['data'] == {'data': 'data'}

"""Test for events functionality"""
from prusa.connect.printer import Event, const

# pylint: disable=missing-function-docstring


def test_event():
    event = Event(const.Event.STATE_CHANGED, const.Source.WUI)
    assert event.timestamp > 1
    payload = event.to_payload()
    assert payload['event'] == 'STATE_CHANGED'
    assert payload['source'] == 'WUI'

    event = Event(const.Event.STATE_CHANGED, const.Source.WUI, timestamp=24)
    assert event.timestamp == 24


def test_data():
    event = Event(const.Event.STATE_CHANGED,
                  const.Source.WUI,
                  data="data",
                  null=None)
    payload = event.to_payload()
    assert payload['data'] == {'data': 'data'}


def test_kwargs():
    event = Event(const.Event.STATE_CHANGED,
                  const.Source.WUI,
                  command_id=42,
                  job_id=12,
                  reason="Chuck Norris",
                  state=const.State.FINISHED)
    payload = event.to_payload()
    assert payload['command_id'] == 42
    assert payload['job_id'] == 12
    assert payload['reason'] == "Chuck Norris"
    assert payload['state'] == "FINISHED"

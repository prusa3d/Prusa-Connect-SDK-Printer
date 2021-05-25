"""Test for error state functionality"""
from prusa.connect.printer.errors import TOKEN, API, INTERNET, HTTP

# pylint: disable=missing-function-docstring


def test_error_states():
    # initial checks
    assert not INTERNET.ok
    assert not HTTP.ok
    assert not TOKEN.ok
    assert not API.ok

    assert INTERNET.prev is None
    assert API.next is None

    assert INTERNET.next is HTTP
    assert HTTP.next is TOKEN
    assert TOKEN.next is API
    assert API.prev is TOKEN
    assert TOKEN.prev is HTTP
    assert HTTP.prev is INTERNET

    # state propagation: backward
    HTTP.ok = True
    assert INTERNET.ok is True
    assert HTTP.ok is True
    assert not TOKEN.ok
    assert not API.ok

    # forward
    API.ok = True
    HTTP.ok = False
    assert HTTP.ok is False
    assert TOKEN.ok is False
    assert API.ok is False  # previous state failed so this must fail too
    assert INTERNET.ok is True  # internet might be OK when HTTP fails


def test_errors_iterating():
    API.ok = True
    for error in INTERNET:
        assert error.ok is True

    INTERNET.ok = False
    for error in INTERNET:
        assert error.ok is False

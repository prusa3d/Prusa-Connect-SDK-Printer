"""Test for error state functionality"""
from prusa.connect.printer.errors import ErrorState

# pylint: disable=missing-function-docstring


def test_error_states():
    internet = ErrorState(
        "internet", "DNS works and other hosts in the "
        "internet can be reached")
    http = ErrorState("http",
                      "HTTP traffic to Connect is OK, no 5XX statuses",
                      prev=internet)
    connect = ErrorState("connect", "There are no 4XX problems while "
                         "communicating to Connect",
                         prev=http)

    # initial checks
    assert not internet.ok
    assert not http.ok
    assert not connect.ok

    assert internet.prev is None
    assert connect.next is None

    assert internet.next is http
    assert http.next is connect
    assert connect.prev is http
    assert http.prev is internet

    # state propagation: backward
    http.ok = True
    assert internet.ok is True
    assert http.ok is True
    assert not connect.ok

    # forward
    connect.ok = True
    http.ok = False
    assert http.ok is False
    assert connect.ok is False  # previous state failed so this must fail too
    assert internet.ok is True  # internet might be OK when HTTP fails

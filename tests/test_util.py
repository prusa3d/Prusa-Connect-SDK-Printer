"""Tests for the util module"""
from unittest.mock import Mock

import pytest
import requests

from prusa.connect.printer.util import (
    RetryingSession,
    get_timestamp,
    make_fingerprint,
)


def test_get_timestamp():
    """A given timestamp is kept, none means the current time"""
    assert get_timestamp(42.0) == 42.0
    assert get_timestamp() > 0


def test_make_fingerprint():
    """The fingerprint is a sha256 hex digest"""
    assert make_fingerprint("a") == (
        "ca978112ca1bbdcafac231b39a23dc4da786eff8147c4e72b9807785afee48bb")


def test_call_and_retry_succeeds_after_error():
    """A connection error is retried"""
    callback = Mock(side_effect=[requests.exceptions.ConnectionError(), "ok"])
    assert RetryingSession().call_and_retry(callback, 1, a=2) == "ok"
    callback.assert_called_with(1, a=2)


def test_call_and_retry_gives_up():
    """After max_retries the last error is raised"""
    callback = Mock(side_effect=requests.exceptions.ConnectionError())
    with pytest.raises(requests.exceptions.ConnectionError):
        RetryingSession(max_retries=2).call_and_retry(callback)
    assert callback.call_count == 2


def test_get_and_post(requests_mock):
    """get and post go through the retrying wrapper"""
    requests_mock.get("http://x/", text="g")
    requests_mock.post("http://x/", text="p")
    session = RetryingSession()
    assert session.get("http://x/").text == "g"
    assert session.post("http://x/", json={"a": 1}).text == "p"

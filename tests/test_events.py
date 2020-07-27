import pytest   # type: ignore
import requests # noqa

from prusa.connect.printer import Event, const
from prusa.connect.printer.connection import Connection

FINGERPRINT = "__fingerprint__"
SERVER = "http://server"


@pytest.fixture()
def connection():
    return Connection(SERVER, FINGERPRINT)


def test_event(requests_mock, connection):
    requests_mock.post(SERVER+"/p/events", status_code=204)

    Event(const.Event.STATE_CHANGED, const.Source.WUI)(connection)
    Event(const.Event.STATE_CHANGED, const.Source.WUI, data="data")(connection)

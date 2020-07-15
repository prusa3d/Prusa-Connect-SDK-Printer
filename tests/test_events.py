import pytest   # type: ignore
import requests # noqa

from prusa.connect.printer import Event, types
from prusa.connect.printer.connection import Connection

FINGERPRINT = "__fingerprint__"
SERVER = "http://server"


@pytest.fixture()
def connection():
    return Connection(SERVER, FINGERPRINT)


def test_event(requests_mock, connection):
    requests_mock.post(SERVER+"/p/events", status_code=204)

    Event(types.Event.STATE_CHANGED, types.Source.WUI)(connection)
    Event(types.Event.STATE_CHANGED, types.Source.WUI, data="data")(connection)

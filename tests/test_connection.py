import pytest  # type: ignore
import requests  # noqa

from prusa.connect.printer.connection import Connection

assert pytest

SERVER = "http://server"
FINGERPRINT = "__fingerprint__"
TOKEN = "__token__"


def test_connection_anonym(requests_mock):
    requests_mock.post(SERVER + "/p/test", status_code=204)
    requests_mock.get(SERVER + "/p/test")

    conn = Connection(SERVER, FINGERPRINT)
    headers = conn.make_headers(123)
    assert headers == {"Fingerprint": FINGERPRINT, "Timestamp": "123"}

    conn.post("/p/test", headers, {'key': 'val'})
    conn.get("/p/test", headers)


def test_make_headers(requests_mock):
    requests_mock.post(SERVER + "/p/test", status_code=204)
    requests_mock.get(SERVER + "/p/test")

    conn = Connection(SERVER, FINGERPRINT, TOKEN)
    headers = conn.make_headers(123)
    assert headers == {
        "Fingerprint": FINGERPRINT,
        "Token": TOKEN,
        "Timestamp": "123"
    }
    conn.post("/p/test", headers, {'key': 'val'})
    conn.get("/p/test", headers)

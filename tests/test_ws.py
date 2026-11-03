"""Tests for the WebSocket transport (PrinterWS) without real sockets"""
import json
from typing import Any, ClassVar, List
from unittest.mock import Mock

import pytest

from prusa.connect.printer import ws as ws_module
from prusa.connect.printer.ws import PrinterWS

URL = "ws://connect.example/p/ws"


class FakeApp:
    """Replacement of websocket.WebSocketApp"""
    instances: ClassVar[List[Any]] = []
    opens = True  # the handshake succeeds

    def __init__(self, url, **callbacks):
        self.url = url
        self.callbacks = callbacks
        self.sock = None
        self.sent = []
        self.closed = False
        self.run_kwargs = None
        FakeApp.instances.append(self)

    def run_forever(self, **kwargs):
        """Pretend to be the connection thread"""
        self.run_kwargs = kwargs
        if FakeApp.opens:
            self.sock = Mock(connected=True)
            self.callbacks["on_open"](self)

    def send(self, data):
        """Remember what was sent"""
        self.sent.append(data)

    def close(self):
        """Remember the close"""
        self.closed = True


@pytest.fixture(name="app_cls")
def fixture_app_cls(monkeypatch):
    """Patch WebSocketApp, the connection opens as soon as it runs"""
    FakeApp.instances = []
    FakeApp.opens = True
    monkeypatch.setattr(ws_module.websocket, "WebSocketApp", FakeApp)
    return FakeApp


def test_not_connected():
    """Nothing is sent without a connection"""
    sock = PrinterWS(Mock())
    assert not sock.connected
    assert not sock.send({"a": 1})
    sock.disconnect()  # harmless


def test_connect_and_send(app_cls):
    """Headers are passed to the handshake and payload is sent as JSON"""
    sock = PrinterWS(Mock())
    sock.connect(URL, {"Token": "t", "Skipped": None, "Fingerprint": "f"})
    app = app_cls.instances[0]
    assert app.url == URL
    assert app.callbacks["header"] == ["Token: t", "Fingerprint: f"]
    assert app.run_kwargs == {"ping_interval": 30, "ping_timeout": 10}
    assert sock.connected
    assert sock.send({"a": 1})
    assert [json.loads(i) for i in app.sent] == [{"a": 1}]

    app.sock.connected = False
    assert not sock.connected
    assert not sock.send({"a": 2})
    assert len(app.sent) == 1


def test_connected_without_socket(app_cls):
    """The app without the socket (not opened yet) is not connected"""
    sock = PrinterWS(Mock())
    sock.connect(URL, {})
    app_cls.instances[0].sock = None
    assert not sock.connected


def test_reconnect_closes_previous(app_cls):
    """Connecting again closes the old connection first"""
    sock = PrinterWS(Mock())
    sock.connect(URL, {})
    sock.connect(URL, {})
    first, second = app_cls.instances
    assert first.closed
    assert not second.closed
    sock.disconnect()
    assert second.closed
    assert not sock.connected


def test_send_failure(app_cls):
    """An exception while sending is reported as failure"""
    sock = PrinterWS(Mock())
    sock.connect(URL, {})
    app_cls.instances[0].send = Mock(side_effect=OSError("broken"))
    assert not sock.send({"a": 1})


def test_connect_timeout(app_cls, monkeypatch):
    """connect() does not block forever when the handshake never ends"""
    monkeypatch.setattr(ws_module.const, "CONNECTION_TIMEOUT", 0.01)
    app_cls.opens = False
    sock = PrinterWS(Mock())
    sock.connect(URL, {})
    assert not sock.connected


def test_callbacks(app_cls):
    """Incoming messages are forwarded, errors unblock connect()"""
    on_message = Mock()
    sock = PrinterWS(on_message)
    sock.connect(URL, {})
    callbacks = app_cls.instances[0].callbacks
    callbacks["on_message"](None, "Jmsg")
    on_message.assert_called_once_with("Jmsg")
    callbacks["on_close"](None, 1000, "bye")

    sock._open_event.clear()  # pylint: disable=protected-access
    callbacks["on_error"](None, OSError("x"))
    assert sock._open_event.is_set()  # pylint: disable=protected-access

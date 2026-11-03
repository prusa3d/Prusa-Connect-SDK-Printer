"""WebSocket transport for Connect SDK — telemetry and events."""
import json
from logging import getLogger
from threading import Event, Lock, Thread
from typing import Callable, Optional

import websocket  # type: ignore

from . import const

log = getLogger("connect-printer")


class PrinterWS:
    """Persistent WebSocket connection to Connect.

    Runs WebSocketApp.run_forever() in a background daemon thread so that
    Printer.loop (synchronous/threaded) can call send() without blocking.
    Incoming frames are forwarded to the on_message callback supplied at
    construction (Printer.handle_ws_message).
    """

    def __init__(self, on_message: Callable[[str], None]):
        self._on_message = on_message
        self._ws: Optional[websocket.WebSocketApp] = None
        self._thread: Optional[Thread] = None
        self._open_event = Event()
        self._send_lock = Lock()

    @property
    def connected(self) -> bool:
        """True when the WebSocket handshake completed and socket is open."""
        return (self._ws is not None and self._ws.sock is not None
                and self._ws.sock.connected)

    def connect(self, url: str, headers: dict) -> None:
        """(Re)connect to *url* using *headers* for the WS handshake.

        Blocks until on_open fires or CONNECTION_TIMEOUT elapses.
        Safe to call when already connected — disconnects first.
        """
        self.disconnect()
        self._open_event.clear()

        header_list = [
            f"{key}: {value}" for key, value in headers.items()
            if value is not None
        ]

        self._ws = websocket.WebSocketApp(
            url,
            header=header_list,
            on_open=self._handle_open,
            on_message=self._handle_message,
            on_error=self._handle_error,
            on_close=self._handle_close,
        )

        self._thread = Thread(
            target=self._ws.run_forever,
            kwargs={
                "ping_interval": 30,
                "ping_timeout": 10,
            },
            daemon=True,
        )
        self._thread.start()

        if not self._open_event.wait(timeout=const.CONNECTION_TIMEOUT):
            log.warning("WS connect timed out: %s", url)

    def disconnect(self) -> None:
        """Close the WebSocket and wait for the background thread to stop."""
        if self._ws is not None:
            self._ws.close()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=const.CONNECTION_TIMEOUT)
        self._ws = None
        self._thread = None

    def send(self, payload: dict) -> bool:
        """Serialize *payload* to JSON and send as a text frame.

        Returns True on success, False when not connected or on any error.
        Thread-safe (one send at a time via lock).
        """
        if not self.connected:
            log.warning("WS not connected, dropping: %s", payload)
            return False
        try:
            with self._send_lock:
                self._ws.send(json.dumps(payload))  # type: ignore[union-attr]
            return True
        except Exception:  # pylint: disable=broad-except
            log.exception("WS send failed")
            return False

    # --- internal WebSocketApp callbacks ---

    def _handle_open(self, _ws) -> None:
        log.debug("WS connection opened")
        self._open_event.set()

    def _handle_message(self, _ws, message: str) -> None:
        self._on_message(message)

    def _handle_error(self, _ws, error) -> None:
        log.error("WS error: %s", error)
        self._open_event.set()  # unblock connect() if still waiting

    def _handle_close(self, _ws, close_code, close_msg) -> None:
        log.debug("WS closed: %s %s", close_code, close_msg)

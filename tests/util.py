import json
from typing import Callable, List

from func_timeout import FunctionTimedOut, func_timeout  # type: ignore

FINGERPRINT = "__fingerprint__"
SN = "SN001002XP003"
CONNECT_HOST = "server"
CONNECT_PORT = 8000
SERVER = f"http://{CONNECT_HOST}:{CONNECT_PORT}"
TOKEN = "a44b552a12d96d3155cb"


def run_loop(fct, timeout=0.1):
    try:
        func_timeout(timeout, fct)
    except FunctionTimedOut:
        pass


def ws_json_command(command_id: int, data: dict) -> str:
    """Format a high-level JSON command in the BuddyEncoder wire format."""
    return f"J{command_id:08x}{json.dumps(data)}"


def ws_gcode_command(command_id: int, gcode: str) -> str:
    """Format a low-level GCode command in the BuddyEncoder wire format."""
    return f"G{command_id:08x}{gcode}"


def ws_force_gcode_command(command_id: int, gcode: str) -> str:
    """Format a forced GCode command (F prefix) in BuddyEncoder wire format."""
    return f"F{command_id:08x}{gcode}"


class FakeWS:
    """Drop-in test double for PrinterWS — no real sockets, no threads."""

    def __init__(self, on_message: Callable[[str], None]):
        self._on_message = on_message
        self.sent: List[dict] = []
        self.connect_calls: List[tuple] = []
        self._connected = False
        self.fail_send = False  # set True to simulate send failure
        self.fail_connect = False  # set True to simulate connection failure

    @property
    def connected(self) -> bool:
        return self._connected

    def connect(self, url: str, headers: dict) -> None:
        self.connect_calls.append((url, headers))
        if not self.fail_connect:
            self._connected = True

    def disconnect(self) -> None:
        self._connected = False

    def send(self, payload: dict) -> bool:
        if self.fail_send:
            return False
        self.sent.append(payload)
        return True

    def simulate_message(self, raw: str) -> None:
        """Simulate server pushing a raw WS text frame."""
        self._on_message(raw)

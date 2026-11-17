"""Inline file transfer over the WebSocket, and WS connection upkeep"""
import os
import shutil
import tempfile
import threading

import pytest

from prusa.connect.printer import const, download, errors
from prusa.connect.printer.models import Event, Telemetry
from tests.util import ws_json_command

# pylint: disable=missing-function-docstring

CEILING = 1011  # Connect local's default chunk frame size
DST = "/sdcard/inline.gcode"


@pytest.fixture(name="mgr")
def fixture_mgr(printer, monkeypatch):
    """Download manager with a storage and short timeouts"""
    tmp_dir = tempfile.mkdtemp()
    printer.fs.from_dir(tmp_dir, "sdcard")
    monkeypatch.setattr(download, "CONNECTION_TIMEOUT", 0.2)
    monkeypatch.setattr(download.time, "sleep", lambda _: None)
    yield printer.download_mgr
    shutil.rmtree(tmp_dir)


def serve(mgr, content, mangle=None):
    """Answer block requests like Connect local: the range in T frames of
    at most CEILING bytes, addressed to the request's file_id"""
    requests = []

    def send(request):
        requests.append(request)
        frames = []
        start, end = request["start"], request["end"]
        for pos in range(start, end + 1, CEILING):
            chunk = content[pos:min(pos + CEILING, end + 1)]
            frames.append(b"T%08x" % request["file_id"] + chunk)
        if mangle:
            frames = mangle(len(requests), frames)
        for frame in frames:
            mgr.feed_inline(int(frame[1:9], 16), frame[9:])
        return True

    mgr.inline_send_cb = send
    return requests


def run_download(mgr):
    """Run one pass of the download loop"""
    mgr._running_loop = True  # pylint: disable=protected-access
    thread = threading.Timer(0, mgr.loop)
    stopper = threading.Timer(1, mgr.stop_loop)
    thread.start()
    stopper.start()
    thread.join(5)
    stopper.cancel()
    mgr.stop_loop()


def events(printer):
    out = []
    while not printer.queue.empty():
        item = printer.queue.get_nowait()
        if isinstance(item, Event):
            out.append(item.to_payload())
    return out


def start(printer, size):
    printer.command.check_state(1, "START_INLINE_DOWNLOAD")
    printer.ws.simulate_message(
        ws_json_command(
            1, {
                "command": "START_INLINE_DOWNLOAD",
                "kwargs": {
                    "path": DST,
                    "hash": "abc",
                    "team_id": 1,
                    "orig_size": size,
                },
            }))
    printer.command()


def landed(mgr):
    with open(mgr.to_os_path(DST), "rb") as file:
        return file.read()


def test_inline_download(printer, mgr):
    content = os.urandom(250_000)
    mgr.buffer_size = 100_000
    requests = serve(mgr, content)
    start(printer, len(content))

    info = [e for e in events(printer) if e["event"] == "TRANSFER_INFO"]
    # Connect local reads the id the TRANSFER_* events will carry from here
    assert info and info[0]["command_id"] == 1
    assert info[0]["transfer_id"] == printer.transfer.transfer_id

    run_download(mgr)

    assert landed(mgr) == content
    assert [(r["start"], r["end"]) for r in requests] == \
        [(0, 99_999), (100_000, 199_999), (200_000, 249_999)]
    assert all(r["hash"] == "abc" and r["team_id"] == 1 for r in requests)
    assert "TRANSFER_FINISHED" in [e["event"] for e in events(printer)]


def test_inline_retry_after_lost_and_stale_frames(printer, mgr):
    content = os.urandom(5000)

    def mangle(attempt, frames):
        if attempt == 1:
            return frames[:-1]  # last frame lost, block times out
        if attempt == 2:
            # A late frame of an abandoned request comes first
            return [b"T%08x" % 0xdead + b"junk"] + frames
        return frames

    requests = serve(mgr, content, mangle)
    start(printer, len(content))
    run_download(mgr)

    assert landed(mgr) == content
    assert len(requests) == 2
    assert requests[0]["file_id"] != requests[1]["file_id"]


def test_inline_empty_frame_retries(printer, mgr):
    content = os.urandom(3000)

    def mangle(attempt, frames):
        return [b"T%08x" % int(frames[0][1:9], 16)] if attempt == 1 \
            else frames

    serve(mgr, content, mangle)
    start(printer, len(content))
    run_download(mgr)
    assert landed(mgr) == content


def test_inline_stall_aborts(printer, mgr, monkeypatch):
    monkeypatch.setattr(download, "INLINE_STALL_TIMEOUT", 0)
    mgr.inline_send_cb = lambda request: False  # never connected
    start(printer, 100)
    run_download(mgr)

    names = [e["event"] for e in events(printer)]
    assert "TRANSFER_ABORTED" in names
    assert not os.path.exists(mgr.to_os_path(DST))


def test_inline_requires_kwargs(printer):
    printer.command.check_state(2, "START_INLINE_DOWNLOAD")
    printer.ws.simulate_message(
        ws_json_command(2, {
            "command": "START_INLINE_DOWNLOAD",
            "kwargs": {"path": DST},
        }))
    printer.command()
    assert "FAILED" in [e["event"] for e in events(printer)] or \
        "REJECTED" in [e["event"] for e in events(printer)]


def test_binary_frame_routing(printer):
    fed = []
    printer.download_mgr.feed_inline = lambda *args: fed.append(args)
    printer.handle_ws_message(b"T0000002a" + b"\x00\xff")
    printer.handle_ws_message(b"Tzzzzzzzz" + b"x")  # bad id, ignored
    printer.handle_ws_message(b"\xff" * 12)  # undecodable, ignored
    assert fed == [(42, b"\x00\xff")]


def test_feed_drops_other_file_ids(mgr):
    mgr._inline_file_id = 7  # pylint: disable=protected-access
    mgr.feed_inline(8, b"late")
    mgr.feed_inline(7, b"ok")
    assert mgr._inline_frames.get_nowait() == b"ok"  # noqa: SLF001
    assert mgr._inline_frames.empty()  # noqa: SLF001


def test_reconnect_backs_off(printer):
    printer.ws.fail_connect = True
    printer.ensure_ws_connected()
    printer.ensure_ws_connected()  # inside the backoff window
    assert len(printer.ws.connect_calls) == 1


def test_refused_token_is_not_retried(printer):
    errors.TOKEN.ok = True
    printer.ws.fail_connect = True
    printer.ws.last_status = 401
    printer.ensure_ws_connected()
    assert not errors.TOKEN.ok
    # Backoff over, still the same token: no new attempt
    printer._Printer__ws_retry_at = 0  # pylint: disable=protected-access
    printer.ensure_ws_connected()
    assert len(printer.ws.connect_calls) == 1
    # A new token is tried right away
    printer.ws.fail_connect = False
    printer.token = "new-token"
    printer.ensure_ws_connected()
    assert len(printer.ws.connect_calls) == 2
    assert printer.ws.connected


def test_offline_keeps_events_drops_telemetry(printer, monkeypatch):
    monkeypatch.setattr("prusa.connect.printer.sleep", lambda _: None)
    printer.ws.fail_connect = True
    event = Event(const.Event.FINISHED, const.Source.CONNECT, command_id=5)
    printer.queue.put(event)
    printer.loop_step()
    assert printer.queue.get_nowait() is event

    printer.queue.put(Telemetry(const.State.IDLE))
    printer.loop_step()
    assert printer.queue.empty()

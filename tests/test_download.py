import os
import time

import pytest
import responses

from prusa.connect.printer import const
from .test_printer import printer
from func_timeout import func_timeout, FunctionTimedOut  # type: ignore

assert printer

# pylint: disable=missing-function-docstring
# pylint: disable=redefined-outer-name

GCODE_URL = "http://prusaprinters.org/my_example.gcode"


@responses.activate
@pytest.fixture
def gcode(printer):
    responses.add(responses.GET,
                  GCODE_URL,
                  body=os.urandom(1024 * 1024),
                  status=200,
                  content_type="application/octet-stream",
                  stream=True)


@pytest.fixture
def download_mgr(printer):
    yield printer.download_mgr
    if printer.download_mgr.current:
        if os.path.exists(printer.download_mgr.current.filename):
            os.remove(printer.download_mgr.current.filename)


def run_loop(download_mgr, timeout=0.5):
    try:
        func_timeout(timeout, download_mgr.loop)
    except FunctionTimedOut:
        pass


def test_download_ok(download_mgr, gcode):
    assert download_mgr.current is None

    dl = download_mgr.start(GCODE_URL)
    run_loop(download_mgr)

    assert dl.progress >= 0
    assert dl.filename == "./my_example.gcode"
    assert dl.to_print is False
    assert dl.to_select is False
    if dl.start_ts is not None:
        assert dl.start_ts <= time.time()
    assert dl.downloaded >= 0


def test_download_to_print(gcode, download_mgr):
    dl = download_mgr.start(GCODE_URL, to_print=True)
    assert dl.to_print is True


def test_download_to_select(gcode, download_mgr):
    dl = download_mgr.start(GCODE_URL, to_select=True)
    assert dl.to_select is True


def test_download_time_remaining(gcode, download_mgr):
    dl = download_mgr.start(GCODE_URL)
    dl._test_loops = 1
    dl.BufferSize = 1
    run_loop(download_mgr)

    assert dl.time_remaining() > 0


def test_download_stop(gcode, download_mgr):
    dl = download_mgr.start(GCODE_URL)
    dl._test_loops = 1
    dl.BufferSize = 1
    dl.stop()

    assert dl.end_ts is None
    assert dl.stop_ts is not None
    assert dl.time_remaining() is None


def test_download_info(gcode, download_mgr):
    dl = download_mgr.start(GCODE_URL, to_select=True)
    dl._test_loops = 1
    dl.BufferSize = 1
    run_loop(download_mgr)

    info = dl.to_dict()
    assert info['filename'] == "./my_example.gcode"
    assert info['downloaded'] >= 0
    assert info['start'] <= time.time()
    assert info['progress'] >= 0
    assert info['to_print'] is False
    assert info['to_select'] is True
    assert info['stopped'] is None
    assert info['end'] is None
    assert info['time_remaining'] > 0
    assert info['total'] >= 0


@responses.activate
def test_download_from_connect_server_has_token(printer):
    url = printer.server + "/path/here"
    responses.add(responses.GET, url, status=200)
    dl = printer.download_mgr.start(url, to_select=True)
    run_loop(printer.download_mgr)
    assert dl.token


@responses.activate
def test_download_no_token(printer):
    url = "http://somewhere.else/path"
    responses.add(responses.GET, url, status=200)
    dl = printer.download_mgr.start(url, to_select=True)
    run_loop(printer.download_mgr)
    assert not dl.token


def test_telemetry_sends_download_info(printer, gcode, download_mgr):
    dl = download_mgr.start(GCODE_URL, to_print=True)
    dl._test_loops = 1
    dl.BufferSize = 1
    run_loop(printer.download_mgr, timeout=.5)

    printer.telemetry(const.State.READY)
    item = printer.queue.get_nowait()

    telemetry = item.to_payload()
    assert "download_progress" in telemetry
    assert "download_time_remaining" in telemetry

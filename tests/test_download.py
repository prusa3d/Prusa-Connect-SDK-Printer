import os
import time
import queue
import threading
import tempfile
import shutil

import pytest
import responses

from prusa.connect.printer import const
from prusa.connect.printer import Telemetry
from .test_printer import printer

assert printer

# pylint: disable=missing-function-docstring
# pylint: disable=redefined-outer-name

GCODE_URL = "http://prusaprinters.org/my_example.gcode"
DST = '/sdcard/my_example.gcode'


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
    tmp_dir = tempfile.TemporaryDirectory()
    printer.fs.from_dir(tmp_dir.name, 'sdcard')
    yield printer.download_mgr

    shutil.rmtree(tmp_dir.name)


def run_test_loop(download_mgr, timeout=.1, unset_stop=False):
    def fullstop():
        download_mgr.stop()
        if unset_stop:
            download_mgr.current.stop_ts = None
        download_mgr._running_loop = False

    t = threading.Timer(timeout, fullstop)
    t.start()

    download_mgr.loop()


def storage_path(fs, filename, mount='sdcard'):
    return os.path.join(fs.mounts[mount].path_storage, filename)


def test_download_ok(download_mgr, gcode):
    assert download_mgr.current is None

    dl = download_mgr.start(GCODE_URL, DST)
    run_test_loop(download_mgr)

    assert dl.progress >= 0
    assert dl.destination == storage_path(download_mgr.fs, 'my_example.gcode')
    assert dl.to_print is False
    assert dl.to_select is False
    if dl.start_ts is not None:
        assert dl.start_ts <= time.time()
    assert dl.downloaded >= 0
    assert not dl.throttle


def test_download_to_print(gcode, download_mgr):
    dl = download_mgr.start(GCODE_URL, DST, to_print=True)
    assert dl.to_print is True
    assert dl.to_select is False


def test_download_to_select(gcode, download_mgr):
    dl = download_mgr.start(GCODE_URL, DST, to_select=True)
    assert dl.to_select is True
    assert dl.to_print is False


def test_download_time_remaining(gcode, download_mgr):
    dl = download_mgr.start(GCODE_URL, DST)
    dl.BUFFER_SIZE = 1
    run_test_loop(download_mgr)
    dl.stop_ts = None  # let's prettend we did not stop

    assert dl.time_remaining() > 0


def test_download_stop(gcode, download_mgr):
    dl = download_mgr.start(GCODE_URL, DST)
    dl.BUFFER_SIZE = 1
    run_test_loop(download_mgr)

    assert dl.end_ts is None
    assert dl.stop_ts is not None
    assert dl.time_remaining() == 0


def test_download_info(gcode, download_mgr):
    dl = download_mgr.start(GCODE_URL, DST, to_select=True)
    dl.BUFFER_SIZE = 1
    run_test_loop(download_mgr)

    info = dl.to_dict()
    assert info['destination'] == storage_path(download_mgr.fs,
                                               'my_example.gcode')
    assert info['downloaded'] >= 0
    assert info['start'] <= time.time()
    assert info['progress'] >= 0
    assert info['to_print'] is False
    assert info['to_select'] is True
    assert info['time_remaining'] >= 0
    assert info['size'] >= 0
    assert info['url'] == GCODE_URL


@responses.activate
def test_download_from_connect_server_has_token(printer, download_mgr):
    url = printer.server + "/path/here"
    responses.add(responses.GET, url, status=200)
    dl = download_mgr.start(url, DST, to_select=True)
    run_test_loop(download_mgr)
    assert 'Token' in dl.headers


@responses.activate
def test_download_no_token(download_mgr):
    url = "http://somewhere.else/path"
    responses.add(responses.GET, url, status=200)
    dl = download_mgr.start(url, DST, to_select=True)
    run_test_loop(download_mgr)
    assert 'Token' not in dl.headers


def test_telemetry_sends_download_info(printer, gcode, download_mgr):
    dl = download_mgr.start(GCODE_URL, DST, to_print=True)
    dl.BUFFER_SIZE = 1

    loop = threading.Thread(target=run_test_loop,
                            daemon=True,
                            args=(download_mgr, ),
                            kwargs={'timeout': 2})
    loop.start()

    start = time.time()
    while (start + 3) >= time.time():
        dl = printer.download_mgr.current
        if dl and dl.progress:
            printer.telemetry(const.State.READY)
            item = printer.queue.get_nowait()
            while not isinstance(item, Telemetry):
                item = printer.queue.get_nowait()

            telemetry = item.to_payload()
            assert "download_progress" in telemetry
            assert "download_time_remaining" in telemetry

            download_mgr._running_loop = False
            break
    else:
        assert 0, "test failed, `break` was not reached"


def test_printed_file_cb(download_mgr, printer):
    """Download will be aborted if currently printed file is the same"""
    printer.queue.get_nowait()  # MEDIUM_INSERTED from mounting `tmp`
    download_mgr.printed_file_cb = lambda: \
        os.path.abspath(download_mgr.current.destination)
    download_mgr.start(GCODE_URL, DST)
    run_test_loop(download_mgr, unset_stop=True)

    # first event is DOWNLOAD_STOPPED because download_mgr fixture calls stop
    item = printer.queue.get_nowait()
    assert item.event == const.Event.DOWNLOAD_STOPPED
    assert item.source == const.Source.CONNECT

    item = printer.queue.get_nowait()
    assert item.event == const.Event.DOWNLOAD_ABORTED
    assert item.source == const.Source.CONNECT


def test_download_twice_in_a_row(gcode, download_mgr, printer):
    printer.queue.get_nowait()  # MEDIUM_INSERTED from mounting `tmp`
    download_mgr.start(GCODE_URL, DST, to_print=True)
    run_test_loop(download_mgr, timeout=1)

    download_mgr.start(GCODE_URL, DST, to_print=True)
    run_test_loop(download_mgr, timeout=1)

    with pytest.raises(queue.Empty):  # no DOWNLOAD_ABORTED events
        while True:
            item = printer.queue.get_nowait()
            assert not item.event == const.Event.DOWNLOAD_ABORTED


def test_download_throttle(download_mgr, gcode):
    dl = download_mgr.start(GCODE_URL, DST)

    dl.throttle = 1
    start = time.time()
    run_test_loop(download_mgr, timeout=1)

    assert time.time() - 1 >= start  # at least one sec has passed


def test_destination_not_abs(download_mgr):
    with pytest.raises(ValueError):
        download_mgr.start(GCODE_URL, 'output.gcode')


def test_download_mgr_os_path(download_mgr):
    assert download_mgr.os_path('/sdcard/one') == storage_path(
        download_mgr.fs, 'one')

    with pytest.raises(ValueError):
        download_mgr.os_path('/sdcard/../foo/one')


def test_download_finished_cb(download_mgr, printer):
    res = {}

    def download_finished_cb(download, res=res):
        res['destination'] = download.destination

    download_mgr.download_finished_cb = download_finished_cb

    download_mgr.start(GCODE_URL, DST, to_select=True)
    run_test_loop(download_mgr, timeout=2)

    assert res

    printer.queue.get_nowait()  # MEDIUM_INSERTED from mounting
    item = printer.queue.get_nowait()
    assert item.event == const.Event.DOWNLOAD_FINISHED

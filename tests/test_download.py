"""Unit tests for Download manager and transfer info."""
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
from .util import printer

assert printer  # type: ignore

# pylint: disable=missing-function-docstring
# pylint: disable=redefined-outer-name

GCODE_URL = "https://media.prusaprinters.org/media/prints/27216/gcodes/" + \
            "272161_a9977cd4-cc70-4fb3-8d09-276a023b132d/" + \
            "cam_clip_3_015mm_pla_mk3s_2h13m.gcode"
DST = '/sdcard/my_example.gcode'


@responses.activate
@pytest.fixture
def gcode(printer):
    # pylint: disable=unused-argument
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
        download_mgr.transfer.stop()
        if unset_stop:
            download_mgr.transfer.stop_ts = 0
        download_mgr._running_loop = False

    t = threading.Timer(timeout, fullstop)
    t.start()

    download_mgr.loop()


def storage_path(fs, filename, storage='sdcard'):
    return os.path.join(fs.storage_dict[storage].path_storage, filename)


def test_download_ok(download_mgr, gcode):
    assert download_mgr.transfer.type == \
           const.TransferType.NO_TRANSFER
    transfer = download_mgr.transfer
    download_mgr.start(const.TransferType.FROM_WEB,
                       DST,
                       GCODE_URL,
                       to_print=False,
                       to_select=False)
    run_test_loop(download_mgr)
    assert transfer.progress >= 0
    assert type(transfer.progress) is float
    assert download_mgr.transfer.os_path == \
           storage_path(download_mgr.fs, 'my_example.gcode')
    assert transfer.to_print is False
    assert transfer.to_select is False
    if transfer.start_ts is not None:
        assert transfer.start_ts <= time.time()
    assert transfer.transferred >= 0
    assert not download_mgr.throttle


def test_optional_download(gcode, download_mgr):
    download_mgr.start(const.TransferType.FROM_WEB, DST)

    assert download_mgr.transfer.path
    assert download_mgr.transfer.in_progress


def test_download_to_print(gcode, download_mgr):
    download_mgr.start(const.TransferType.FROM_WEB,
                       DST,
                       GCODE_URL,
                       to_print=True,
                       to_select=False)
    assert download_mgr.transfer.to_print is True
    assert download_mgr.transfer.to_select is False


def test_download_to_select(gcode, download_mgr):
    download_mgr.start(const.TransferType.FROM_WEB,
                       DST,
                       GCODE_URL,
                       to_print=False,
                       to_select=True)
    assert download_mgr.transfer.to_select is True
    assert download_mgr.transfer.to_print is False


def test_download_stop(gcode, download_mgr):
    download_mgr.start(const.TransferType.FROM_WEB,
                       DST,
                       GCODE_URL,
                       to_print=False,
                       to_select=False)
    transfer = download_mgr.transfer
    download_mgr.buffer_size = 1
    run_test_loop(download_mgr)

    assert transfer.time_remaining() == 0


def test_download_info(gcode, download_mgr):
    info = download_mgr.start(const.TransferType.FROM_WEB,
                              DST,
                              GCODE_URL,
                              to_print=False,
                              to_select=True)

    assert download_mgr.transfer.os_path == storage_path(
        download_mgr.fs, 'my_example.gcode')
    assert info['transferred'] >= 0
    assert info['start'] is None
    assert info['progress'] >= 0
    assert info['progress'] == 0.0
    assert info['to_print'] is False
    assert info['to_select'] is True
    assert info['time_remaining'] is None
    assert info['size'] is None
    assert info['url'] == GCODE_URL

    download_mgr.buffer_size = 1
    run_test_loop(download_mgr)

    info = download_mgr.transfer.to_dict()
    assert download_mgr.transfer.os_path == storage_path(
        download_mgr.fs, 'my_example.gcode')
    assert info['type'] == 'NO_TRANSFER'


@responses.activate
def test_download_from_connect_server_has_token(printer, download_mgr):
    url = printer.server + "/path/here"
    responses.add(responses.GET, url, status=200)
    download_mgr.start(const.TransferType.FROM_CONNECT,
                       DST,
                       url,
                       to_print=False,
                       to_select=True)
    run_test_loop(download_mgr)
    assert 'Token' in download_mgr.headers


@responses.activate
def test_download_no_token(download_mgr):
    url = "http://somewhere.else/path"
    responses.add(responses.GET, url, status=200)
    download_mgr.start(const.TransferType.FROM_WEB,
                       DST,
                       url,
                       to_print=False,
                       to_select=True)
    run_test_loop(download_mgr)
    assert 'Token' not in download_mgr.headers


def test_telemetry_sends_download_info(printer, gcode, download_mgr):
    download_mgr.start(const.TransferType.FROM_WEB,
                       DST,
                       GCODE_URL,
                       to_print=True,
                       to_select=False)
    download_mgr.buffer_size = 1

    loop = threading.Thread(target=run_test_loop,
                            daemon=True,
                            args=(download_mgr, ),
                            kwargs={'timeout': 2})
    loop.start()

    start = time.time()
    while (start + 3) >= time.time():
        dl = printer.download_mgr.transfer
        if dl.type != const.TransferType.NO_TRANSFER and dl.progress:
            printer.telemetry(const.State.IDLE)
            item = printer.queue.get_nowait()
            while not isinstance(item, Telemetry):
                item = printer.queue.get_nowait()

            telemetry = item.to_payload()
            assert "transfer_progress" in telemetry
            assert "transfer_time_remaining" in telemetry
            assert "transfer_transferred" in telemetry

            download_mgr._running_loop = False
            break
    else:
        assert 0, "test failed, `break` was not reached"


def test_printed_file_cb(download_mgr, printer):
    """Transfer will be aborted if currently printed file is the same"""
    printer.queue.get_nowait()  # MEDIUM_INSERTED from attaching `tmp`
    download_mgr.printed_file_cb = lambda: \
        os.path.abspath(download_mgr.os_path)
    download_mgr.start(const.TransferType.FROM_WEB,
                       DST,
                       GCODE_URL,
                       to_print=False,
                       to_select=False)
    run_test_loop(download_mgr, unset_stop=True)

    item = printer.queue.get_nowait()  # Download response
    assert item.event == const.Event.TRANSFER_INFO
    assert item.source == const.Source.WUI
    assert item.data['transfer_id'] == download_mgr.transfer.transfer_id

    item = printer.queue.get_nowait()
    assert item.event == const.Event.TRANSFER_ABORTED
    assert item.source == const.Source.CONNECT
    assert item.data['transfer_id'] == download_mgr.transfer.transfer_id


def test_download_twice_in_a_row(gcode, download_mgr, printer):
    printer.queue.get_nowait()  # MEDIUM_INSERTED from attaching `tmp`
    download_mgr.start(const.TransferType.FROM_WEB,
                       DST,
                       GCODE_URL,
                       to_print=True,
                       to_select=False)
    run_test_loop(download_mgr, timeout=1)

    download_mgr.start(const.TransferType.FROM_WEB,
                       DST,
                       GCODE_URL,
                       to_print=True,
                       to_select=False)
    run_test_loop(download_mgr, timeout=1)

    with pytest.raises(queue.Empty):  # no TRANSFER_ABORTED events
        while True:
            item = printer.queue.get_nowait()
            assert not item.event == const.Event.TRANSFER_ABORTED


def test_download_throttle(download_mgr, gcode):
    download_mgr.start(const.TransferType.FROM_WEB,
                       DST,
                       GCODE_URL,
                       to_print=False,
                       to_select=False)

    download_mgr.throttle = 1
    start = time.time()
    run_test_loop(download_mgr, timeout=1)

    assert time.time() - 1 >= start  # at least one sec has passed


def test_destination_not_abs(download_mgr):
    with pytest.raises(ValueError):
        download_mgr.start(const.TransferType.FROM_WEB,
                           GCODE_URL,
                           'output.gcode',
                           to_print=False,
                           to_select=False)


def test_download_mgr_os_path(download_mgr):
    assert download_mgr.to_os_path('/sdcard/one') == storage_path(
        download_mgr.fs, 'one')

    with pytest.raises(ValueError):
        download_mgr.to_os_path('/sdcard/../foo/one')


def test_download_finished_cb(download_mgr, printer):
    res = {}

    def download_finished_cb(transfer):
        res['path'] = transfer.path

    download_mgr.download_finished_cb = download_finished_cb

    download_mgr.start(const.TransferType.FROM_WEB,
                       DST,
                       GCODE_URL,
                       to_print=False,
                       to_select=True)
    run_test_loop(download_mgr, timeout=2)

    assert res

    printer.queue.get_nowait()  # MEDIUM_INSERTED from attaching

    item = printer.queue.get_nowait()  # Download response
    assert item.event == const.Event.TRANSFER_INFO
    assert item.source == const.Source.WUI

    item = printer.queue.get_nowait()
    assert item.event in (const.Event.TRANSFER_FINISHED,
                          const.Event.TRANSFER_STOPPED)
    assert item.data['transfer_id'] == download_mgr.transfer.transfer_id

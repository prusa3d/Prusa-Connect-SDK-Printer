import os
import time

import pytest
import responses

from prusa.connect.printer.download import Download
from .test_printer import printer

assert printer

# pylint: disable=missing-function-docstring
# pylint: disable=redefined-outer-name

GCODE_URL = "http://prusaprinters.org/my_example.gcode"


class DownloadMock:
    def __init__(self, counter):
        self.counter = counter

    def __call__(self):
        self.counter -= 1
        return self.counter == 0

    @staticmethod
    def patch(loops, buffer_size=1024):
        Download._stop_requested = DownloadMock(loops)
        Download.BufferSize = buffer_size


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
        os.remove(printer.download_mgr.current.filename)


def test_download_ok_3_iterations(download_mgr, gcode):
    assert download_mgr.current is None

    DownloadMock.patch(3, buffer_size=2)
    dl = download_mgr.start(GCODE_URL)

    assert dl.progress > 0
    assert dl.filename == "./my_example.gcode"
    assert dl.to_print is False
    assert dl.to_select is False
    assert dl.start_ts <= time.time()
    assert dl.end_ts is None
    assert dl.downloaded > 0


def test_download_to_print(gcode, download_mgr):
    dl = download_mgr.start(GCODE_URL, to_print=True)
    assert dl.to_print is True


def test_download_to_select(gcode, download_mgr):
    dl = download_mgr.start(GCODE_URL, to_select=True)

    assert dl.to_select is True


def test_download_time_remaining(gcode, download_mgr):
    DownloadMock.patch(3, buffer_size=1)
    dl = download_mgr.start(GCODE_URL)

    assert dl.time_remaining() > 0


def test_download_stop(gcode, download_mgr):
    DownloadMock.patch(3, buffer_size=1)

    dl = download_mgr.start(GCODE_URL)
    dl.stop()

    assert dl.end_ts is None
    assert dl.stop_ts is not None
    assert dl.time_remaining() is None


def test_download_info(gcode, download_mgr):
    DownloadMock.patch(3, buffer_size=16)
    dl = download_mgr.start(GCODE_URL, to_select=True)
    info = dl.to_dict()

    assert info['filename'] == "./my_example.gcode"
    assert info['downloaded'] > 0
    assert info['start'] <= time.time()
    assert info['progress'] > 0
    assert info['to_print'] is False
    assert info['to_select'] is True
    assert info['stopped'] is None
    assert info['end'] is None
    assert info['time_remaining'] > 0
    assert info['total'] > 0


def test_info_contains_download(printer, download_mgr, gcode):
    DownloadMock.patch(3, buffer_size=16)
    dl = download_mgr.start(GCODE_URL, to_select=True)
    download_mgr.current = dl

    info = printer.get_info()
    assert info['download']['current']
    assert info['download']['download_dir']


@responses.activate
def test_download_from_connect_server_has_token(printer):
    url = printer.server + "/path/here"
    responses.add(responses.GET, url, status=200)
    dl = printer.download_mgr.start(url, to_select=True)
    assert dl.token


@responses.activate
def test_download_no_token(printer):
    url = "http://somewhere.else/path"
    responses.add(responses.GET, url, status=200)
    dl = printer.download_mgr.start(url, to_select=True)
    assert not dl.token

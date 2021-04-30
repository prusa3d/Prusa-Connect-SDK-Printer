import pytest
import responses
import os
from prusa.connect.printer.download import Download

# pylint: disable=missing-function-docstring
# pylint: disable=redefined-outer-name

GCODE_URL = "http://prusaprinters.org/my_example.gcode"


class Loop:
    def __init__(self, counter):
        self.counter = counter

    def __call__(self):
        self.counter -= 1
        return self.counter == 0


@responses.activate
@pytest.fixture
def gcode():
    responses.add(
        responses.GET,
        GCODE_URL,
        body=os.urandom(1024 * 1024),
        headers={'Content-length': str(1024 * 1024)},
        status=200,
        content_type="application/octet-stream",
        stream=True,
    )


def patch_download_loop(count):
    Download._stop_requested = Loop(count)


def test_download_ok(printer, gcode):
    patch_download_loop(3)
    printer.download_mgr.start(GCODE_URL)

    dl = printer.download_mgr.current
    assert dl

    if printer.download_mgr.current:
        os.remove(printer.download_mgr.current.filename)


def test_download_invalid_url():
    pass


def test_download_to_print():
    pass


def test_download_to_select():
    pass


def test_download_stop():
    pass

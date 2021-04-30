"""Download functionality infrastructure."""
from logging import getLogger
from .const import DOWNLOAD_DIR
from urllib.parse import urlparse

import requests
import time
import os

log = getLogger("connect-printer")

# pylint: disable=too-many-instance-attributes
# NOTE: Temporary for pylint with python3.9
# pylint: disable=unsubscriptable-object

buffer_size = 1024


class DownloadRunningError(Exception):
    pass


# XXX send token
# XXX allow from prusa printers only
# XXX send info


class DownloadMgr:
    """Download manager."""

    Dir = DOWNLOAD_DIR

    def __init__(self):
        self.current = None

    def start(self, url, filename=None, to_print=False, to_select=False):
        if self.current:
            raise DownloadRunningError()

        # XXX set filename from url if None
        if filename is None:
            parsed = urlparse(url)
            filename = os.path.basename(parsed.path)
        filename = os.path.join(self.Dir, filename)
        # XXX allow writing to other directories as well?

        dl = self.current = Download(url,
                                     filename=filename,
                                     to_print=to_print,
                                     to_select=to_select)
        dl()

    def stop(self):
        # XXX do clenaup as well?
        self.current.stop()

    def info(self):
        pass


class Download:
    def __init__(self, url, filename=None, to_print=False, to_select=False):
        self.url = url
        self.filename = filename
        self.to_print = to_print
        self.to_select = to_select
        self.start_ts = None
        self.stop_ts = None
        self.end_ts = None
        self.done = 0  # percentage, values: 0 to 1

        # XXX compute time remaining ??

    def stop(self):
        self.stop_ts = time.time()

    def __call__(self):
        self.start_ts = time.time()
        with open(self.filename, 'wb') as f:
            response = requests.get(self.url, stream=True)
            total = response.headers.get('content-length')

            if total is None:
                f.write(response.content)
            else:
                downloaded = 0
                total = int(total)
                for data in response.iter_content(chunk_size=buffer_size):
                    if self._stop_requested():
                        return
                    downloaded += len(data)
                    f.write(data)
                    self.done = downloaded / total
                    print("XXX", self.done)
        self.ends_ts = time.time()

    def _stop_requested(self):
        return self.stop_ts is not None

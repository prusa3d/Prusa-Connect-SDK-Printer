"""Download functionality infrastructure."""
from logging import getLogger
from .const import DOWNLOAD_DIR
from urllib.parse import urlparse
from os.path import normpath, abspath, basename, dirname

import requests
import time
import os

log = getLogger("connect-printer")

# pylint: disable=too-many-instance-attributes
# NOTE: Temporary for pylint with python3.9
# pylint: disable=unsubscriptable-object


class DownloadRunningError(Exception):
    pass


# XXX allow from prusa printers only?


class DownloadMgr:
    """Download manager."""

    Dir = DOWNLOAD_DIR

    def __init__(self, printer):
        self.current = None
        # XXX ugly: DownloadMgr knows about printer and vice versa
        self.printer = printer

    def start(self, url, filename=None, to_print=False, to_select=False):
        if self.current:
            raise DownloadRunningError()

        # take filename from `url` if not set
        if filename is None:
            parsed = urlparse(url)
            filename = basename(parsed.path)
        filename = os.path.join(self.Dir, filename)

        # guard
        abs_path = abspath(normpath(filename))
        abs_dl_dir = abspath(normpath(self.Dir))
        if not abs_path.startswith(abs_dl_dir):
            raise ValueError(f"{filename} is outside of download dir")

        # make dir (in case filename contains a subdir)
        try:
            dir = dirname(filename)
            os.makedirs(dir)
        except FileExistsError:
            log.debug(f"{dir} already exists")

        try:
            token = None
            if self.printer.server and self.printer.token and \
                    url.lower().startswith(self.printer.server.lower()):
                token = self.printer.token
            dl = self.current = Download(url,
                                         filename=filename,
                                         to_print=to_print,
                                         to_select=to_select,
                                         token=token)
            dl()
            return dl
        finally:
            self.current = None

    def stop(self):
        self.current.stop()

    def info(self):
        return {
            'current': self.current and self.current.to_dict(),
            'download_dir': self.Dir,
        }


class Download:
    BufferSize = 1024

    def __init__(self,
                 url,
                 filename=None,
                 to_print=False,
                 to_select=False,
                 token=None):
        self.url = url
        self.filename = filename
        self.to_print = to_print
        self.to_select = to_select
        self.start_ts = None
        self.stop_ts = None
        self.end_ts = None
        self.progress = 0  # percentage, values: 0 to 1
        self.total = 0
        self.downloaded = 0
        self.token = token

    def time_remaining(self):
        """Return the estimated time remaining for the download in seconds.
        Returns None if not computation is not possible.
        """

        # finished or aborted
        if self.end_ts is not None or self.stop_ts is not None:
            return None

        if self.start_ts is not None:
            elapsed = time.time() - self.start_ts
            return self.total / self.downloaded * elapsed

    def stop(self):
        self.stop_ts = time.time()

    def __call__(self):
        self.start_ts = time.time()
        headers = {}
        if self.token:
            headers['Token'] = self.token
        response = requests.get(self.url, stream=True, headers=headers)
        self.total = response.headers.get('Content-Length')

        with open(self.filename, 'wb') as f:
            if self.total is None:
                f.write(response.content)
            else:
                self.downloaded = 0
                self.total = int(self.total)
                for data in response.iter_content(chunk_size=self.BufferSize):
                    if self._stop_requested():
                        return
                    self.downloaded += len(data)
                    f.write(data)
                    self.progress = self.downloaded / self.total
        self.end_ts = time.time()

    def _stop_requested(self):
        return self.stop_ts is not None

    def to_dict(self):
        return {
            "filename": self.filename,
            "total": self.total,
            "downloaded": self.downloaded,
            "progress": self.progress,
            "time_remaining": self.time_remaining(),
            "start": self.start_ts,
            "end": self.end_ts,
            "stopped": self.stop_ts,
            "to_select": self.to_select,
            "to_print": self.to_print,
        }

"""Download functionality for SDK."""
import os
import time

from logging import getLogger
from os.path import normpath, abspath, basename, dirname

import requests

from . import const

log = getLogger("connect-printer")

# pylint: disable=too-many-instance-attributes
# NOTE: Temporary for pylint with python3.9
# pylint: disable=unsubscriptable-object


class DownloadRunningError(Exception):
    """Exception thrown when a download is already in progress"""


class DownloadMgr:
    """Download manager."""

    LOOP_INTERVAL = .1

    def __init__(self, fs, conn_details_cb, event_cb, printed_file_cb):
        # pylint: disable=invalid-name
        self.fs = fs
        self.conn_details_cb = conn_details_cb
        self.event_cb = event_cb
        self.printed_file_cb = printed_file_cb
        self._running_loop = False
        self.current = None
        self.download_finished_cb = lambda download: None

    def start(self, url, destination, to_print=False, to_select=False):
        """Start a download of `url` saving it into the `destination`.
        This `destination` is the absolute virtual path in `self.fs`
        (:class:prusa.connect.printer.files.Filesystem)
        """
        if self.current:
            self.event_cb(const.Event.REJECTED,
                          const.Source.CONNECT,
                          reason="Another download in progress")
            return None

        log.info("Starting download: %s", url)

        # transform destination to OS path and validate
        os_dst = self.os_path(destination)

        # make dir (in case filename contains a subdir)
        try:
            dir_ = dirname(os_dst)
            os.makedirs(dir_)
        except FileExistsError:
            log.debug("%s already exists", dir_)

        server, token = self.conn_details_cb()
        # server is not connect server, set token to None

        headers = {}
        if server and token and url.lower().startswith(server.lower()):
            headers['Token'] = token

        download = self.current = Download(url,
                                           os_dst,
                                           to_print=to_print,
                                           to_select=to_select,
                                           headers=headers)
        return download

    def os_path(self, destination):
        """Translate virtual `destination` of self.fs to real OS path."""
        if not os.path.isabs(destination):
            raise ValueError('Destination must be absolute')

        try:
            _, mount_name, rest = destination.split(self.fs.sep, 2)
            mount = self.fs.mounts[mount_name]
            path_storage = mount.path_storage.rstrip(self.fs.sep)
            os_dst = self.fs.sep.join([path_storage, rest])
            os_dst = normpath(os_dst)
            if not os_dst.startswith(path_storage):
                msg = "Destination is outside of defined path_storage for " \
                      "mount_point: %s"
                raise ValueError(msg % mount_name)
            return os_dst
        except KeyError as err:
            raise ValueError("Invalid mount point: `%s` in `%s`" %
                             (mount_name, destination)) from err

    def loop(self):
        """Infinite download loop"""
        self._running_loop = True
        while self._running_loop:
            download = self.current
            try:
                if download:
                    download()
                    abs_fn = abspath(download.destination)
                    if download.stop_ts:  # download was stopped
                        tmp_fn = download.tmp_filename()
                        if os.path.exists(tmp_fn):
                            os.remove(tmp_fn)
                    else:
                        if self.printed_file_cb() != abs_fn:
                            os.rename(download.tmp_filename(), abs_fn)
                        else:
                            msg = "Gcode being printed would be" \
                                  "overwritten by downloaded file -> aborting."
                            self.event_cb(const.Event.DOWNLOAD_ABORTED,
                                          const.Source.CONNECT,
                                          reason=msg)
                    self.current = None

                    self.event_cb(const.Event.DOWNLOAD_FINISHED,
                                  const.Source.CONNECT,
                                  url=download.url,
                                  destination=download.destination)
                    self.download_finished_cb(download)
            except Exception as err:  # pylint: disable=broad-except
                log.error(err)
                self.event_cb(const.Event.DOWNLOAD_ABORTED,
                              const.Source.CONNECT,
                              reason=str(err))
                self.current = None
            time.sleep(self.LOOP_INTERVAL)

    def stop_loop(self):
        """Set internal variable to stop the download loop."""
        self._running_loop = False

    def stop(self):
        """Stop current download"""
        if self.current:
            self.current.stop()
            self.event_cb(const.Event.DOWNLOAD_STOPPED, const.Source.CONNECT)

    def info(self):
        """Return important info on Download Manager"""
        return {
            'current': self.current and self.current.to_dict(),
        }


class Download:
    """Model a single download"""

    BUFFER_SIZE = 1024
    throttle = 0.00  # after each write sleep for this amount of seconds

    # pylint: disable=too-many-arguments
    # pylint: disable=dangerous-default-value
    def __init__(self,
                 url,
                 destination,
                 to_print=False,
                 to_select=False,
                 headers={}):
        self.url = url
        self.destination = destination
        self.to_print = to_print
        self.to_select = to_select
        self.start_ts = None
        self.stop_ts = None
        self.end_ts = None
        self.progress = 0  # percentage, values: 0 to 1
        self.size = 0
        self.downloaded = 0
        self.headers = headers

    def time_remaining(self):
        """Return the estimated time remaining for the download in seconds.
        Returns None if not computation is not possible.
        """

        # finished or aborted
        if self.end_ts is not None or self.stop_ts is not None:
            return 0

        # no content-length specified
        if self.size is None:
            return None

        if self.start_ts is not None:
            elapsed = time.time() - self.start_ts
            if elapsed == 0 or self.downloaded == 0:
                return float("inf")
            return self.size / self.downloaded * elapsed

        return None

    def stop(self):
        """Stop download"""
        self.stop_ts = time.time()

    def __call__(self):
        """Execute the download and store the file in `self.tmp_filename()`"""
        self.start_ts = time.time()
        response = requests.get(self.url, stream=True, headers=self.headers)
        self.size = response.headers.get('Content-Length')
        if self.size is not None:
            self.size = int(self.size)

        # pylint: disable=invalid-name
        log.debug("Save download to: %s (%s)", self.tmp_filename(), self.url)
        with open(self.tmp_filename(), 'wb') as f:
            self.downloaded = 0
            for data in response.iter_content(chunk_size=self.BUFFER_SIZE):
                if self.stop_ts is not None:
                    return
                f.write(data)
                if self.throttle:
                    time.sleep(self.throttle)
                self.downloaded += len(data)
                if self.size is not None:
                    self.progress = self.downloaded / self.size
        self.end_ts = time.time()

    def tmp_filename(self):
        """Generate a temporary filename for download based on
        `self.destination`"""
        dir_ = dirname(self.destination)
        base = basename(self.destination)
        return abspath(os.path.join(dir_, ".%s.part" % base))

    def to_dict(self):
        """Marshall a download instance"""
        return {
            "url": self.url,
            "destination": self.destination,
            "size": self.size,
            "downloaded": self.downloaded,
            "progress": self.progress,
            "time_remaining": self.time_remaining(),
            "start": self.start_ts,
            "to_select": self.to_select,
            "to_print": self.to_print,
        }

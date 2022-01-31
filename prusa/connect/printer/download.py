"""Download functionality for SDK."""
import os
import threading
import time

from logging import getLogger
from os.path import normpath, abspath, basename, dirname
from typing import Optional

import requests

from . import const

log = getLogger("connect-printer")

# pylint: disable=too-many-instance-attributes
# NOTE: Temporary for pylint with python3.9
# pylint: disable=unsubscriptable-object

DOWNLOAD_TYPES = (const.TransferType.FROM_WEB, const.TransferType.FROM_CONNECT,
                  const.TransferType.FROM_PRINTER)


class TransferRunningError(Exception):
    """Exception thrown when a transfer is already in progress"""


class TransferAbortedError(Exception):
    """Transfer was aborted"""


class TransferStoppedError(Exception):
    """Transfer was stopped"""


class FilenameTooLongError(Exception):
    """File has exceeded filename length"""


class ForbiddenCharactersError(Exception):
    """File contains forbidden characters"""


def forbidden_characters(filename):
    """Check if filename contains any of the forbidden characters e.g. '\'
    """
    return any(character in filename for character in
               const.FORBIDDEN_CHARACTERS)


def filename_too_long(filename):
    """Check if filename lenght, including .gcode suffix, is > 248 characters
    """
    return len(filename.encode('utf-8')) > const.FILENAME_LENGTH


class DownloadMgr:
    """Download manager."""
    LOOP_INTERVAL = .1
    BUFFER_SIZE = 1024
    VALID_MIME_TYPES = ('application/gcode', 'text/plain',
                        'application/binary', 'application/octet-stream')

    def __init__(self, fs, transfer, conn_details_cb, event_cb,
                 printed_file_cb, download_finished_cb):
        # pylint: disable=invalid-name
        # pylint: disable=too-many-arguments
        self.fs = fs
        self.conn_details_cb = conn_details_cb
        self.event_cb = event_cb
        self.printed_file_cb = printed_file_cb
        self._running_loop = False
        self.headers = None
        self.transfer = transfer
        self.download_finished_cb = download_finished_cb

    def start(self, type_, path, url=None, to_print=None, to_select=None):
        """Start a download of `url` saving it into the `path`.
        This `path` is the absolute virtual path in `self.fs`
        (:class:prusa.connect.printer.files.Filesystem)
        """
        # pylint: disable=too-many-arguments
        # Check if no other transfer is running
        try:
            self.transfer.start(type_, path, url, to_print, to_select)
        except TransferRunningError:
            self.event_cb(const.Event.REJECTED,
                          const.Source.CONNECT,
                          reason="Another transfer in progress")
            return

        log.info("Starting download: %s", url)

        # transform destination to OS path and validate
        self.transfer.os_path = self.to_os_path(path)
        # make dir (in case filename contains a subdir)
        dir_ = None
        # This needs refactoring
        try:
            dir_ = dirname(self.transfer.os_path)
            os.makedirs(dir_)
        except FileExistsError:
            log.debug("%s already exists", dir_)

    def to_os_path(self, path):
        """Translate virtual `destination` of self.fs to real OS path."""
        if not os.path.isabs(path):
            raise ValueError('Destination must be absolute')
        mount_name = None
        try:
            _, mount_name, rest = path.split(self.fs.sep, 2)
            mount = self.fs.mounts[mount_name]
            path_storage = mount.path_storage.rstrip(self.fs.sep)
            os_path_ = self.fs.sep.join([path_storage, rest])
            os_path_ = normpath(os_path_)
            if not os_path_.startswith(path_storage):
                msg = "Destination is outside of defined path_storage for " \
                      "mount_point: %s"
                raise ValueError(msg % mount_name)
            return os_path_
        except KeyError as err:
            raise ValueError("Invalid mount point: `%s` in `%s`" %
                             (mount_name, path)) from err

    def loop(self):
        """Infinite download loop"""
        # pylint: disable=too-many-nested-blocks
        self._running_loop = True
        while self._running_loop:
            if self.transfer.type in DOWNLOAD_TYPES:
                try:
                    self.download()
                    abs_fn = abspath(self.transfer.os_path)
                    if self.transfer.stop_ts:  # download was stopped
                        tmp_fn = self.tmp_filename()
                        if os.path.exists(tmp_fn):
                            os.remove(tmp_fn)
                    else:
                        if self.printed_file_cb() != abs_fn:
                            os.rename(self.tmp_filename(), abs_fn)
                        else:
                            msg = "Gcode being printed would be" \
                                  "overwritten by downloaded file -> aborting."
                            self.event_cb(const.Event.TRANSFER_ABORTED,
                                          const.Source.CONNECT,
                                          reason=msg)

                    self.event_cb(const.Event.TRANSFER_FINISHED,
                                  const.Source.CONNECT,
                                  url=self.transfer.url,
                                  destination=self.transfer.path)
                    self.download_finished_cb(self.transfer)

                except TransferStoppedError:
                    self.event_cb(const.Event.TRANSFER_STOPPED,
                                  const.Source.CONNECT)

                except Exception as err:  # pylint: disable=broad-except
                    log.error(err)
                    self.event_cb(const.Event.TRANSFER_ABORTED,
                                  const.Source.CONNECT,
                                  reason=str(err))
                finally:
                    # End of transfer - reset transfer data
                    self.transfer.type = const.TransferType.NO_TRANSFER

            time.sleep(self.LOOP_INTERVAL)

    def stop_loop(self):
        """Set internal variable to stop the download loop."""
        self._running_loop = False

    def info(self):
        """Return important info on Download Manager"""
        return self.transfer.to_dict()

    def download(self):
        """Execute the download and store the file in `self.tmp_filename()`"""
        self.transfer.start_ts = time.time()
        server, self.headers = self.conn_details_cb()

        # server is not connect server, set token to None
        if not server or \
                not self.transfer.url.lower().startswith(server.lower()):
            self.headers = {}

        res = requests.get(self.transfer.url,
                           stream=True,
                           headers=self.headers)

        if res.status_code != 200:
            raise TransferAbortedError("Invalid status code: %s" %
                                       res.status_code)
        mime_type = res.headers.get('Content-Type')
        if mime_type and mime_type.lower() not in self.VALID_MIME_TYPES:
            raise TransferAbortedError("Invalid content type: %s" % mime_type)
        self.transfer.size = res.headers.get('Content-Length')
        if self.transfer.size is not None:
            self.transfer.size = int(self.transfer.size)

        # pylint: disable=invalid-name
        log.debug("Save download to: %s (%s)", self.tmp_filename(),
                  self.transfer.url)
        with open(self.tmp_filename(), 'wb') as f:
            for data in res.iter_content(chunk_size=self.BUFFER_SIZE):
                if self.transfer.stop_ts > 0:
                    raise TransferStoppedError("Transfer was stopped")
                f.write(data)
                if self.transfer.throttle:
                    time.sleep(self.transfer.throttle)
                self.transfer.transferred += len(data)
        if not self.transfer.transferred:
            raise TransferAbortedError("Empty response")

    def tmp_filename(self):
        """Generate a temporary filename for download based on
        `self.destination`"""
        dir_ = dirname(self.transfer.os_path)
        base = basename(self.transfer.path)
        return abspath(os.path.join(dir_, ".%s.part" % base))


class Transfer:
    """File transfer representation object"""

    url: Optional[str] = None
    to_print: Optional[bool] = None
    to_select: Optional[bool] = None

    def __init__(self):
        self.type = const.TransferType.NO_TRANSFER
        self.path = None
        self.size = None
        self.transferred = 0
        self.event_cb = None
        self.throttle = 0.00  # after each write sleep for this amount of secs.
        self.lock = threading.Lock()

        self.start_ts = 0
        self.stop_ts = 0

    @property
    def in_progress(self):
        """Return True if any transfer is in progress"""
        return self.type != const.TransferType.NO_TRANSFER

    def start(self, type_, path, url=None, to_print=None, to_select=None):
        """Set a new transfer type, if no transfer is in progress"""
        # pylint: disable=too-many-arguments
        filename = basename(path)

        if forbidden_characters(filename):
            raise ForbiddenCharactersError(
                "File name contains forbidden characters")

        if filename_too_long(filename):
            raise FilenameTooLongError(
                "File name length is too long")

        with self.lock:
            if self.in_progress:
                raise TransferRunningError
            self.reset()

            self.type = type_
            self.path = path
            self.url = url
            self.to_print = to_print
            self.to_select = to_select

    def stop(self):
        """Stop transfer"""
        self.stop_ts = time.time()

    def reset(self):
        """Reset transfer data"""
        self.size = None
        self.transferred = 0
        self.start_ts = 0
        self.stop_ts = 0

    def get_speed(self):
        """Return current transfer speed"""

    @property
    def progress(self):
        """Calculate current transfer progress"""
        if self.size is not None:
            return self.transferred / self.size * 100
        return 0.0

    def time_remaining(self):
        """Return the estimated time remaining for the transfer in seconds.
        Returns None if not computation is not possible.
        """
        # finished or aborted
        if self.stop_ts > 0:
            return 0

        # no content-length specified
        if self.size is None:
            return None

        if self.start_ts > 0:
            elapsed = time.time() - self.start_ts
            if elapsed == 0 or self.transferred == 0:
                return None  # stands for Infinity
            return round(self.size / self.transferred * elapsed - elapsed, 0)
        return None

    def to_dict(self):
        """Serialize a transfer instance."""
        if self.in_progress:
            time_remaining = self.time_remaining()
            if isinstance(time_remaining, float):
                time_remaining = int(time_remaining)
            return {
                "type": self.type.value,
                "path": self.path,
                "url": self.url,
                "size": self.size,
                "start": int(self.start_ts),
                "progress": float("%.2f" % self.progress),
                "transferred": self.transferred,
                "time_remaining": time_remaining,
                "to_select": self.to_select,
                "to_print": self.to_print,
            }
        return {"type": self.type.value}

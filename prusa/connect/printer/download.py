"""Download functionality for SDK."""
import os
import threading
import time

from logging import getLogger
from os.path import normpath, abspath, basename, dirname

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


class DownloadMgr:
    """Download manager."""
    LOOP_INTERVAL = .1
    BUFFER_SIZE = 1024
    VALID_MIME_TYPES = ('application/gcode', 'text/plain',
                        'application/binary', 'application/octet-stream')

    def __init__(self, fs, conn_details_cb, event_cb, printed_file_cb,
                 transfer):
        # pylint: disable=invalid-name
        # pylint: disable=too-many-arguments
        self.fs = fs
        self.conn_details_cb = conn_details_cb
        self.event_cb = event_cb
        self.printed_file_cb = printed_file_cb
        self._running_loop = False
        self.headers = None
        self.transfer = transfer
        self.download_finished_cb = lambda transfer: None

    def start(self, type, url, path, to_print, to_select):
        """Start a download of `url` saving it into the `path`.
        This `path` is the absolute virtual path in `self.fs`
        (:class:prusa.connect.printer.files.Filesystem)
        """
        # pylint: disable=too-many-arguments
        # Check if no other transfer is running
        try:
            self.transfer.start(type, url, path, to_print,
                                         to_select)
        except TransferRunningError:
            self.event_cb(const.Event.REJECTED,
                          const.Source.CONNECT,
                          reason="Another transfer in progress")
            return

        log.info("Starting download: %s", url)
        # self.transfer.url = url
        # self.transfer.path = path
        # self.transfer.to_print = to_print
        # self.transfer.to_select = to_select

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
                    # TODO - reset in beginning or in the end of transfer?
                    self.transfer.type = const.TransferType.NO_TRANSFER
                    # self.transfer.reset()

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
                self.transfer.completed += len(data)
        if not self.transfer.completed:
            raise TransferAbortedError("Empty response")

    def tmp_filename(self):
        """Generate a temporary filename for download based on
        `self.destination`"""
        dir_ = dirname(self.transfer.os_path)
        base = basename(self.transfer.path)
        return abspath(os.path.join(dir_, ".%s.part" % base))


class Transfer:
    """File transfer representation object"""
    def __init__(self):
        self.type = const.TransferType.NO_TRANSFER
        self.url = None
        self.path = None
        self.size = None
        self.estimated_end = 0
        self.completed = 0
        self.to_select = False
        self.to_print = False
        self.event_cb = None
        self.throttle = 0.00  # after each write sleep for this amount of secs.
        self.lock = threading.Lock()

        self.start_ts = 0
        self.stop_ts = 0

    @property
    def in_progress(self):
        """Return True if any transfer is in progress"""
        return self.type != const.TransferType.NO_TRANSFER

    def start(self, type, url, path, to_print, to_select):
        """Set a new transfer type, if no transfer is in progress"""
        # pylint: disable=too-many-arguments
        with self.lock:
            if self.in_progress:
                raise TransferRunningError
            self.reset()

            self.type = type
            self.url = url
            self.path = path
            self.to_print = to_print
            self.to_select = to_select

    def stop(self):
        """Stop transfer"""
        self.stop_ts = time.time()

    def reset(self):
        """Reset transfer data"""
        self.type = const.TransferType.NO_TRANSFER
        self.size = None
        self.completed = 0
        self.start_ts = 0
        self.stop_ts = 0

    def get_speed(self):
        """Return current transfer speed"""

    @property
    def progress(self):
        """Calculate current transfer progress"""
        if self.size is not None:
            return self.completed / self.size * 100
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
            return -1

        if self.start_ts is not None:
            elapsed = time.time() - self.start_ts
            if elapsed == 0 or self.completed == 0:
                return -1  # stands for Infinity
            return int(self.size / self.completed * elapsed - elapsed)
        return -1

    def to_dict(self):
        """Serialize a transfer instance"""
        return {
            "type": self.type.value,
            "url": self.url,
            "path": self.path,
            "size": self.size,
            "start": self.start_ts,
            "estimated_end": self.estimated_end,
            "progress": float("%.2f" % self.progress),
            "completed": self.completed,
            "time_remaining": self.time_remaining(),
            "to_select": self.to_select,
            "to_print": self.to_print,
        }

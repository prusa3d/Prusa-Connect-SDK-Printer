"""Download functionality for SDK."""
import os
import threading
import time
from logging import getLogger
from os.path import abspath, basename, dirname, normpath
from random import randint
from typing import Callable, Optional

import requests  # type: ignore

from . import const
from .const import CONNECTION_TIMEOUT, Event, Source, TransferType
from .files import Filesystem
from .models import EventCallback

log = getLogger("connect-printer")

# pylint: disable=too-many-instance-attributes
# NOTE: Temporary for pylint with python3.9
# pylint: disable=unsubscriptable-object

DOWNLOAD_TYPES = (TransferType.FROM_WEB, TransferType.FROM_CONNECT,
                  TransferType.FROM_PRINTER)


class TransferRunningError(Exception):
    """Exception thrown when a transfer is already in progress"""


class TransferAbortedError(Exception):
    """Transfer was aborted"""


class TransferStoppedError(Exception):
    """Transfer was stopped"""


class ForbiddenCharactersError(Exception):
    """Forbidden characters in filename or foldername"""
    message = "Forbidden characters in filename or foldername"

    def __init__(self, msg=None):
        super().__init__(msg or self.message)


class FilenameTooLongError(Exception):
    """Filename length is too long"""
    message = "Filename length is too long"

    def __init__(self, msg=None):
        super().__init__(msg or self.message)


class FoldernameTooLongError(Exception):
    """Foldername length is too long"""
    message = "Foldername length is too long"

    def __init__(self, msg=None):
        super().__init__(msg or self.message)


def forbidden_characters(path):
    """Check if path contains any of the forbidden characters e.g. '\'
    """
    return any(character in path for character in const.FORBIDDEN_CHARACTERS)


def filename_too_long(filename):
    """Check if filename lenght, including .gcode suffix, is > 248 characters
    """
    return len(filename.encode('utf-8')) > const.FILENAME_LENGTH


def foldername_too_long(path):
    """Check if any foldername length in path is > 255 characters"""
    path_ = path.split(os.sep)
    return any(len(folder) > const.MAX_NAME_LENGTH for folder in path_)


def generate_transfer_id():
    """Return transfer ID as 32bit integer"""
    return randint(0, 2**32 - 1)


class Transfer:
    """File transfer representation object"""

    url: Optional[str] = None
    to_print: Optional[bool] = None
    to_select: Optional[bool] = None
    start_cmd_id: Optional[int] = None
    path: Optional[str] = None
    size: Optional[int] = None
    hash: Optional[str] = None
    team_id: Optional[int] = None
    os_path: str

    def __init__(self):
        self.transfer_id = None
        self.type = TransferType.NO_TRANSFER
        self._transferred = 0
        self.lock = threading.Lock()

        self.started_cb = lambda: None
        self.progress_cb = lambda: None
        self.stopped_cb = lambda: None

        # start_ts is deprecated, because it uses system time, instead of tics
        self.start_ts = 0
        self.stop_ts = 0

        self.start_time = None

    @property
    def transferred(self):
        """Returns the number of bytes already transferred"""
        return self._transferred

    @transferred.setter
    def transferred(self, transferred):
        """Sets the number of bytes transferred and calls back, so UI can
        update and whatnot"""
        self._transferred = transferred
        self.progress_cb()

    @property
    def in_progress(self):
        """Return True if any transfer is in progress"""
        return self.type != TransferType.NO_TRANSFER

    def start(self,
              type_: TransferType,
              path: str,
              url: Optional[str] = None,
              to_print: Optional[bool] = None,
              to_select: Optional[bool] = None,
              start_cmd_id: Optional[int] = None,
              hash_: Optional[str] = None,
              team_id: Optional[int] = None) -> dict:
        """Set a new transfer type, if no transfer is in progress"""
        # pylint: disable=too-many-arguments
        filename = basename(path)

        if forbidden_characters(filename):
            raise ForbiddenCharactersError()

        if filename_too_long(filename):
            raise FilenameTooLongError()

        if foldername_too_long(path):
            raise FoldernameTooLongError()

        with self.lock:
            if self.in_progress:
                raise TransferRunningError
            self.reset()

            self.start_cmd_id = start_cmd_id
            self.transfer_id = generate_transfer_id()
            self.start_time = time.monotonic()
            self.type = type_
            self.path = path
            self.url = url
            self.to_print = to_print
            self.to_select = to_select
            self.hash = hash_
            self.team_id = team_id
            self.started_cb()

            retval = self.to_dict()
            retval['event'] = Event.TRANSFER_INFO
            retval['source'] = Source.WUI
            return retval

    def stop(self):
        """Stop transfer - set the stop timestamp"""
        self.stop_ts = time.time()
        self.stopped_cb()

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

    def time_transferring(self):
        """Return elapsed transferring time as a difference of the start
        time and current time using monotonic"""
        return int(time.monotonic() - self.start_time)

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
            return int(self.size / self.transferred * elapsed - elapsed)
        return None

    def to_dict(self):
        """Serialize a transfer instance."""
        if self.in_progress:
            time_remaining = self.time_remaining()
            if isinstance(time_remaining, float):
                time_remaining = int(time_remaining)
            return {
                "transfer_id": self.transfer_id,
                "start_cmd_id": self.start_cmd_id,
                "type": self.type.value,
                "path": self.path,
                "url": self.url,
                "size": self.size,
                "progress": float("%.2f" % self.progress),
                "transferred": self.transferred,
                "time_remaining": time_remaining,
                "time_transferring": self.time_transferring(),
                "to_print": self.to_print,
            }
        return {"type": self.type.value}


class DownloadMgr:
    """Download manager."""
    LOOP_INTERVAL = .1
    VALID_MIME_TYPES = ('text/plain', 'text/x.gcode', 'application/binary',
                        'application/octet-stream')
    SMALL_BUFFER = 1024
    BIG_BUFFER = 1024 * 100

    def __init__(self, fs: Filesystem, transfer: Transfer,
                 conn_details_cb: Callable, event_cb: EventCallback,
                 printed_file_cb: Callable, download_finished_cb: Callable):
        # pylint: disable=invalid-name
        # pylint: disable=too-many-arguments
        self.buffer_size = self.BIG_BUFFER
        self.throttle = 0
        self.fs = fs
        self.conn_details_cb = conn_details_cb
        self.event_cb = event_cb
        self.printed_file_cb = printed_file_cb
        self._running_loop = False
        self.headers = None
        self.transfer = transfer
        self.download_finished_cb = download_finished_cb

    def start(self,
              type_: TransferType,
              path: str,
              url: Optional[str] = None,
              to_print: Optional[bool] = None,
              to_select: Optional[bool] = None,
              start_cmd_id: Optional[int] = None,
              hash_: Optional[str] = None,
              team_id: Optional[int] = None) -> dict:
        """Start a download of `url` saving it into the `path`.
        This `path` is the absolute virtual path in `self.fs`
        (:class:prusa.connect.printer.files.Filesystem)
        """
        # pylint: disable=too-many-arguments
        # Check if no other transfer is running
        retval = {}
        try:
            retval = self.transfer.start(type_, path, url, to_print, to_select,
                                         start_cmd_id, hash_, team_id)
        except TransferRunningError:
            return {
                "event": Event.REJECTED,
                "source": Source.CONNECT,
                "reason": "Another transfer in progress",
            }

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

        return retval

    def to_os_path(self, path: str):
        """Translate virtual `destination` of self.fs to real OS path."""
        if not os.path.isabs(path):
            raise ValueError('Destination must be absolute')
        storage_name = None
        try:
            _, storage_name, rest = path.split(self.fs.sep, 2)
            storage = self.fs.storage_dict[storage_name]
            if not storage.path_storage:
                raise ValueError("Storage does not have path_storage.")
            path_storage = storage.path_storage.rstrip(self.fs.sep)
            os_path_ = self.fs.sep.join([path_storage, rest])
            os_path_ = normpath(os_path_)
            if not os_path_.startswith(path_storage):
                msg = "Destination is outside of defined path_storage for " \
                      "storage: %s"
                raise ValueError(msg % storage_name)
            return os_path_
        except KeyError as err:
            raise ValueError("Invalid storage: `%s` in `%s`" %
                             (storage_name, path)) from err

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
                            self.event_cb(
                                Event.TRANSFER_ABORTED,
                                Source.CONNECT,
                                reason=msg,
                                path=self.transfer.path,
                                transfer_id=self.transfer.transfer_id,
                                start_cmd_id=self.transfer.start_cmd_id)

                    self.event_cb(Event.TRANSFER_FINISHED,
                                  Source.CONNECT,
                                  start_cmd_id=self.transfer.start_cmd_id,
                                  path=self.transfer.path,
                                  transfer_id=self.transfer.transfer_id)
                    self.download_finished_cb(self.transfer)

                except TransferStoppedError:
                    self.event_cb(Event.TRANSFER_STOPPED,
                                  Source.CONNECT,
                                  path=self.transfer.path,
                                  transfer_id=self.transfer.transfer_id,
                                  start_cmd_id=self.transfer.start_cmd_id)

                except Exception as err:  # pylint: disable=broad-except
                    log.error(err)
                    self.event_cb(Event.TRANSFER_ABORTED,
                                  Source.CONNECT,
                                  reason=str(err),
                                  path=self.transfer.path,
                                  transfer_id=self.transfer.transfer_id,
                                  start_cmd_id=self.transfer.start_cmd_id)
                finally:
                    # End of transfer - reset transfer data
                    self.transfer.type = TransferType.NO_TRANSFER

            time.sleep(self.LOOP_INTERVAL)

    def stop_loop(self):
        """Set internal variable to stop the download loop."""
        self._running_loop = False

    def info(self):
        """Returns important info of Download Manager"""
        return self.transfer.to_dict()

    def download(self):
        """Execute the download and store the file in `self.tmp_filename()`"""
        self.transfer.start_ts = time.time()
        server, self.headers = self.conn_details_cb()

        # server is not connect server, set token to None
        if not server or \
                not self.transfer.url.lower().startswith(server.lower()):
            user_agent = self.headers.get("User-Agent")
            self.headers = {"User-Agent": user_agent}

        res = requests.get(self.transfer.url,
                           stream=True,
                           headers=self.headers,
                           timeout=CONNECTION_TIMEOUT)

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
            self.event_cb(Event.TRANSFER_INFO, Source.WUI, **self.info())
            for data in res.iter_content(chunk_size=self.buffer_size):
                if self.transfer.stop_ts > 0:
                    raise TransferStoppedError("Transfer was stopped")
                if not self._running_loop:
                    raise TransferAbortedError("Transfer was aborted")
                f.write(data)
                if self.throttle:
                    time.sleep(self.throttle)
                self.transfer.transferred += len(data)

        if not self.transfer.transferred:
            raise TransferAbortedError("Empty response")

    def tmp_filename(self):
        """Generate a temporary filename for download based on
        `self.destination`"""
        dir_ = dirname(self.transfer.os_path)
        base = basename(self.transfer.path)
        return abspath(os.path.join(dir_, ".%s.part" % base))

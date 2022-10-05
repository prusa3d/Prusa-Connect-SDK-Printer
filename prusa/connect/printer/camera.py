"""Camera functionality"""
from logging import getLogger
from queue import Queue, Empty

from .models import Snapshot, CameraRegister
from .const import TIMESTAMP_PRECISION

log = getLogger("connect-camera")


class CameraMgr:
    """Camera Manager representation object."""
    snapshot_queue: "Queue[Snapshot]"

    def __init__(self, conn, queue):
        """
        :conn: Current RetryingSession connection
        :server Connect server URL
        """
        self.conn = conn
        self.server = None
        self.queue = queue
        self.snapshot_queue = Queue()
        self.__running_snapshot_loop = False

    def register(self, data):
        """Register the camera with Connect"""
        self.queue.put(CameraRegister(data))

    def registration_cb(self, res, data):
        """Registration callback"""
        # pylint: disable=unused-argument
        if res.status_code == 200:
            pass  # make some magic with camera token
        else:
            log.warning(res.text)

    def snapshot(self, data: bytes, camera_fingerprint: str, camera_token: str,
                 timestamp: float):
        """Create snapshot and push it to the queue"""
        snapshot = Snapshot(data, camera_fingerprint, camera_token, timestamp)

        self.snapshot_queue.put(snapshot)

    def snapshot_loop(self):
        """Gets an item Snapshot from queue and sends it"""
        self.__running_snapshot_loop = True
        while self.__running_snapshot_loop:
            try:
                # Get the item to send
                item = self.snapshot_queue.get(
                    timeout=TIMESTAMP_PRECISION)

                # Send it
                res = item.send_data(self.conn, self.server)
                if res.status_code in (401, 403):
                    item.fail_cb()
                if res.status_code > 400:
                    log.warning(res.text)
                elif res.status_code == 400:
                    log.debug(res.text)
            except Empty:
                continue
            except Exception: # pylint: disable=broad-except
                log.exception(
                    "Unexpected exception caught in SDK snapshot loop!")

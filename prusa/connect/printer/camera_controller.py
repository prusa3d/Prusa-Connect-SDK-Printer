"""Implementation of CameraController"""
import logging
from functools import partial
from queue import Queue, Empty
from time import time
from typing import Set, Dict, List, Callable, Iterator, Optional

from requests import Session

from .camera import Snapshot, Camera
from .const import TriggerScheme, TIMESTAMP_PRECISION, \
    TRIGGER_SCHEME_TO_SECONDS
from .models import CameraRegister, LoopObject

log = logging.getLogger("camera_controller")

# pylint: disable=fixme
# TODO: Add Z change trigger thingy


class CameraController:
    """This component harbors functioning cameras, triggers them, sends out
    images to connect and should contain functionality needed for operating
    with functional cameras"""
    def __init__(self, session: Session, server: Optional[str],
                 send_cb: Callable[[LoopObject], None]) -> None:
        """
        :session: Current Session connection
        :server: Connect server URL
        :send_cb: a callback for sending LoopObjects
        """
        # A callback for sending LoopObjects to Connect
        self.send_cb = send_cb
        self.session = session
        self.server = server
        # pylint: disable=unsubscriptable-object
        self.snapshot_queue: Queue[Snapshot] = Queue()

        self._cameras: Dict[str, Camera] = {}
        self._camera_order: List[str] = []
        self._trigger_piles: Dict[TriggerScheme, Set[Camera]] = {
            scheme: set()
            for scheme in TriggerScheme
        }

        # --- triggers ---
        self._layer_trigger_counter = 0
        self._last_trigger = {}
        self._time_triggers = {}
        for trigger_scheme in TRIGGER_SCHEME_TO_SECONDS:
            self._time_triggers[trigger_scheme] = partial(
                self._interval_elapsed, trigger_scheme)
            self._last_trigger[trigger_scheme] = time()
        self._running = False

    def add_camera(self, camera: Camera) -> None:
        """Adds a camera. This camera has to be functional"""
        camera_id = camera.camera_id
        self._cameras[camera_id] = camera
        self._trigger_piles[camera.trigger_scheme].add(camera)
        camera.scheme_cb = self.scheme_handler
        camera.photo_cb = self.photo_handler

    def remove_camera(self, camera_id: str) -> None:
        """Removes the camera, either on request, or because it became
        disconnected, removes """
        if camera_id not in self._cameras:
            return
        camera = self._cameras[camera_id]
        self._trigger_piles[camera.trigger_scheme].remove(camera)
        del self._cameras[camera_id]

    def get_camera(self, camera_id: str) -> Camera:
        """Gets the camera by its ID"""
        return self._cameras[camera_id]

    def __contains__(self, camera_id: str) -> bool:
        """Does the camera manager know about a camera with given ID?"""
        return camera_id in self._cameras

    @property
    def cameras_in_order(self) -> Iterator[Camera]:
        """Iterates over functional cameras in the configured order"""
        for camera_id in self._camera_order:
            if camera_id in self._cameras:
                yield self._cameras[camera_id]

    def set_camera_order(self, camera_order: List[str]) -> None:
        """Usually called by the CameraConfigurator to order
        the SDK cameras"""
        self._camera_order = camera_order

    def register_camera(self, camera_id: str) -> None:
        """Passes the camera to SDK for registration"""
        if camera_id not in self._cameras:
            log.warning(
                "Tried registering a camera id: %s that's not "
                "tracked by this controller", camera_id)
            return
        self.send_cb(CameraRegister(self.get_camera(camera_id)))

    def _interval_elapsed(self, trigger_scheme) -> bool:
        """Is it time to trigger the time based trigger scheme pile?"""
        interval = TRIGGER_SCHEME_TO_SECONDS[trigger_scheme]
        if time() - self._last_trigger[trigger_scheme] < interval:
            return False
        self._last_trigger[trigger_scheme] = time()
        return True

    def layer_trigger(self):
        """Called every layer, triggers the layer dependant trigger schemes"""
        self._layer_trigger_counter += 1
        self.trigger_pile(TriggerScheme.EACH_LAYER)
        if not self._layer_trigger_counter % 5:
            self.trigger_pile(TriggerScheme.FIFTH_LAYER)
            self._layer_trigger_counter = 0

    def tick(self) -> None:
        """Called periodically by the SDK to let us trigger cameras when it's
        the right time"""
        for scheme, trigger in self._time_triggers.items():
            if trigger():
                self.trigger_pile(scheme)

    def trigger_pile(self, scheme: TriggerScheme) -> None:
        """Triggers a pile of cameras (cameras are piled by their trigger
        scheme)"""
        for camera in self._trigger_piles[scheme]:
            if camera.is_busy:
                log.warning("Skipping camera %s because it's busy",
                            camera.name)
            else:
                camera.trigger_a_photo()

    def scheme_handler(self, camera: Camera, old: TriggerScheme,
                       new: TriggerScheme) -> None:
        """Transfers cameras between the triggering scheme piles"""
        self._trigger_piles[old].remove(camera)
        self._trigger_piles[new].add(camera)

    def photo_handler(self, camera: Camera, photo_data: bytes) -> None:
        """Here a callback call to the SDK starts the image upload"""
        if not camera.is_registered:
            return
        log.debug("A camera %s has taken a photo. (%s bytes)", camera.name,
                  len(photo_data))
        snapshot = Snapshot(photo_data, camera)
        self.snapshot_queue.put(snapshot)

    def snapshot_loop(self) -> None:
        """Gets an item Snapshot from queue and sends it"""
        self._running = True
        while self._running:
            try:
                # Get the item to send
                item = self.snapshot_queue.get(timeout=TIMESTAMP_PRECISION)

                # Send it
                res = item.send(self.session, self.server)
                if res.status_code in (401, 403):
                    log.error("Failed to authorize request, "
                              "resetting camera token")
                    item.camera.set_token(None)
                if res.status_code > 400:
                    log.warning(res.text)
                elif res.status_code == 400:
                    log.debug(res.text)
            except Empty:
                continue
            except Exception:  # pylint: disable=broad-except
                log.exception(
                    "Unexpected exception caught in SDK snapshot loop!")

    def stop(self) -> None:
        """Signals to the loop to stop"""
        self._running = False

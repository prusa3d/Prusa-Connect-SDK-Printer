"""Implementation of CameraController"""
import logging
from queue import Queue, Empty
from time import time
from typing import Set, Dict, List

from requests import Session

from .camera import Snapshot, Camera
from .const import TriggerScheme, TIMESTAMP_PRECISION
from .models import CameraRegister

log = logging.getLogger("camera_configurators")

# pylint: disable=fixme
# TODO: Add Z change trigger thingy


class CameraController:
    """This component harbors functioning cameras, triggers them, sends out
    images to connect and should contain functionality needed for operating
    with functional cameras"""
    def __init__(self, session: Session, server, send_cb):
        """
        :session: Current Session connection
        :server: Connect server URL
        :send_cb: a callback for sending LoobObjects
        """
        # A callback for sending LoopObjects to Connect
        self.send_cb = send_cb
        # A callback for saving cameras
        self.save_cb = lambda camera: None
        self.session = session
        self.server = server
        # pylint: disable=unsubscriptable-object
        self.snapshot_queue: Queue[Snapshot] = Queue()

        self._cameras: Dict[str, Camera] = {}
        self._camera_order: List[str] = []
        self._trigger_piles: Dict[TriggerScheme, Set[Camera]] = {
            TriggerScheme.TEN_MIN: set(),
            TriggerScheme.EACH_LAYER: set(),
            TriggerScheme.MANUAL: set(),
        }

        # --- triggers ---
        self.layer_changed = False  # Flip this to true from outside
        self._last_trigger = time()
        self._triggers = {
            TriggerScheme.TEN_MIN: self._was_10_min,
            TriggerScheme.EACH_LAYER: self._was_layer_change
        }
        self._running = False

    def add_camera(self, camera: Camera):
        """Adds a camera. This camera has to be functional"""
        camera_id = camera.camera_id
        self._cameras[camera_id] = camera
        self._trigger_piles[camera.trigger_scheme].add(camera)
        camera.scheme_cb = self.scheme_handler
        camera.photo_cb = self.photo_handler
        camera.save_cb = self.save_cb

        # Register if the camera does not have a token
        # TODO: Do something more intelligent
        if not camera.is_registered:
            self.register_camera(camera_id)

    def remove_camera(self, camera_id):
        """Removes the camera, either on request, or because it became
        disconnected, removes """
        if camera_id not in self._cameras:
            return
        camera = self._cameras[camera_id]
        self._trigger_piles[camera.trigger_scheme].remove(camera)
        del self._cameras[camera_id]

    def get_camera(self, camera_id):
        """Gets the camera by its ID"""
        return self._cameras[camera_id]

    def __contains__(self, camera_id):
        """Does the camera manager know about a camera with given ID?"""
        return camera_id in self._cameras

    @property
    def cameras_in_order(self):
        """Iterates over functional cameras in the configured order"""
        for camera_id in self._camera_order:
            if camera_id in self._cameras:
                yield self._cameras[camera_id]

    def set_camera_order(self, camera_order):
        """Usually called by the CameraConfigurator to order
        the SDK cameras"""
        self._camera_order = camera_order

    def register_camera(self, camera_id):
        """Passes the camera to SDK for registration"""
        if camera_id not in self._cameras:
            log.warning(
                "Tried registering a camera id: %s that's not "
                "tracked by this controller", camera_id)
            return
        self.send_cb(CameraRegister(self.get_camera(camera_id)))

    def _was_10_min(self):
        """Was it ten minutes since we triggered the cameras?"""
        if time() - self._last_trigger < 10:
            return False
        self._last_trigger = time()
        return True

    def _was_layer_change(self):
        """Did a layer change occur since last triggering the cameras?"""
        if not self.layer_changed:
            return False
        self.layer_changed = False
        return True

    def tick(self):
        """Called periodically by the SDK to let us trigger cameras when it's
        the right time"""
        for scheme, trigger in self._triggers.items():
            if trigger():
                self.trigger_pile(scheme, "tick")

    def trigger_pile(self, scheme: TriggerScheme, trigger_info: str):
        """Triggers a pile of cameras (cameras are piled by their trigger
        scheme)"""
        for camera in self._trigger_piles[scheme]:
            if camera.is_busy:
                log.warning("Skipping camera %s because it's busy",
                            camera.name)
            else:
                # FIXME: trigger_info is a temporary debugging thing
                self.trigger_info = trigger_info
                camera.trigger_a_photo()

    def scheme_handler(self, camera: Camera, old: TriggerScheme,
                       new: TriggerScheme):
        """Transfers cameras between the triggering scheme piles"""
        self._trigger_piles[old].remove(camera)
        self._trigger_piles[new].add(camera)

    def photo_handler(self, camera: Camera, photo_data):
        """Here a callback call to the SDK starts the image upload
        Note to self: Don't block, get your own thread
        """
        with open(f"snapshots/{self.trigger_info}.jpg", "wb") as file:
            file.write(photo_data)

        # if not camera.is_registered:
        #     self.register_camera(camera.camera_id)
        #     return
        # log.debug("A camera %s has taken a photo. (%s bytes)", camera.name,
        #           len(photo_data))
        # snapshot = Snapshot(photo_data, camera)
        # self.snapshot_queue.put(snapshot)

    def snapshot_loop(self):
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

    def stop(self):
        """Signals to the loop to stop"""
        self._running = False

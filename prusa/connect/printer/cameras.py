"""
Contains the implementation of all SDK camera classes
The camera capabilities
Cameras and their drivers
Camera configurator and manager
"""
import logging
from copy import deepcopy
from queue import Queue, Empty
from time import time
from threading import Event
from typing import Any, Set, Dict, List, Optional

from requests import Session

from .util import get_timestamp, make_fingerprint
from .const import PHOTO_TIMEOUT, CapabilityType, NotSupported, CameraBusy, \
    DriverError, TriggerScheme, DEFAULT_CAMERA_SETTINGS, TIMESTAMP_PRECISION, \
    CONNECTION_TIMEOUT
from .models import Resolution, CameraRegister

log = logging.getLogger("cameras")

# pylint: disable=fixme
# TODO: Add Z change trigger thingy
# --- SDK ---


class Snapshot:
    """Snapshot from the camera"""
    endpoint = "/c/snapshot"
    method = "PUT"

    # pylint: disable=too-many-arguments
    def __init__(self, data: bytes, camera: "Camera"):
        self.data = data
        self.camera = camera
        self.timestamp = get_timestamp()

    def send(self, conn: Session, server):
        """A snapshot send function"""
        name = self.__class__.__name__
        log.debug("Sending %s: %s", name, self)

        headers = {
            "Timestamp": str(self.timestamp),
            "Fingerprint": self.camera.fingerprint,
            "Token": self.camera.token,
            'Content-Type': "image/jpg"
        }
        res = conn.request(method=self.method,
                           url=server + self.endpoint,
                           headers=headers,
                           data=self.data,
                           timeout=CONNECTION_TIMEOUT)

        log.debug("%s response: %s", name, res.text)
        return res


def value_setter(capability_type):
    """A decorator for methods setting a camera option while making sure
    it is valid"""
    def value_setter_decorator(func):
        def inner(camera: "Camera", value):
            old_value = camera.get_value(capability_type)
            if not camera.supports(capability_type):
                raise NotSupported(
                    f"The camera {camera.name} does not support setting "
                    f"{capability_type.name}")
            if camera.is_busy:
                raise CameraBusy(f"The camera {camera.name} is far too busy "
                                 f"to take care of your request")
            try:
                func(camera, value)
            except Exception as exception:  # pylint: disable=broad-except
                try:
                    func(camera, old_value, call_back=False)
                except (AttributeError, ValueError, KeyError, TypeError):
                    # Might not be an ideal way to resolve this
                    camera.force_reset(capability_type)
                finally:
                    # pylint: disable=protected-access
                    raise DriverError(
                        f"The driver {camera._driver.name} failed to set the"
                        f" {capability_type.value} from {old_value} "
                        f"to {value}.") from exception

        return inner

    return value_setter_decorator


def value_getter(capability_type):
    """A decorator for methods getting a camera option"""
    def value_getter_decorator(func):
        def inner(camera: "Camera"):
            if not camera.supports(capability_type):
                raise NotSupported(
                    f"The camera {camera.name} does not support "
                    f"{capability_type.name}")
            return func(camera)

        return inner

    return value_getter_decorator


# pylint: disable=too-many-public-methods
class Camera:
    """The class for fully loaded cameras to be operated by the application"""
    _last_photo: Any

    def __init__(self, driver):
        self._trigger_scheme = TriggerScheme.TEN_MIN
        self._resolution = None
        self._available_resolutions = {}
        self._rotation = 0
        self._exposure = 0.0
        self._token = None

        self._ready_event = Event()
        self._ready_event.set()
        self._last_photo = None

        # Changed the trigger scheme of this camera - tell the manager
        self.scheme_cb = lambda camera, old, new: None
        # A photo has been taken - give it to the manager
        self.photo_cb = lambda camera, photo: None
        # A callback for saving the current camera config
        self.save_cb = lambda camera_id: None

        self._driver = driver
        self._driver.photo_cb = self._photo_handler

        self._supported_capabilities = frozenset(
            self._driver.supported_capabilities)
        if CapabilityType.TRIGGER_SCHEME not in self._supported_capabilities:
            raise AttributeError(
                "Be sure to fill out driver supported capabilities. "
                "TRIGGER_SCHEME is the bare minimum")

        if self.supports(CapabilityType.RESOLUTION):
            self._available_resolutions = self._driver.available_resolutions

        # - Initial settings -
        initial_settings = deepcopy(DEFAULT_CAMERA_SETTINGS)

        config = self._driver.config
        driver_settings = self.settings_from_string(config)
        initial_settings.update(driver_settings)

        # Check for missing configuration
        needed = set(
            map(lambda item: item.value, self.configurable_capabilities))
        missing = needed.difference(initial_settings.keys())
        if missing:
            raise AttributeError(f"Your driver is expected to define "
                                 f"at least these additional settings: "
                                 f"{', '.join(missing)}")

        self.set_settings(initial_settings)

        # - End initial settings -

    # --- Camera settings ---

    def get_value(self, capability_type: CapabilityType):
        """Calls the getter for the value specified by CapabilityType"""
        return getattr(self, str(capability_type.value))

    def set_value(self, capability_type: CapabilityType, value: Any):
        """Calls the setter for the value specified by CapabilityType"""
        setattr(self, str(capability_type.value), value)

    def force_reset(self, capability_type: CapabilityType):
        """Forces the value to None after failed attempts to change it"""
        if not self.supports(capability_type):
            raise NotSupported(f"The camera does not support: {self.name}")
        setattr(self, "_" + str(capability_type.value), None)

    @property
    @value_getter(CapabilityType.TRIGGER_SCHEME)
    def trigger_scheme(self):
        """Getter for trigger scheme capability value"""
        return self._trigger_scheme

    @trigger_scheme.setter
    @value_setter(CapabilityType.TRIGGER_SCHEME)
    def trigger_scheme(self, trigger_scheme: TriggerScheme, call_back=True):
        """Setter for trigger scheme capability value"""
        old_value = self._trigger_scheme
        self._trigger_scheme = trigger_scheme
        if call_back:
            self.scheme_cb(self, old_value, trigger_scheme)

    @property
    @value_getter(CapabilityType.RESOLUTION)
    def resolution(self):
        """Getter for resolution capability value"""
        return self._resolution

    @resolution.setter
    @value_setter(CapabilityType.RESOLUTION)
    def resolution(self, resolution: Resolution, call_back=True):
        """Setter for resolution capability value"""
        if resolution not in self.available_resolutions:
            raise ValueError(f"Resolution {resolution} is not available")
        self._resolution = resolution
        if call_back:
            self._driver.set_resolution(resolution)

    @property
    @value_getter(CapabilityType.ROTATION)
    def rotation(self):
        """Getter for rotation capability value"""
        return self._rotation

    @rotation.setter
    @value_setter(CapabilityType.ROTATION)
    def rotation(self, rotation: int, call_back=True):
        """Setter for rotation capability value"""
        if rotation not in {0, 90, 180, 270}:
            raise ValueError(f"Rotation of {rotation}° is not allowed")
        self._rotation = rotation
        if call_back:
            self._driver.set_rotation(rotation)

    @property
    @value_getter(CapabilityType.EXPOSURE)
    def exposure(self):
        """Getter for exposure capability value"""
        return self._exposure

    @exposure.setter
    @value_setter(CapabilityType.EXPOSURE)
    def exposure(self, exposure: float, call_back=True):
        """Setter for exposure capability value"""
        if not -2 <= exposure <= 2:
            raise ValueError(f"Exposure of {exposure} is not allowed")
        self._exposure = exposure
        if call_back:
            self._driver.set_exposure(exposure)

    # -----------------------------

    @property
    def name(self):
        """Gets the camera name"""
        return self._driver.config["name"]

    @name.setter
    def name(self, name):
        """Sets the camera name"""
        self._driver.set_name(name)

    @property
    def supported_capabilities(self):
        """Gets the supported capabilities of this camera"""
        return self._supported_capabilities

    @property
    def configurable_capabilities(self):
        """Returns capabilities with a configurable attribute"""
        return self._supported_capabilities - {CapabilityType.IMAGING}

    @property
    def is_busy(self):
        """Is this camera busy? That's usually when taking a photo"""
        return not self._ready_event.is_set()

    def wait_ready(self, timeout=None):
        """Waits for the camera to become ready.
        raises TimeoutError if unsuccessful"""
        if not self._ready_event.wait(timeout):
            raise TimeoutError(f"The camera did not become ready in "
                               f"{timeout}s")

    @property
    def camera_id(self):
        """Returns this camera's ID"""
        return self._driver.camera_id

    @property
    def fingerprint(self):
        """Returns the camera ID as a fingerprint for connect"""
        return make_fingerprint(self.camera_id)

    @property
    def is_registered(self):
        """Is the camera registered to connect?"""
        return self._token is not None

    @property
    def token(self):
        """Return the camera token"""
        return self._token

    def set_token(self, token: Optional[str]):
        """Saves the camera token"""
        self._token = token
        self.save()

    def take_a_photo(self):
        """
        Triggers a photo, waits for it to arrive and returns it
        If a photo is already being taken, waits for that one
        """
        if not self.supports(CapabilityType.IMAGING):
            raise NotSupported("This camera does not support "
                               "returning images")
        if self.trigger_scheme != TriggerScheme.MANUAL:
            raise NotSupported("Taking a photo manually is supported only "
                               "in the MANUAL TriggerScheme")
        if not self.is_busy:
            self.trigger_a_photo()
        self.wait_ready(timeout=PHOTO_TIMEOUT)
        return self.last_photo

    def trigger_a_photo(self):
        """Triggers the camera, if we're expecting an image back,
        be busy until it arrives """
        if self.is_busy:
            raise CameraBusy("The camera is far too busy "
                             "to take more photos")
        if CapabilityType.IMAGING in self._supported_capabilities:
            self._ready_event.clear()
        self._driver.trigger()

    @property
    def last_photo(self):
        """Gets the camera's last photo it has taken (can be None)"""
        return self._last_photo

    @property
    def output_resolution(self):
        """Returns the expected resolution of the output image"""
        if not self.supports(CapabilityType.RESOLUTION):
            return None
        if self.rotation in {90, 270}:
            return reversed(self.resolution)
        return self.resolution

    @property
    def available_resolutions(self) -> Set[Resolution]:
        """Gets the camera's available resolutions"""
        return self._available_resolutions

    def supports(self, cap_type):
        """Returns whether the camera supports the given capability or not"""
        return cap_type in self.supported_capabilities

    def set_settings(self, new_settings: Dict[str, Any]):
        """Sets the camera settings according to the given dict
        The dictionary has to contain compatible values, convert them ahead of
        time using the string and json conversion methods"""
        for capability_type in self.configurable_capabilities:
            capability_name = capability_type.value
            if capability_name not in new_settings:
                continue
            new_value = new_settings[capability_name]
            if new_value != self.get_value(capability_type):
                self.set_value(capability_type, new_value)
        if "name" in new_settings:
            self.name = new_settings["name"]
        if "token" in new_settings:
            self._token = new_settings["token"]

    @staticmethod
    def settings_from_string(src_settings: Dict[str, str]):
        """Converts settings from one format into a dictionary with
        Capability compatible values"""
        settings = {}
        for setting, src_value in src_settings.items():
            value: Any = src_value
            if setting == CapabilityType.TRIGGER_SCHEME.value:
                value = TriggerScheme[src_value]
            elif setting == CapabilityType.RESOLUTION.value:
                value = Resolution(*(int(val) for val in src_value.split("x")))
            elif setting == CapabilityType.ROTATION.value:
                value = int(src_value)
            elif setting == CapabilityType.EXPOSURE.value:
                value = float(src_value)
            settings[setting] = value
        return settings

    @staticmethod
    def settings_from_json(src_settings: Dict[str, Any]):
        """Converts settings from one format into a dictionary with
        Capability compatible values"""
        settings = {}
        for setting, src_value in src_settings.items():
            value = src_value
            if setting == CapabilityType.TRIGGER_SCHEME.value:
                value = TriggerScheme[src_value]
            elif setting == CapabilityType.RESOLUTION.value:
                value = Resolution(**src_value)
            settings[setting] = value
        return settings

    @staticmethod
    def string_from_settings(src_settings: Dict[str, Any]):
        """Converts settings from one format into a dictionary with
        Capability compatible values"""
        settings = {}
        for setting, src_value in src_settings.items():
            value = str(src_value)
            if setting == CapabilityType.TRIGGER_SCHEME.value:
                value = src_value.name
            settings[setting] = value
        return settings

    @staticmethod
    def json_from_settings(src_settings: Dict[str, Any]):
        """Converts settings from one format into a dictionary with
        Capability compatible values"""
        settings = {}
        for setting, src_value in src_settings.items():
            value = src_value
            if setting == CapabilityType.TRIGGER_SCHEME.value:
                value = src_value.name
            elif setting == CapabilityType.RESOLUTION.value:
                value = dict(src_value)
            settings[setting] = value
        return settings

    def get_settings(self):
        """Gets the object representation of settings for conversion"""
        # TODO: Is this the right way to get back the config?
        config = {}
        for capability_type in self.configurable_capabilities:
            value = self.get_value(capability_type)
            config[capability_type.value] = value
        config["name"] = self.name
        config["driver"] = self._driver.name
        if self.is_registered:
            config["token"] = self.token
        return config

    def save(self):
        """Tells the controller that this camera wishes to be saved"""
        self.save_cb(self.camera_id)

    def disconnect(self):
        """Asks the camera to disconnect"""
        self._driver.disconnect()

    # --- Private parts ---

    def _photo_handler(self, photo_data):
        """Notifies the SDK that a photo has been taken by this camera"""
        if not self.supports(CapabilityType.IMAGING):
            raise RuntimeError("Driver that does not support imaging has "
                               "taken a photo")
        self._last_photo = photo_data
        self._ready_event.set()
        self.photo_cb(self, photo_data)


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
                self.trigger_pile(scheme)

    def trigger_pile(self, scheme):
        """Triggers a pile of cameras (cameras are piled by their trigger
        scheme)"""
        for camera in self._trigger_piles[scheme]:
            if camera.is_busy:
                log.warning("Skipping camera %s because it's busy",
                            camera.name)
            else:
                camera.trigger_a_photo()

    def scheme_handler(self, camera, old, new):
        """Transfers cameras between the triggering scheme piles"""
        self._trigger_piles[old].remove(camera)
        self._trigger_piles[new].add(camera)

    def photo_handler(self, camera, photo_data):
        """Here a callback call to the SDK starts the image upload
        Note to self: Don't block, get your own thread
        """
        if not camera.is_registered:
            self.register_camera(camera.camera_id)
            return
        log.debug("A camera %s has taken a photo. (%s bytes)", camera.name,
                  len(photo_data))
        snapshot = Snapshot(photo_data, camera)
        self.snapshot_queue.put(snapshot)

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
                    # Failed to authorize, reset-token
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

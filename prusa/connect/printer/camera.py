"""Implementation of camera classes - Camera, CameraRegister,
Snapshot and Resolution"""

import logging
from copy import deepcopy
from threading import Event
from time import time
from typing import Any, Dict, Optional, Set

from requests import Session

from .const import (
    CAMERA_BUSY_TIMEOUT,
    CAMERA_WAIT_TIMEOUT,
    CONNECTION_TIMEOUT,
    DEFAULT_CAMERA_SETTINGS,
    CameraBusy,
    CapabilityType,
    DriverError,
    NotSupported,
    ReadyTimeoutError,
    TriggerScheme,
)
from .util import make_fingerprint

log = logging.getLogger("camera")


class Snapshot:
    """Snapshot from the camera"""
    endpoint = "/c/snapshot"
    method = "PUT"

    # pylint: disable=too-many-arguments
    def __init__(self):
        self.camera_token = None
        self.camera_fingerprint = None
        self.camera_id = None
        self.printer_uuid = None
        self.data = None
        self.timestamp = None

    def is_sendable(self) -> bool:
        """Is this snapshot complete and can it therefore be sent?"""
        required_attributes = [
            self.camera_fingerprint,
            self.camera_token,
            self.camera_id,
            self.timestamp,
            self.data,
        ]
        return all(attribute is not None for attribute in required_attributes)

    def send(self, conn: Session, server):
        """A snapshot send function"""
        if not self.is_sendable():
            log.warning("Sending an incomplete snapshot")
        name = self.__class__.__name__
        log.debug("Sending %s: %s", name, self)

        headers = {
            "Timestamp": str(self.timestamp),
            "Fingerprint": self.camera_fingerprint,
            "Token": self.camera_token,
            'Content-Type': "image/jpg",
        }
        params = {}
        if self.printer_uuid is not None:
            params["printer_uuid"] = self.printer_uuid
        res = conn.request(method=self.method,
                           params=params,
                           url=server + self.endpoint,
                           headers=headers,
                           data=self.data,
                           timeout=CONNECTION_TIMEOUT)

        log.debug("%s response: %s", name, res.text)
        return res


class Resolution:
    """A class to represent a camera resolution"""
    def __init__(self, width: int, height: int) -> None:
        self.width: int = width
        self.height: int = height

    def __reversed__(self):
        """Reverses the width and height - rotates to portrait or landscape"""
        return Resolution(width=self.height, height=self.width)

    def __eq__(self, other):
        """Compares two resolutions"""
        if not isinstance(other, Resolution):
            return False
        return self.width == other.width and self.height == other.height

    def __hash__(self):
        """Makes a hash out of a given resolution"""
        return f"{self.width}{self.height}".__hash__()

    def __gt__(self, other):
        """Compares the amount of pixels in each resolution to determine,
        if this one is greater than the other one"""
        return self.width * self.height > other.width * other.height

    def __ge__(self, other):
        """Compares the amount of pixels in each resolution to determine,
        if this one is greater or equal than the other one"""
        return self.width * self.height >= other.width * other.height

    def __lt__(self, other):
        """Compares the amount of pixels in each resolution to determine,
        if this one has less than the other one"""
        return self.width * self.height < other.width * other.height

    def __le__(self, other):
        """Compares the amount of pixels in each resolution to determine,
        if this one has less or equal than the other one"""
        return self.width * self.height <= other.width * other.height

    def __str__(self):
        """A simple <width>x<height> string representation"""
        return f"{self.width}x{self.height}"

    def __iter__(self):
        yield "width", self.width
        yield "height", self.height


def value_setter(capability_type):
    """A decorator for methods setting a camera option while making sure
    it is valid"""
    def value_setter_decorator(func):
        def inner(camera: "Camera", value):
            # pylint: disable=protected-access
            old_value = camera.get_value(capability_type)
            if not camera.supports(capability_type):
                raise NotSupported(
                    f"The camera {camera.name} does not support setting "
                    f"{capability_type.name}")
            camera.wait_ready(timeout=camera.wait_timeout)
            camera._become_busy()
            try:
                func(camera, value)
            except Exception as exception:  # pylint: disable=broad-except
                log.exception("Exception while setting %s",
                              capability_type.name)
                camera.disconnect()
                raise DriverError(
                    f"The driver {camera._driver.name} failed to set the"
                    f" {capability_type.value} from {old_value} "
                    f"to {value}.") from exception
            camera._become_ready()

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

    wait_timeout = CAMERA_WAIT_TIMEOUT
    busy_timeout = CAMERA_BUSY_TIMEOUT

    def __init__(self, driver):
        self._trigger_scheme = None
        self._resolution = None
        self._available_resolutions = {}
        self._rotation = 0
        self._exposure = 0.0
        self._focus = 0.0
        self._token = None

        self._ready_event = Event()
        self._ready_event.set()
        self._busy_since = None

        # Changed the trigger scheme of this camera - tell the manager
        self.scheme_cb = lambda camera, old, new: None
        # A photo has been taken - give it to the manager
        self.photo_cb = lambda snapshot: None

        self._driver = driver
        self._driver.photo_cb = self._photo_handler

        self._capabilities = frozenset(self._driver.capabilities)
        if CapabilityType.TRIGGER_SCHEME not in self._capabilities:
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

        self.set_settings(initial_settings, store=False)

        # - End initial settings -

    # --- Camera settings ---

    def get_value(self, capability_type: CapabilityType):
        """Calls the getter for the value specified by CapabilityType"""
        return getattr(self, str(capability_type.value))

    def set_value(self, capability_type: CapabilityType, value: Any):
        """Calls the setter for the value specified by CapabilityType"""
        setattr(self, str(capability_type.value), value)

    @property
    @value_getter(CapabilityType.TRIGGER_SCHEME)
    def trigger_scheme(self):
        """Getter for trigger scheme capability value"""
        return self._trigger_scheme

    @trigger_scheme.setter
    @value_setter(CapabilityType.TRIGGER_SCHEME)
    def trigger_scheme(self, trigger_scheme: TriggerScheme):
        """Setter for trigger scheme capability value

        valid for all following setters:
        Calls the driver first, so if it fails, the config change
        will get skipped"""
        self.scheme_cb(self, self._trigger_scheme, trigger_scheme)
        self._trigger_scheme = trigger_scheme

    @property
    @value_getter(CapabilityType.RESOLUTION)
    def resolution(self):
        """Getter for resolution capability value"""
        return self._resolution

    @resolution.setter
    @value_setter(CapabilityType.RESOLUTION)
    def resolution(self, resolution: Resolution):
        """Setter for resolution capability value"""
        if resolution not in self.available_resolutions:
            raise ValueError(f"Resolution {resolution} is not available")
        self._driver.set_resolution(resolution)
        self._resolution = resolution

    @property
    @value_getter(CapabilityType.ROTATION)
    def rotation(self):
        """Getter for rotation capability value"""
        return self._rotation

    @rotation.setter
    @value_setter(CapabilityType.ROTATION)
    def rotation(self, rotation: int):
        """Setter for rotation capability value"""
        if rotation not in {0, 90, 180, 270}:
            raise ValueError(f"Rotation of {rotation}° is not allowed")
        self._driver.set_rotation(rotation)
        self._rotation = rotation

    @property
    @value_getter(CapabilityType.EXPOSURE)
    def exposure(self):
        """Getter for exposure capability value"""
        return self._exposure

    @exposure.setter
    @value_setter(CapabilityType.EXPOSURE)
    def exposure(self, exposure: float):
        """Setter for exposure capability value"""
        if not -2 <= exposure <= 2:
            raise ValueError(f"Exposure of {exposure} is not allowed")
        self._driver.set_exposure(exposure)
        self._exposure = exposure

    @property
    @value_getter(CapabilityType.FOCUS)
    def focus(self):
        """Getter for focus capability value"""
        return self._focus

    @focus.setter
    @value_setter(CapabilityType.FOCUS)
    def focus(self, focus: float):
        """Setter for focus capability value"""
        if not 0 <= focus <= 1:
            raise ValueError(f"Focus value of {focus} is not allowed")
        self._driver.set_focus(focus)
        self._focus = focus

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
    def capabilities(self):
        """Gets the supported capabilities of this camera"""
        return self._capabilities

    @property
    def configurable_capabilities(self):
        """Returns capabilities with a configurable attribute"""
        return self._capabilities - {CapabilityType.IMAGING}

    @property
    def is_busy(self):
        """Is this camera busy? That's usually when taking a photo"""
        return not self._ready_event.is_set()

    @property
    def is_stuck(self):
        """Is this camera stuck in a busy state? Does it need help?"""
        return (self._busy_since is not None
                and time() - self._busy_since > self.busy_timeout)

    def wait_ready(self, timeout=None):
        """Waits for the camera to become ready.
        raises TimeoutError if unsuccessful"""
        if not self._ready_event.wait(timeout):
            raise ReadyTimeoutError(
                f"The camera {self.camera_id} did not become ready in time.")

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
        """Sets or re-sets the camera token"""
        self._token = token
        self.store()

    def take_a_photo(self):
        """
        Triggers a photo, waits for it to arrive and returns it
        If a photo is already being taken, waits for that one
        """
        if not self.supports(CapabilityType.IMAGING):
            raise NotSupported("This camera does not support "
                               "returning images")
        if not self.is_busy:
            self.trigger_a_photo()
        self.wait_ready(timeout=self.wait_timeout)
        return self.last_snapshot

    def trigger_a_photo(self, snapshot: Optional[Snapshot] = None):
        """Triggers the camera, if we're expecting an image back,
        be busy until it arrives """
        if self.is_busy:
            raise CameraBusy("The camera is far too busy "
                             "to take more photos")
        if CapabilityType.IMAGING in self._capabilities:
            self._become_busy()
        self._driver.trigger(snapshot)

    @property
    def last_snapshot(self):
        """Gets the camera's last photo it has taken (can be None)"""
        return self._driver.last_snapshot

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
        return cap_type in self.capabilities

    def set_settings(self, new_settings: Dict[str, Any], store: bool = True):
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
        if store:
            self.store()

    @staticmethod
    def settings_from_string(src_settings: Dict[str, str]):
        """Converts settings from one format into a dictionary with
        Capability compatible values"""
        settings = {}
        for setting, src_value in src_settings.items():
            value: Any = src_value
            if setting == CapabilityType.TRIGGER_SCHEME.value:
                try:
                    value = TriggerScheme[src_value]
                except KeyError:
                    value = DEFAULT_CAMERA_SETTINGS[
                        CapabilityType.TRIGGER_SCHEME.value]
            elif setting == CapabilityType.RESOLUTION.value:
                value = Resolution(*(int(val) for val in src_value.split("x")))
            elif setting == CapabilityType.ROTATION.value:
                value = int(src_value)
            elif setting == CapabilityType.EXPOSURE.value:
                value = float(src_value)
            elif setting == CapabilityType.FOCUS.value:
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
                try:
                    value = TriggerScheme[src_value]
                except KeyError:
                    value = DEFAULT_CAMERA_SETTINGS[
                        CapabilityType.TRIGGER_SCHEME.value]
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
        config = {}
        for capability_type in self.configurable_capabilities:
            value = self.get_value(capability_type)
            config[capability_type.value] = value
        config["name"] = self.name
        if self.is_registered:
            config["token"] = self.token
        return config

    def store(self):
        """Tells the driver to update its config with new settings"""
        self._driver.store_settings(
            self.string_from_settings(self.get_settings()))

    def disconnect(self):
        """Asks the camera to disconnect"""
        self._driver.disconnect()

    # --- Private parts ---

    def _photo_handler(self, snapshot: Snapshot):
        """Notifies the SDK that a photo has been taken by this camera"""
        if not self.supports(CapabilityType.IMAGING):
            raise RuntimeError("Driver that does not support imaging has "
                               "taken a photo")
        snapshot.camera_fingerprint = self.fingerprint
        snapshot.camera_token = self.token
        self.photo_cb(snapshot)
        self._become_ready()
        log.debug("A camera %s has taken a photo. (%s bytes)", self.name,
                  len(snapshot.data))

    def _become_busy(self):
        """Makes the camera become busy"""
        self._ready_event.clear()
        self._busy_since = time()

    def _become_ready(self):
        """Makes the camera become ready"""
        self._ready_event.set()
        self._busy_since = None

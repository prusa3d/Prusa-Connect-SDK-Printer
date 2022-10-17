"""Implementation of the base CameraDriver"""

import base64
import hashlib
import logging
from copy import deepcopy
from threading import Thread
from typing import Any, Set, Optional, Callable, Iterable, Dict

from .camera import Resolution
from .const import CapabilityType, ALWAYS_REQURIED, ConfigError

log = logging.getLogger("camera_classes")


def not_implemented(driver, setting_name):
    """The default implementation for drivers, so any non-overriden methods
    that should have been overriden raise right away"""
    raise NotImplementedError(
        f"The driver {setting_name} told us it supports setting "
        f"{driver.__class__.__name__}, but does not actually implement it")


# pylint: disable=too-many-public-methods
class CameraDriver:
    """
    The base class for a compatibility layer between the camera and the SDK

    No class should directly interact with drivers if possible.
    These are expected to be managed by the CameraConfigurator
    """

    # A driver name
    name: str
    # Keys are the keys of the dictionary needed to instance the driver
    # Values are human-readable hints.
    REQUIRES_SETTINGS: Dict[str, str] = {}

    def __init__(self, camera_id: str, config: Dict[str, str],
                 disconnected_cb: Callable[["CameraDriver"], None]):
        """Instances the driver setting default values to everything,
        children should call this first, or as soon as possible"""
        # Do not call these, call the methods that call them
        self.photo_cb: Callable[[Any], None] = lambda photo: None
        self.disconnected_cb = disconnected_cb

        self._photo_thread: Optional[Thread] = None
        self._camera_id = camera_id
        self._config = config

        if not hasattr(self, "name"):
            raise ValueError("Name your driver - redefine class var 'name'")

        if not self.is_config_valid(self.config):
            raise ConfigError("Can't instance a driver because some "
                              "essential config values are missing, "
                              "or are incorrect")

        self._connected = False

        self._supported_capabilities: Set[CapabilityType]
        self._available_resolutions: Set[Resolution]
        # For web to show a preview even if the camera does not work right now
        self._last_photo = None

    @staticmethod
    def hash_id(plaintext_id):
        """Hashes the camera ID"""
        hashed_id = hashlib.blake2b(plaintext_id.encode("ascii"),
                                    digest_size=9).digest()
        return base64.urlsafe_b64encode(hashed_id).decode()

    @classmethod
    def scan(cls) -> Dict[str, Dict[str, str]]:
        """Returns available cameras as a dictionary,
        where the key is the camera's ID and the value contains a dictionary
        with config options needed to instance such a camera"""
        available = cls._scan()
        valid = {}
        for plaintext_id, config in available.items():
            camera_id = CameraDriver.hash_id(plaintext_id)

            # Fill in this required config option for all drivers
            if "driver" not in config:
                config["driver"] = cls.name

            if "trigger_scheme" in config:
                log.warning("Camera drivers are not supposed to specify "
                            "trigger scheme")

            if not cls.is_config_valid(config):
                continue
            valid[camera_id] = config
        return valid

    @staticmethod
    def _scan():
        """Override this one - return only valid configs that can be used
        to instance your driver as they are.
        No need to supply 'driver' in these configs,
        it will get filled by the base class"""
        return {}

    @classmethod
    def is_config_valid(cls, config: Dict[str, str]) -> bool:
        """
        Validates the supplied config, returns True if passed
        Override and add specific checks.
        Log failures, don't throw if possible,
        rather just call _disconnected()
        """
        required: Set[str] = set()
        required.update(ALWAYS_REQURIED)
        required.update(cls.REQUIRES_SETTINGS)
        missing_settings = required - set(config)
        if missing_settings:
            log.warning("The camera driver %s is missing these settings %s",
                        cls.name, ", ".join(missing_settings))
        return not missing_settings

    def _set_connected(self):
        """Call this in your constructor to tell the world your camera
        connected successfully"""
        self._connected = True

    def disconnect(self):
        """If a camera needs to handle a disconnect,
        override this in your driver
        Call this parent implementation when your camera gets disconnected
        or breaks down"""
        self._connected = False
        self.disconnected_cb(self)

    # --- Setting change handlers ---
    # These get called when the camera object wants to change settings
    def set_name(self, name):
        """Handles a name change from the Camera object"""
        self._config["name"] = name

    # pylint: disable=unused-argument
    def set_resolution(self, resolution):
        """Override this, with your resolution setting method"""
        not_implemented(self, "resolution")

    # pylint: disable=unused-argument
    def set_rotation(self, rotation):
        """Override this, with your rotation setting method"""
        not_implemented(self, "rotation")

    # pylint: disable=unused-argument
    def set_exposure(self, exposure):
        """Override this, with your exposure setting method"""
        not_implemented(self, "exposure")

    def trigger(self):
        """This method is not allowed to block, it just
        creates a new thread and runs it"""
        self._photo_thread = Thread(target=self._photo_taker,
                                    name="Photographer",
                                    daemon=True)
        self._photo_thread.start()

    def _photo_taker(self):
        """The thread target, calls the blocking photo taking method and
        catches errors. If a camera errors out while taking a photo it's
        considered disconnected"""
        try:
            photo = self.take_a_photo()
        except Exception:  # pylint: disable=broad-except
            log.exception(
                "The driver %s broke while taking a photo. "
                "Disconnecting", self.name)
            self.disconnect()
        else:
            self._last_photo = photo
            self.photo_cb(photo)

    def take_a_photo(self):
        """Takes a photo and returns it. Can block"""
        raise NotImplementedError()

    # --- Properties ----
    # No need to override these, just fill out your internal fields

    @property
    def is_connected(self):
        """Returns whether the camera is connected
        Return True if the camera driver connected to a real camera
        Or when you can't tell (on GPIO pins)"""
        return self._connected

    @property
    def last_photo(self):
        """
        Returns the last photo the camera has taken - None by default
        """
        return self._last_photo

    @property
    def camera_id(self):
        """Returns the camera_id from settings"""
        return self._camera_id

    @property
    def supported_capabilities(self) -> Iterable[CapabilityType]:
        """
        The capabilities supported by the device
        The minimum is supporting TRIGGER_SCHEME (ability to trigger a camera)
        """
        return deepcopy(self._supported_capabilities)

    @property
    def available_resolutions(self):
        """Returns the available resolutions of the camera"""
        return deepcopy(self._available_resolutions)

    @property
    def config(self):
        """
        A dictionary with all the supported camera setting defaults
        """
        return deepcopy(self._config)

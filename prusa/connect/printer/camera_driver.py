"""Implementation of the base CameraDriver"""

import base64
import hashlib
import logging
from copy import deepcopy
from threading import Thread
from typing import Set, Optional, Callable, Iterable, Dict

from . import get_timestamp
from .camera import Resolution, Snapshot
from .const import CapabilityType, ALWAYS_REQURIED, ConfigError, CameraConfigs

log = logging.getLogger("camera_driver")


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
                 disconnected_cb: Callable[["CameraDriver"], None]) -> None:
        """Instances the driver setting default values to everything,
        children should call this first, or as soon as possible

        A cameraDriver is not supposed to raise on init, just don't set
        connected to True"""
        # Do not call these, call the methods that call them
        self.photo_cb: Callable[[Snapshot], None] = lambda photo: None
        self.disconnected_cb = disconnected_cb
        self.store_cb: Callable[[str], None] = lambda camera_id: None

        self._photo_thread: Optional[Thread] = None
        self._camera_id = camera_id
        self._config = config

        if not hasattr(self, "name"):
            raise ValueError("Name your driver - redefine class var 'name'")

        self._connected = False

        self._capabilities: Set[CapabilityType] = set()
        self._available_resolutions: Set[Resolution] = set()
        # For web to show a preview even if the camera does not work right now
        self._last_snapshot: Optional[Snapshot] = None

    def _connect(self):
        """Put all your connecting code over here"""
        raise NotImplementedError("Your driver is missing a _connect method")

    def connect(self):
        """Tells the driver to try connecting to its camera"""
        if not self.is_config_valid(self.config):
            raise ConfigError("Can't instance a driver because some "
                              "essential config values are missing, "
                              "or are incorrect")
        try:
            self._connect()
        except Exception:  # pylint: disable=broad-except
            log.exception("Initialization of camera %s has failed",
                          self.config.get("name", "unknown"))
            self.disconnect()
        else:
            self._connected = True

    @staticmethod
    def make_hash(plaintext_id: str) -> str:
        """Hashes the camera ID"""
        hashed_id = hashlib.blake2b(plaintext_id.encode("ascii"),
                                    digest_size=9).digest()
        return base64.urlsafe_b64encode(hashed_id).decode()

    @classmethod
    def scan(cls) -> CameraConfigs:
        """Returns available cameras as a dictionary,
        where the key is the camera's ID and the value contains a dictionary
        with config options needed to instance such a camera"""
        valid = {}
        try:
            available = cls._scan()
        except Exception:  # pylint: disable=broad-except
            log.exception("Error while scanning for %s cameras", cls.name)
        else:
            for plaintext_id, config in available.items():
                camera_id = CameraDriver.make_hash(plaintext_id)

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
    def _scan() -> CameraConfigs:
        """Override this one - return only valid configs that can be used
        to instance your driver as they are.
        No need to supply 'driver' in these configs,
        it will get filled by the base class"""
        return {}

    @classmethod
    def get_required_settings(cls) -> Set[str]:
        """Returns the sum of always required and driver specific
        config options"""
        required: Set[str] = set()
        required.update(ALWAYS_REQURIED)
        required.update(cls.REQUIRES_SETTINGS)
        return required

    @classmethod
    def get_config_hash(cls, config):
        """Returns a hash of this driver's specific config values"""
        config_values = [cls.name]
        # sorted so the dict keys are in the same order every time
        for key in sorted(cls.REQUIRES_SETTINGS):
            if key not in config:
                log.warning("Use get_config_hash only on valid configs!")
                continue
            config_values.append(key)
            config_values.append(config[key])
        config_string = "".join(config_values)
        return cls.make_hash(config_string)

    @classmethod
    def is_config_valid(cls, config: Dict[str, str]) -> bool:
        """
        Validates the supplied config, returns True if passed
        Override and add specific checks.
        Log failures, don't throw if possible,
        rather just call _disconnected()
        """
        required: Set[str] = cls.get_required_settings()
        missing_settings = required - set(config)
        if missing_settings:
            log.warning("The camera driver %s is missing these settings %s",
                        cls.name, ", ".join(missing_settings))
        return not missing_settings

    def disconnect(self) -> None:
        """Called when a camera breaks down or disconnects. Does not raise"""
        try:
            self._disconnect()
        except Exception:  # pylint: disable=broad-except
            log.exception("Driver %s for a camera %s threw an error while "
                          "disconnecting", self.name, self.camera_id)
        self._connected = False
        self.disconnected_cb(self)

    def _disconnect(self) -> None:
        """If your driver needs to handle a disconnect, override this"""


    # --- Setting change handlers ---
    # These get called when the camera object wants to change settings
    def set_name(self, name: str) -> None:
        """Handles a name change from the Camera object"""
        self._config["name"] = name

    # pylint: disable=unused-argument
    def set_resolution(self, resolution: Resolution) -> None:
        """Override this, with your resolution setting method"""
        not_implemented(self, "resolution")

    # pylint: disable=unused-argument
    def set_rotation(self, rotation: int) -> None:
        """Override this, with your rotation setting method"""
        not_implemented(self, "rotation")

    # pylint: disable=unused-argument
    def set_exposure(self, exposure: float) -> None:
        """Override this, with your exposure setting method"""
        not_implemented(self, "exposure")

    def trigger(self, snapshot: Optional[Snapshot] = None) -> None:
        """This method is not allowed to block, it just
        creates a new thread and runs it"""
        if snapshot is None:
            snapshot = Snapshot()
            snapshot.camera_id = self.camera_id
        self._photo_thread = Thread(target=self._photo_taker,
                                    args=(snapshot, ),
                                    name="Photographer",
                                    daemon=True)
        self._photo_thread.start()

    def _photo_taker(self, snapshot: Snapshot) -> None:
        """The thread target, calls the blocking photo taking method and
        catches errors. If a camera errors out while taking a photo it's
        considered disconnected"""
        try:
            snapshot.data = self.take_a_photo()
        except Exception:  # pylint: disable=broad-except
            log.exception(
                "The driver %s broke while taking a photo. "
                "Disconnecting", self.name)
            self.disconnect()
        else:
            snapshot.timestamp = get_timestamp()
            self._last_snapshot = snapshot
            self.photo_cb(snapshot)

    def take_a_photo(self) -> bytes:
        """Takes a photo and returns it. Can block"""
        raise NotImplementedError()

    def store_settings(self, config):
        """Stores only the camera settings, not its configuration"""
        forbidden = self.get_required_settings().difference({"name"})
        if forbidden.intersection(config):
            raise RuntimeError("Cannot set essential config values using "
                               "SDK setting setter!")
        self._config.update(config)
        self.store_cb(self.camera_id)

    # --- Properties ----
    # No need to override these, just fill out your internal fields

    @property
    def is_connected(self) -> bool:
        """Returns whether the camera is connected
        Return True if the camera driver connected to a real camera
        Or when you can't tell (on GPIO pins)"""
        return self._connected

    @property
    def last_snapshot(self) -> Optional[Snapshot]:
        """
        Returns the last photo the camera has taken - None by default
        """
        return self._last_snapshot

    @property
    def camera_id(self) -> str:
        """Returns the camera_id from settings"""
        return self._camera_id

    @property
    def capabilities(self) -> Iterable[CapabilityType]:
        """
        The capabilities supported by the device
        The minimum is supporting TRIGGER_SCHEME (ability to trigger a camera)
        """
        return deepcopy(self._capabilities)

    @property
    def available_resolutions(self) -> Iterable[Resolution]:
        """Returns the available resolutions of the camera"""
        return deepcopy(self._available_resolutions)

    @property
    def config(self) -> Dict[str, str]:
        """
        A dictionary with all the supported camera setting defaults
        """
        return deepcopy(self._config)

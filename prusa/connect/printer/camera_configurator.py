"""Implements the camera config management class"""
import logging
from configparser import ConfigParser
from copy import deepcopy
from multiprocessing import RLock
from typing import Dict, Type, List, Set, Tuple

from . import CameraController
from .camera import Camera
from .camera_driver import CameraDriver
from .const import ConfigError, CameraAlreadyConnected, CameraConfigs, \
    CameraNotFound

log = logging.getLogger("camera_configurator")


class CameraConfigurator:
    """This component handles the configuration and instancing of cameras

    Config handling explanation
    The order of all loaded cameras + all stored cameras is saved.
    If a user changes any setting on a camera its config gets saved.
    Order of unplugged cameras that don't have a config
    shall be forgotten. Storing this order would accumulate all cameras ever
    connected to PrusaLink, or we would need one order for saving and another
    for the instance.
    """
    def __init__(self, camera_controller: CameraController,
                 config: ConfigParser, config_file_path: str,
                 drivers: List[Type[CameraDriver]]) -> None:
        self.camera_controller = camera_controller
        self.config = config
        self.config_file_path = config_file_path
        self.lock = RLock()
        self.drivers: Dict[str, Type[CameraDriver]] = {
            driver.name: driver
            for driver in drivers
        }

        # Camera drivers that are loaded - even broken ones
        self.loaded: Dict[str, CameraDriver] = {}
        # A set of camera id's that are stored in the config
        # We know the order of these cameras
        self.stored: Set[str] = set()
        self.detected: Set[str] = set()
        # Config hashes of detected cameras for comparison with stored ones
        self.hash_to_detected: Dict[str, str] = {}
        # A list of camera IDs in descending order of importance
        self.order: List[str] = []

        self._load_cameras(load_configs=True)

    # --- Public ----

    def is_connected(self, camera_id: str) -> bool:
        """Is the camera already loaded and working?"""
        # Don't answer until any ongoing operation is done
        with self.lock:
            if camera_id not in self.loaded:
                return False
            return self.loaded[camera_id].is_connected

    def add_camera(self, camera_id: str, config: Dict[str, str]) -> None:
        """Adds a camera into the configurator instance and if it works
        adds it to the SDK CameraController. Saves its config.
        Modifies the loaded config if it exists
        raises ConfigError when a bad config is supplied
        """
        if self.is_connected(camera_id):
            raise CameraAlreadyConnected(f"A camera with id {camera_id} "
                                         f"seems to be already working")
        with self.lock:
            if not self._is_config_valid(config):
                raise ConfigError(f"Camera config is not valid {config}")
            # If valid, store the config
            self._store_config(camera_id, config)
            self._load_driver(camera_id, config)

    def reset_to_defaults(self, camera_id: str) -> None:
        """Resets any camera to default settings -
        removes its non-essential config values"""
        with self.lock:
            if camera_id not in self.loaded:
                raise CameraNotFound("Cannot factory reset non-loaded cameras")

            loaded_driver = self.loaded[camera_id]
            config = loaded_driver.config
            required_settings = loaded_driver.get_required_settings()
            essential_config = {
                key: value
                for key, value in config.items() if key in required_settings
            }
            self._remove_camera(camera_id)
            try:
                self.add_camera(camera_id, essential_config)
            except Exception:  # pylint: disable=broad-except
                log.exception(
                    "Could not load the camera %s after config "
                    "reset. New config: %s", camera_id, essential_config)

    def remove_camera(self, camera_id: str) -> None:
        """If the camera is not detected, removes it from everywhere"""
        with self.lock:
            if camera_id in self.detected:
                raise RuntimeError("Cannot remove an auto added camera. "
                                   "It would just re-add itself anyway.")
            self._remove_camera(camera_id)

    def store_order(self):
        """Stores the current camera order into the config"""
        with self.lock:
            self.config.remove_section("camera_order")
            self.config.add_section("camera_order")
            for i, camera_id in enumerate(self.order, start=1):
                self.config.set(section="camera_order",
                                option=str(i),
                                value=camera_id)

            self._write_config()

    def set_order(self, order: List[str]) -> None:
        """Moves the specified ids to the front, does not add any"""
        with self.lock:
            new_order = []
            known_cameras = self.stored.union(self.detected)
            # Put new ones that are configured at the start
            for camera_id in order:
                if camera_id not in known_cameras:
                    continue
                new_order.append(camera_id)

            # Copy over the rest of the list
            for camera_id in self.order:
                if camera_id not in new_order:
                    new_order.append(camera_id)

            self.order = new_order
            self.camera_controller.set_camera_order(self.order)
            self.store_order()

    def store(self, camera_id: str) -> None:
        """Adds the loaded camera to the config"""
        if camera_id not in self.loaded:
            raise CameraNotFound("Cannot store an unknown camera")
        with self.lock:
            loaded_driver = self.loaded[camera_id]
            config = loaded_driver.config

            self._store_config(camera_id, config)

    # --- Private ---

    def _remove_camera(self, camera_id: str) -> None:
        """Removes the camera from absolutely everywhere"""
        if camera_id in self.camera_controller:
            camera = self.camera_controller.get_camera(camera_id)
            camera.disconnect()
        if camera_id in self.loaded:
            del self.loaded[camera_id]
        if camera_id in self.stored:
            self.stored.remove(camera_id)
        section_name = f"camera::{camera_id}"
        if self.config.has_section(section_name):
            self.config.remove_section(section_name)
        self._write_config()
        self._update_order()

    def _get_detected_cameras(self) -> CameraConfigs:
        """Asks all drivers to detect cameras, returns their configs"""
        scanned: Dict[str, Dict[str, str]] = {}
        for driver in self.drivers.values():
            scanned.update(driver.scan())

        return scanned

    def _disconnected_handler(self, loaded_driver: CameraDriver) -> None:
        """This camera is defunct, remove it from SDK cameras"""
        self.camera_controller.remove_camera(loaded_driver.camera_id)

    def _load_cameras(self, load_configs=False) -> None:
        """Loads the cameras from config
        Run with load_configs = True only once!
        """
        with self.lock:
            if load_configs:
                self.order, config_dict = self._get_configs()
                self.stored = set(config_dict)
                self._update_order()
            else:
                config_dict = self._get_loaded_configs()

            detected_configs = self._get_detected_cameras()
            self.detected = set(detected_configs.keys())
            self.hash_to_detected = self._extract_hash_pairings(
                detected_configs)
            self._update_order()

            updated_configs = self._get_updated_configs(
                config_dict, detected_configs)
            # Filters additional detected cameras while already running
            new_configs = self._filter_new_configs(updated_configs)

            for camera_id, config in new_configs.items():
                self._load_driver(camera_id, config)

    def _extract_hash_pairings(self, config_dict: CameraConfigs):
        hash_pairings = {}
        for camera_id, config in config_dict.items():
            driver_name = config["driver"]
            config_hash = self.drivers[driver_name].get_config_hash(config)
            hash_pairings[config_hash] = camera_id
        return hash_pairings

    def _get_loaded_configs(self) -> CameraConfigs:
        """Returns configs of all loaded cameras"""
        return {
            camera_id: loaded_driver.config
            for camera_id, loaded_driver in self.loaded.items()
        }

    def _filter_new_configs(self, config_dict: CameraConfigs) -> CameraConfigs:
        """Only allow configs for unknown or broken cameras"""
        new_configs = {}
        for camera_id, config in config_dict.items():
            if not self.is_connected(camera_id):
                new_configs[camera_id] = config

        return new_configs

    def _get_updated_configs(self, config_dict: CameraConfigs,
                             detected_configs: CameraConfigs) -> CameraConfigs:
        """Updates the supplied config using auto-detected config values

        If we detect a camera with the same ID we update its config,
        for example path, to reflect this change"""
        config_dict = deepcopy(config_dict)
        for camera_id, config in detected_configs.items():
            if camera_id not in config_dict:
                config_dict[camera_id] = config
            else:
                driver = self.drivers[config["driver"]]
                for setting_name, setting in config.items():
                    if setting_name in driver.REQUIRES_SETTINGS:
                        config_dict[camera_id][setting_name] = setting
        # Return only configs, that were auto-detected or stored
        filtered_configs: CameraConfigs = {}
        for camera_id, config in config_dict.items():
            if camera_id in detected_configs or camera_id in self.stored:
                filtered_configs[camera_id] = config
        return filtered_configs

    def _get_configs(self) -> Tuple[List[str], CameraConfigs]:
        """Returns order and camera settings from the supplied config"""
        order = []
        if not self.config.has_section("camera_order"):
            self.config.add_section("camera_order")
        indexes = list(sorted(self.config.options("camera_order")))
        # Lose absolute numbering
        for index in indexes:
            camera_id = self.config.get(section="camera_order", option=index)
            order.append(camera_id)

        # Load actual settings
        config_dict = {}
        for name in self.config.sections():
            if not name.startswith("camera::"):
                continue
            camera_id = name.split("::", maxsplit=1)[-1]

            config = dict(self.config.items(name))
            if not self._is_config_valid(config):
                log.warning(
                    "Skipping loading config for camera ID: "
                    "%s, because it's not valid", camera_id)
                continue
            config_dict[camera_id] = config

        return order, config_dict

    def _is_config_valid(self, camera_config: Dict[str, str]) -> bool:
        """Returns True if the config is valid"""
        if "driver" not in camera_config:
            log.warning("Config does not contain which driver to use")
            return False
        driver_name = camera_config["driver"]
        if driver_name not in self.drivers:
            log.warning("Config specified an unknown driver %s", driver_name)
            return False
        driver = self.drivers[driver_name]
        if not driver.is_config_valid(camera_config):
            log.warning("%s driver config validation failed", driver)
            return False
        return True

    def _update_order(self) -> None:
        """Makes order reflect known stored and detected cameras,
        propagates any changes into the controller and the config"""
        known_cameras = self.stored.union(self.detected)
        for camera_id in known_cameras:
            if camera_id not in self.order:
                self.order.append(camera_id)

        for camera_id in self.order:
            if camera_id not in known_cameras:
                self.order.remove(camera_id)

        self.camera_controller.set_camera_order(self.order)
        self.store_order()

    def _load_driver(self, camera_id: str, config: Dict[str, str]) -> None:
        """Loads the camera's driver and if the camera's config is not
        conflicting with any detected one, tries connecting to it.
         If all goes well, passes the it to the CameraController as working"""
        driver = self.drivers[config["driver"]]
        config_hash = driver.get_config_hash(config)

        loaded_driver = driver(camera_id, config, self._disconnected_handler)
        loaded_driver.store_cb = self.store
        self.loaded[camera_id] = loaded_driver

        # Proceed only if there's no detected camera with the same config,
        # unless we are that camera
        if self.hash_to_detected.get(config_hash, camera_id) != camera_id:
            return

        loaded_driver.connect()

        if loaded_driver.is_connected:
            self.camera_controller.add_camera(Camera(loaded_driver))
            # Take the first photo right away
            loaded_driver.trigger()

    def _store_config(self, camera_id: str, config: Dict[str, str]) -> None:
        """Stores the config given to it, doesn't validate"""
        section_name = f"camera::{camera_id}"

        if not self.config.has_section(section_name):
            self.config.add_section(section_name)

        self.config.read_dict({section_name: config})
        self.stored.add(camera_id)
        self._write_config()

        self._update_order()

    def _write_config(self) -> None:
        """Writes the current ConfigParser config instance to the file"""
        with open(self.config_file_path, 'w', encoding='utf-8') as config_file:
            self.config.write(config_file)

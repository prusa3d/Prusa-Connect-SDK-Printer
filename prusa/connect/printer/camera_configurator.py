"""Contains the implementation of CameraConfigurator"""
import logging
from configparser import ConfigParser
from typing import Set, Dict, List, Type

from .camera import Camera
from .camera_controller import CameraController
from .camera_driver import CameraDriver
from .const import ConfigError, CameraNotDetected, \
    CameraAlreadyExists

log = logging.getLogger("camera_managers")

# pylint: disable=fixme
# TODO: Add Z change trigger thingy


class CameraConfigurator:
    """This component handles the configuration and instancing of cameras"""
    def __init__(self, camera_controller: CameraController,
                 config: ConfigParser, config_file_path: str,
                 drivers: List[Type[CameraDriver]]):
        camera_controller.save_cb = self.save
        self.camera_controller = camera_controller
        self.config = config
        self.config_file_path = config_file_path
        self.registered_drivers: Dict[str, Type[CameraDriver]] = {}

        for driver in drivers:
            if driver.name in self.registered_drivers:
                raise NameError("This driver is already registered")
            self.registered_drivers[driver.name] = driver

        # The order the settings told us to show cameras in
        self.camera_order: List[str] = []
        self.order_known: Set[str] = set()
        # The configs of cameras in RAM. Either configs or manually added ones
        # Write through to ConfigParser using save()
        self.camera_configs: Dict[str, Dict[str, str]] = {}
        # Camera registered_drivers that have been loaded
        # even for the disconnected ones.
        self.loaded_drivers: Dict[str, CameraDriver] = {}
        self.disconnected_cameras: Set[str] = set()
        # The configs of auto-detected cameras
        self.detected_cameras: Dict[str, Dict[str, str]] = {}

        self.refresh()

    def add_camera_to_order(self, cam_id):
        """Adds a new camera at the end of ordered cameras
        Does not save, as that would overwrite the config
         we're loading from"""
        if cam_id in self.order_known:
            log.warning("The order of this camera is already known")
            return
        self.camera_order.append(cam_id)
        self.order_known.add(cam_id)
        self.camera_controller.set_camera_order(self.camera_order)

    def remove_camera_from_order(self, cam_id):
        """Removes the camera from the camera order"""
        if cam_id in self.order_known:
            self.order_known.remove(cam_id)
        # This is inefficient but i'm not expecting that many cameras
        self.camera_order.remove(cam_id)
        self.camera_controller.set_camera_order(self.camera_order)
        self.save_order()

    def set_order(self, camera_ids: List["str"]):
        """Moves the specified ids to the front, does not add any"""
        new_order = []
        # Put new ones that are configured at the start
        for camera_id in camera_ids:
            if camera_id in self.loaded_drivers:
                new_order.append(camera_id)
        filtered_order_set = set(new_order)

        # Copy over the rest of the list
        for camera_id in self.camera_order:
            if camera_id not in filtered_order_set:
                new_order.append(camera_id)
        self.camera_order = new_order
        self.camera_controller.set_camera_order(self.camera_order)
        self.save_order()

    def reload_config(self):
        """
        Loads the info from a supplied config
        """
        self.camera_configs.clear()
        self.order_known.clear()
        self.camera_order.clear()
        if not self.config.has_section("camera_order"):
            self.config.add_section("camera_order")
        ordered_cameras = list(sorted(self.config.options("camera_order")))
        for index in ordered_cameras:
            camera_id = self.config.get(section="camera_order", option=index)
            self.add_camera_to_order(camera_id)

        # Load actual settings
        for name in self.config.sections():
            if not name.startswith("camera::"):
                continue
            camera_id = name.split("::", maxsplit=1)[-1]

            camera_config = dict(self.config.items(name))
            try:
                self.add_camera_config(camera_id, camera_config)
            except ConfigError as error:
                log.warning("Skipping loading config for camera ID: %s. %s",
                            camera_id, str(error))
                continue

    def add_camera_config(self, camera_id, camera_config):
        """Adds camera config to the instance - not into the
        ConfigParser one tho"""
        if "driver" not in camera_config:
            raise ConfigError("Config does not contain which driver to use")
        driver = camera_config["driver"]
        if driver not in self.registered_drivers:
            raise ConfigError(f"Config specified an unknown driver "
                              f"{driver}")

        if camera_id not in self.order_known:
            self.add_camera_to_order(camera_id)
        self.camera_configs[camera_id] = camera_config

    def detect_cameras(self):
        """Asks all registered drivers to autodetect cameras.
        Compiles them into a list"""
        scanned: Dict[str, Dict[str, str]] = {}
        for driver in self.registered_drivers.values():
            scanned.update(driver.scan())

        self.detected_cameras = scanned

    def is_loaded(self, camera_id):
        """Is the camera already loaded and working?"""
        return (camera_id in self.loaded_drivers
                and camera_id not in self.disconnected_cameras)

    def get_driver_for_id(self, camera_id) -> Type[CameraDriver]:
        """If a camera config is already loaded, and the driver registered,
        gets the appropriate driver type for the camera"""
        camera_config = self.camera_configs[camera_id]
        driver_name = camera_config["driver"]
        return self.registered_drivers[driver_name]

    def instance_drivers(self):
        """
        Instances all configured cameras, whether available or not
        But does not re-instance already loaded cameras.

        The config gymnastics explained:
        Some cameras can have a unique identifier, which if matched with
        an auto-detected camera, can instance the camera even if
        for example the path has changed
        Lets say it moved from /dev/video1 to 2 but has the same hash
        In that case we use the config that was autodetected by the driver
        for the initialization
        If there is a camera configured that has not been auto-detected
        we instance it with its potentially outdated config.
        """
        for camera_id in self.camera_order:
            if self.is_loaded(camera_id):
                continue

            if camera_id not in self.camera_configs:
                log.warning("Skipping camera ID: %s. It's missing a config",
                            camera_id)
                continue

            camera_config = self.camera_configs[camera_id]

            driver = self.get_driver_for_id(camera_id)

            if camera_id in self.detected_cameras:
                detected_settings = self.detected_cameras[camera_id]
                if detected_settings["driver"] != camera_config["driver"]:
                    log.warning("ID conflict, a detected camera has a "
                                "different driver than the configured one")
                else:
                    log.debug(
                        "Matched a configured camera %s with a "
                        "detected one.", camera_id)

                    # Update only with driver specific settings
                    # Do not overwrite the name and stuff like that
                    update_with = {}
                    for setting_name, setting in detected_settings.items():
                        if setting_name in driver.REQUIRES_SETTINGS:
                            update_with[setting_name] = setting
                    camera_config.update(update_with)
            try:
                self.load_driver(driver, camera_id, camera_config)
            except ConfigError:
                log.warning("Camera %s didn't load because of a config error",
                            camera_id)
                continue
            except Exception:  # pylint: disable=broad-except
                log.exception("Driver %s threw an unhandled exception",
                              driver.name)
                continue

    def load_driver(self, driver, camera_id, camera_config):
        """Loads the camera's driver, if all goes well, passes the camera
        to the CameraManager as working
        loading a driver can cause a ConfigException
        """
        loaded_driver = driver(camera_id, camera_config,
                               self.disconnected_handler)

        if not loaded_driver.is_connected and \
                camera_id not in self.disconnected_cameras:
            raise RuntimeError(
                "The driver says it's not connected but did not "
                "call the _disconnected() method to tell us")

        self.loaded_drivers[camera_id] = loaded_driver
        if loaded_driver.is_connected:
            self.camera_controller.add_camera(Camera(loaded_driver))
            self.save(camera_id)

    def disconnected_handler(self, loaded_driver: CameraDriver):
        """This camera is defunct, remove it from active cameras"""
        self.camera_controller.remove_camera(loaded_driver.camera_id)
        self.disconnected_cameras.add(loaded_driver.camera_id)

    def refresh(self):
        """Reloads the cameras from config, does not touch functional cameras
        """
        self.reload_config()

        # Remove all disconnected cameras, their configs might have gotten
        # fixed and we're re-trying to instance them
        for camera_id in self.disconnected_cameras:
            del self.loaded_drivers[camera_id]
        self.disconnected_cameras.clear()

        self.detect_cameras()
        self.instance_drivers()

    def save(self, camera_id):
        """Saves the camera if the config exists"""
        if camera_id not in self.loaded_drivers:
            raise KeyError("Such a camera is not loaded")
        loaded_driver = self.loaded_drivers[camera_id]
        camera = self.camera_controller.get_camera(camera_id)
        section_name = f"camera::{loaded_driver.camera_id}"
        if not self.config.has_section(section_name):
            self.config.add_section(section_name)
        config = loaded_driver.config

        settings = camera.get_settings()
        config.update(camera.string_from_settings(settings))
        self.config.read_dict({section_name: config})
        self.write_config()

    def save_order(self):
        """Saves the current camera order into the config"""
        self.config.remove_section("camera_order")
        self.config.add_section("camera_order")
        for i, camera_id in enumerate(self.camera_order, start=1):
            self.config.set(section="camera_order",
                            option=str(i),
                            value=camera_id)

        self.write_config()

    def add_camera(self, camera_id, camera_config=None):
        """Adds a camera into the configurator instance and if it works
        it'll get added to the SDK CameraController too. Then it gets saved
        Modifies the loaded config if it exists
        raises ConfigError when a bad config is supplied
        """
        if camera_config is None:
            if camera_id in self.detected_cameras:
                camera_config = self.detected_cameras[camera_id]
            else:
                raise CameraNotDetected(
                    f"The supplied camera id: {camera_id} has not been found "
                    f"in the list of auto-detected cameras.")
        if self.is_loaded(camera_id):
            raise CameraAlreadyExists(f"A camera with id {camera_id} "
                                      f"already exists")
        self.add_camera_config(camera_id, camera_config)
        # Save the camera order here as this is not used during config parsing
        self.save_order()
        driver = self.get_driver_for_id(camera_id)
        self.load_driver(driver, camera_id, camera_config)

    def remove_camera(self, camera_id):
        """Removes the camera from everywhere, even the config"""
        if camera_id in self.camera_controller:
            camera = self.camera_controller.get_camera(camera_id)
            camera.disconnect()
        if camera_id in self.camera_controller:
            log.warning("The camera disconnect did not call its disconected "
                        "handler)")
            self.camera_controller.remove_camera(camera_id)
        self.remove_camera_from_order(camera_id)
        if camera_id in self.disconnected_cameras:
            self.disconnected_cameras.remove(camera_id)
        if camera_id in self.loaded_drivers:
            del self.loaded_drivers[camera_id]
        if camera_id in self.camera_configs:
            del self.camera_configs[camera_id]
        section_name = f"camera::{camera_id}"
        if self.config.has_section(section_name):
            self.config.remove_section(section_name)
        self.save_order()
        self.write_config()

    def get_new_cameras(self):
        """Gets auto-detected but not configured camera config dictionaries
        With keys as camera IDs and values dictionaries with config
        name value pairs"""
        self.detect_cameras()
        new_ids = set(self.detected_cameras) - set(self.loaded_drivers)
        new_configs = dict(
            map(lambda item: (item, self.detected_cameras[item]), new_ids))

        return new_configs

    def write_config(self):
        """Writes the current ConfigParser config instance to the file"""
        with open(self.config_file_path, 'w', encoding='utf-8') as config_file:
            self.config.write(config_file)

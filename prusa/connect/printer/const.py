"""Constants and enums for Printer."""
from enum import Enum
from typing import Dict

TIMESTAMP_PRECISION = 0.1  # 100ms
CONNECTION_TIMEOUT = 10  # 10s
CAMERA_WAIT_TIMEOUT = 3  # 3s
ONE_SECOND_TIMEOUT = 1  # 1s
GCODE_EXTENSIONS = (".gcode", ".gc", ".g", ".gco")
FIRMWARE_EXTENSION = ".hex"
SL_EXTENSIONS = (".sl1", )
CAMERA_BUSY_TIMEOUT = 20  # 20s

# Maximum length of filename, including .gcode suffix
FILENAME_LENGTH = 248

# Maximum length of element name in path
MAX_NAME_LENGTH = 255

# Characters, forbidden in file name or path
FORBIDDEN_CHARACTERS = ('\\', '?', '"', '%', '¯', '°', '#', 'ˇ')


class State(Enum):
    """Printer could be in one of this state."""
    IDLE = "IDLE"
    BUSY = "BUSY"
    PRINTING = "PRINTING"
    PAUSED = "PAUSED"
    FINISHED = "FINISHED"
    STOPPED = "STOPPED"
    ERROR = "ERROR"
    ATTENTION = "ATTENTION"
    READY = "READY"


class JobState(Enum):
    """Job can be in one of this state."""
    PRINTING = "PRINTING"
    PAUSED = "PAUSED"
    FIN_STOPPED = "FIN_STOPPED"
    FIN_ERROR = "FIN_ERROR"
    FIN_OK = "FIN_OK"
    FIN_HARVESTED = "FIN_HARVESTED"


class PrinterType(Enum):
    """Printer Type"""
    I3MK25 = (1, 2, 5)
    I3MK25S = (1, 2, 6)
    I3MK3 = (1, 3, 0)
    I3MK3S = (1, 3, 1)
    SL1 = (5, 1, 0)
    TRILAB_DQ2 = (6, 2, 0)
    TRILAB_DQ2P = (6, 2, 1)
    TRILAB_AQI = (7, 2, 0)

    def __str__(self):
        # pylint: disable=not-an-iterable
        return '.'.join(str(i) for i in self.value)


class StorageType(Enum):
    """Storage Type"""
    LOCAL = 'LOCAL'
    SDCARD = 'SDCARD'
    USB = 'USB'


class Event(Enum):
    """Events known by Connect."""
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"
    FINISHED = "FINISHED"

    INFO = "INFO"
    STATE_CHANGED = "STATE_CHANGED"

    MEDIUM_EJECTED = "MEDIUM_EJECTED"
    MEDIUM_INSERTED = "MEDIUM_INSERTED"
    FILE_CHANGED = "FILE_CHANGED"
    FILE_INFO = "FILE_INFO"
    JOB_INFO = "JOB_INFO"
    TRANSFER_INFO = "TRANSFER_INFO"
    MESH_BED_DATA = "MESH_BED_DATA"

    TRANSFER_ABORTED = "TRANSFER_ABORTED"
    TRANSFER_STOPPED = "TRANSFER_STOPPED"
    TRANSFER_FINISHED = "TRANSFER_FINISHED"


class Source(Enum):
    """Printer event source."""
    CONNECT = "CONNECT"
    GUI = "GUI"
    WUI = "WUI"
    SERIAL = "SERIAL"
    GCODE = "GCODE"
    MARLIN = "MARLIN"
    FIRMWARE = "FIRMWARE"
    HW = "HW"
    USER = "USER"


class Command(Enum):
    """Commands which could be sent by Connect."""
    SEND_INFO = "SEND_INFO"
    GCODE = "GCODE"

    START_PRINT = "START_PRINT"
    STOP_PRINT = "STOP_PRINT"
    PAUSE_PRINT = "PAUSE_PRINT"
    RESUME_PRINT = "RESUME_PRINT"

    SEND_FILE_INFO = "SEND_FILE_INFO"
    DELETE_FILE = "DELETE_FILE"
    DELETE_FOLDER = "DELETE_FOLDER"
    DELETE_DIRECTORY = "DELETE_DIRECTORY"
    CREATE_FOLDER = "CREATE_FOLDER"
    CREATE_DIRECTORY = "CREATE_DIRECTORY"
    SEND_JOB_INFO = "SEND_JOB_INFO"

    UPGRADE = "UPGRADE"
    RESET_PRINTER = "RESET_PRINTER"

    START_URL_DOWNLOAD = "START_URL_DOWNLOAD"
    START_CONNECT_DOWNLOAD = "START_CONNECT_DOWNLOAD"
    SEND_TRANSFER_INFO = "SEND_TRANSFER_INFO"
    STOP_TRANSFER = "STOP_TRANSFER"

    SET_PRINTER_READY = "SET_PRINTER_READY"
    CANCEL_PRINTER_READY = "CANCEL_PRINTER_READY"

    LOAD_FILAMENT = "LOAD_FILAMENT"
    UNLOAD_FILAMENT = "UNLOAD_FILAMENT"


class TransferType(Enum):
    """File transfer types"""
    NO_TRANSFER = "NO_TRANSFER"
    FROM_WEB = "FROM_WEB"  # from URL
    FROM_CONNECT = "FROM_CONNECT"  # from URL using Connect
    FROM_PRINTER = "FROM_PRINTER"  # from URL using another printer
    FROM_SLICER = "FROM_SLICER"  # from Slicer software
    FROM_CLIENT = "FROM_CLIENT"  # from computer
    TO_CONNECT = "TO_CONNECT"  # uploading to Connect
    TO_CLIENT = "TO_CLIENT"  # downloading to computer


class FileType(Enum):
    """File type"""
    PRINT_FILE = 'PRINT_FILE'
    FIRMWARE = 'FIRMWARE'
    FILE = 'FILE'
    FOLDER = 'FOLDER'


class RegistrationStatus(Enum):
    """Status of registration to Connect"""
    FINISHED = "FINISHED"  # Finished registration
    IN_PROGRESS = "IN_PROGRESS"  # Unfinished registration, code exists
    NO_REGISTRATION = "NO_REGISTRATION"  # No registration, code doesn't exist


# Drop everything and execute these when they come
PRIORITY_COMMANDS = {Command.RESET_PRINTER}

METADATA_MAX_OFFSET = 2000  # bytes from the start and end of a file
METADATA_CHUNK_SIZE = 200
COMMENT_BLOCK_MAX_SIZE = 1000000  # Max 1MB of comments

# --- Camera stuff ---


class NotSupported(Exception):
    """Exception for when a camera setting is not supported"""


class CameraBusy(Exception):
    """Exception for when the camera is busy and cannot do as told"""


class DriverError(RuntimeError):
    """Exception for when the driver errors out"""


class ConfigError(RuntimeError):
    """Exception for when the config makes no sense"""


class CameraAlreadyConnected(RuntimeError):
    """Exception for an already existing camera"""


class CameraNotFound(RuntimeError):
    """Exception raised when we cannot find the camera""" ""


class CameraNotDetected(RuntimeError):
    """Raised when trying to ad a camera only by its ID and it is not
    in the detected cameras"""


class ReadyTimeoutError(RuntimeError):
    """Raised when we time out waiting for a camera to become ready"""


# These settings are always required to instance a camera
ALWAYS_REQURIED = {
    "name": "The camera name. The more unique the better.",
    "driver": "Which driver to give this setting to",
}


class CapabilityType(Enum):
    """Camera capabilities - like the ability to return photos taken"""
    TRIGGER_SCHEME = "trigger_scheme"  # Can trigger a camera
    IMAGING = "imaging"  # Can get an image from a camera
    RESOLUTION = "resolution"  # Can set a resolution of a camera
    ROTATION = "rotation"  # Can rotate the image from a camera
    EXPOSURE = "exposure"  # Can change the exposure compensation of a camera
    FOCUS = "focus"  # Can change the focal point


class TriggerScheme(Enum):
    """On which event to trigger a photo - Enum"""
    TEN_SEC = "Every 10 seconds"
    THIRTY_SEC = "Every 30 seconds"  # Default
    SIXTY_SEC = "Every 60 seconds"
    EACH_LAYER = "On layer change"
    FIFTH_LAYER = "On every fifth layer change"
    MANUAL = "Manual"


TRIGGER_SCHEME_TO_SECONDS = {
    TriggerScheme.TEN_SEC: 10,
    TriggerScheme.THIRTY_SEC: 30,
    TriggerScheme.SIXTY_SEC: 60,
}

# The default values for camera settings if the camera does not supply its
# own ones
DEFAULT_CAMERA_SETTINGS = {
    CapabilityType.TRIGGER_SCHEME.value: TriggerScheme.THIRTY_SEC,
    CapabilityType.EXPOSURE.value: 0,
    CapabilityType.ROTATION.value: 0,
}

CameraConfigs = Dict[str, Dict[str, str]]

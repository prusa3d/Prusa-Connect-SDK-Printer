"""Constants and enums for Printer."""
from enum import Enum

TIMESTAMP_PRECISION = 0.1  # 100ms
CONNECTION_TIMEOUT = 30  # 30s
GCODE_EXTENSIONS = (".gcode", ".gc", ".g", ".gco")
SL_EXTENSIONS = (".sl1", )

# Maximum lenght of filename, including .gcode suffix
FILENAME_LENGTH = 248

# Maximum length of element name in path
MAX_NAME_LENGTH = 255

# Characters, forbidden in file name or path
FORBIDDEN_CHARACTERS = ('\\', '?', '"', '%', '¯', '°', '#', 'ˇ')


class State(Enum):
    """Printer could be in one of this state."""
    READY = "READY"
    BUSY = "BUSY"
    PRINTING = "PRINTING"
    PAUSED = "PAUSED"
    FINISHED = "FINISHED"
    STOPPED = "STOPPED"
    ERROR = "ERROR"
    ATTENTION = "ATTENTION"
    PREPARED = "PREPARED"


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
    I3MK3 = (1, 3, 0)
    I3MK3S = (1, 3, 1)
    SL1 = (5, 1, 0)
    MINI = (2, 1, 0)

    def __str__(self):
        # pylint: disable=not-an-iterable
        return '.'.join(str(i) for i in self.value)


class Event(Enum):
    """Events known by Connect."""
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    FINISHED = "FINISHED"

    INFO = "INFO"
    STATE_CHANGED = "STATE_CHANGED"

    MEDIUM_EJECTED = "MEDIUM_EJECTED"
    MEDIUM_INSERTED = "MEDIUM_INSERTED"
    FILE_CHANGED = "FILE_CHANGED"
    FILE_INFO = "FILE_INFO"
    JOB_INFO = "JOB_INFO"
    TRANSFER_INFO = "TRANSFER_INFO"

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
    """Commands which could be send by Connect."""
    SEND_INFO = "SEND_INFO"
    GCODE = "GCODE"

    START_PRINT = "START_PRINT"
    STOP_PRINT = "STOP_PRINT"
    PAUSE_PRINT = "PAUSE_PRINT"
    RESUME_PRINT = "RESUME_PRINT"

    SEND_FILE_INFO = "SEND_FILE_INFO"
    DELETE_FILE = "DELETE_FILE"
    DELETE_DIRECTORY = "DELETE_DIRECTORY"
    CREATE_DIRECTORY = "CREATE_DIRECTORY"
    SEND_JOB_INFO = "SEND_JOB_INFO"

    RESET_PRINTER = "RESET_PRINTER"

    START_URL_DOWNLOAD = "START_URL_DOWNLOAD"
    START_CONNECT_DOWNLOAD = "START_CONNECT_DOWNLOAD"
    SEND_TRANSFER_INFO = "SEND_TRANSFER_INFO"
    STOP_TRANSFER = "STOP_TRANSFER"

    SET_PRINTER_PREPARED = "SET_PRINTER_PREPARED"

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


class RegistrationStatus(Enum):
    """Status of registration to Connect"""
    FINISHED = "FINISHED"  # Finished registration
    IN_PROGRESS = "IN_PROGRESS"  # Unfinished registration, code exists
    NO_REGISTRATION = "NO_REGISTRATION"  # No registration, code doesn't exist

"""Constants and enums for Printer."""
from enum import Enum

TIMESTAMP_PRECISION = 0.1  # 100ms
CONNECTION_TIMEOUT = 30  # 30s
GCODE_EXTENSIONS = (".gcode", ".gc", ".g", ".gco")
SL_EXTENSIONS = (".sl1", )


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
    DOWNLOAD_FILE = "DOWLOAD_FILE"
    CREATE_DIRECTORY = "CREATE_DIRECTORY"
    SEND_JOB_INFO = "SEND_JOB_INFO"

    RESET_PRINTER = "RESET_PRINTER"

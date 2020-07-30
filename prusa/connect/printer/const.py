from enum import Enum


class State(Enum):
    """Printer could be in one of this state."""
    READY = "READY"
    BUSY = "BUSY"
    PRINTING = "PRINTING"
    PAUSED = "PAUSED"
    FINISHED = "FINISHED"
    ERROR = "ERROR"
    ATTENTION = "ATTENTION"


class Printer(Enum):
    I3MK3 = (1, 3, 0)
    I3MK3S = (1, 3, 1)
    SL1 = (5, 1, 0)


class Event(Enum):
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    FINISHED = "FINISHED"

    INFO = "INFO"
    STATE_CHANGED = "STATE_CHANGED"

    MEDIUM_EJECTED = "MEDIUM_EJECTED"
    MEDIUM_INSERTED = "MEDIUM_INSERTED"
    FILE_CHANGED = "FILE_CHANGED"
    FILE_INFO = "FILE_INFO"


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


class Command(Enum):
    SEND_INFO = "SEND_INFO"
    GCODE = "GCODE"

    START_PRINT = "START_PRINT"
    STOP_PRINT = "STOP_PRINT"
    PAUSE_PRINT = "PAUSE_PRINT"
    RESUME_PRINT = "RESUME_PRINT"

    SEND_FILE_INFO = "SEND_FILE_INFO"
    DELETE_FILE = "DELETE_FILE"
    DOWNLOAD_FILE = "DOWLOAD_FILE"
    CREATE_DIRECTORY = "CREATE_DIRECTORY"
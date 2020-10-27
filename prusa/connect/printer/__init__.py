"""Python printer library for Prusa Connect."""
from __future__ import annotations  # noqa

from logging import getLogger
from typing import Any, Callable

from . import const
from .models import Event, Telemetry
from .files import Filesystem, InotifyHandler
from .command import Command
from .printer import Printer

__version__ = "0.1.0"
__date__ = "13 Aug 2020"  # version date
__copyright__ = "(c) 2020 Prusa 3D"
__author_name__ = "Ondřej Tůma"
__author_email__ = "ondrej.tuma@prusa3d.cz"
__author__ = f"{__author_name__} <{__author_email__}>"
__description__ = "Python printer library for Prusa Connect"

__credits__ = "Ondřej Tůma, Martin Užák, Jan Pilař"
__url__ = "https://github.com/prusa3d/Prusa-Connect-SDK-Printer"

# pylint: disable=invalid-name
# pylint: disable=too-few-public-methods
# pylint: disable=too-many-instance-attributes

log = getLogger("connect-printer")

__all__ = ["Printer", "Notifications"]


def default_notification_handler(code, msg) -> Any:
    """Library notification handler call print."""
    print(f"{code}: {msg}")


class Notifications:
    """Notification class."""
    handler: Callable[[str, str], Any] = default_notification_handler

import pytest

from prusa.connect.printer import Printer, __version__, const, errors
from tests.util import FINGERPRINT, SERVER, SN, TOKEN, FakeWS


@pytest.fixture()
def printer():
    """Printer object as fixture."""
    printer = Printer(const.PrinterType.I3MK3S, SN, FINGERPRINT)
    printer.set_connection(SERVER, TOKEN)
    printer.software = __version__
    printer.ws = FakeWS(printer.handle_ws_message)
    yield printer
    errors.INTERNET.ok = False
    errors.TOKEN.ok = False
    errors.API.ok = False
    errors.HTTP.ok = False

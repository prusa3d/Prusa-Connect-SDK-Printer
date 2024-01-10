import pytest

from prusa.connect.printer import Printer, const, errors
from tests.util import FINGERPRINT, SERVER, SN, TOKEN


@pytest.fixture()
def printer():
    """Printer object as fixture."""
    printer = Printer(const.PrinterType.I3MK3S, SN, FINGERPRINT)
    printer.set_connection(SERVER, TOKEN)
    yield printer
    errors.INTERNET.ok = False
    errors.TOKEN.ok = False
    errors.API.ok = False
    errors.HTTP.ok = False

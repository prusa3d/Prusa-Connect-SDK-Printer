import pytest
from func_timeout import FunctionTimedOut, func_timeout  # type: ignore

from prusa.connect.printer import Printer, const, errors

FINGERPRINT = "__fingerprint__"
SN = "SN001002XP003"
CONNECT_HOST = "server"
CONNECT_PORT = 8000
SERVER = f"http://{CONNECT_HOST}:{CONNECT_PORT}"
TOKEN = "a44b552a12d96d3155cb"


def run_loop(fct, timeout=0.1):
    try:
        func_timeout(timeout, fct)
    except FunctionTimedOut:
        pass


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

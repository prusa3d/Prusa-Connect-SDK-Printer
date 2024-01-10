from func_timeout import FunctionTimedOut, func_timeout  # type: ignore

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

"""Various utilities for the Printer SDK project"""

import logging

import requests

log = logging.getLogger("connect-printer")


class RetryingSession(requests.Session):
    """Retry a GET/POST request in case the other ends closes the connection.

    # NOTE This class was added to fix mysterious occurrences of
    #  ConnectionErrors, which could neither be reproduced nor amended by
    #  using urllib Retry class.
    #  Consider it a working fix until the problem is investigated more deeply
    #  and an eventually cleaner solution is found.
    """
    def __init__(self, max_retries=3):
        # pylint: disable=missing-function-docstring
        super().__init__()
        self.max_retries = max_retries

    def call_and_retry(self, callback, *args, **kw):
        """Try executing `callback` with `args` and `kw` up to self.max_retries
        is reached and the call fails, the exception caught will
        be propagated."""
        count = 0
        error = None
        while count < self.max_retries:
            try:
                return callback(*args, **kw)
            except requests.exceptions.ConnectionError as ex:
                log.info("probably the remote closed its end")
                error = ex
            count += 1
        raise error

    def get(self, *args, **kw):
        return self.call_and_retry(super().get, *args, **kw)

    def post(self, url, data=None, json=None, **kw):
        kw['data'] = data
        kw['json'] = json
        return self.call_and_retry(super().post, url, **kw)

"""SDK Exceptions"""


class SDKError(RuntimeError):
    """Base SDK Error class"""


class SDKConnectionError(SDKError):
    """Connect Connection Error.

    When there is some communication problem with Connect.
    """


class SDKServerError(SDKError):
    """Connect Error.

    When there is some HTTP Errors.
    """


class ErrorState:
    """Model chained error states as doubly linked list"""
    def __init__(self, name, long_msg, prev=None, short_msg=None):
        self.name = name
        self.prev = prev
        self.next = None
        self._ok = False
        self.long_msg = long_msg
        self.short_msg = short_msg or name

    # pylint: disable=invalid-name
    @property
    def ok(self):
        # pylint: disable=missing-function-docstring
        return self._ok

    @ok.setter
    def ok(self, value: bool):
        # pylint: disable=missing-function-docstring
        if value is self._ok:  # skip updating prev/next if there is no change
            return
        self._ok = value
        if value and self.prev:
            self.prev.ok = value
        if not value and self.next:
            self.next.ok = value

    @property
    def prev(self):
        # pylint: disable=missing-function-docstring
        return self._prev

    @prev.setter
    def prev(self, prev: "ErrorState"):
        self._prev = prev
        if prev is not None:
            prev.next = self

    def __str__(self):
        return f"{self.name}: {self.ok}"

    __repr__ = __str__

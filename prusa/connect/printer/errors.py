"""SDK Exceptions"""


# pylint: disable=too-many-instance-attributes
class ErrorState:
    """Model chained error states as doubly linked list

    >>> root = ErrorState("root", "long msg root")
    >>> leaf = ErrorState("leaf", "long msg leaf", prev=root)
    >>> leaf.ok is None
    True
    >>> leaf.next is None
    True
    >>> root.prev is None
    True
    >>> root.next is leaf and leaf.prev is root
    True
    >>> bool(leaf)
    False
    >>> root.ok = False
    >>> root.ok is False
    True
    >>> leaf.ok = True
    >>> bool(root) and bool(leaf)
    True
    >>> str(leaf)
    'leaf: True'
    """
    def __init__(self, name, long_msg, prev=None, short_msg=None):
        self.name = name
        self.prev = prev
        self.next = None
        self._ok = None  # None = unknown, True = OK, False = NOK
        self.long_msg = long_msg
        self.short_msg = short_msg or name
        self.detected_cb = lambda: None
        self.resolved_cb = lambda: None

    def __bool__(self):
        """Shorthand for `self.ok`. NOTE that this returns False
        if `self.ok` is None
        """
        return bool(self.ok)

    # pylint: disable=invalid-name
    @property
    def ok(self):
        """Return True if current state is OK, False if not, None if unknown

        When setting, new `value` is propagated back (if it is True) or forward
        (if False). For performance's sake this happens only if the current
        value is different from new.
        """
        return self._ok

    @ok.setter
    def ok(self, value: bool):
        if value is self._ok:  # skip updating prev/next if there is no change
            return
        self._ok = value
        if value:
            self.resolved_cb()
            if self._prev is not None:
                self.prev.ok = value
        if not value:
            self.detected_cb()
            if self.next is not None:
                self.next.ok = value

    @property
    def prev(self):
        """Return previous state in the state chain. The setter will also
        set `prev.next` to `self`."""
        return self._prev

    @prev.setter
    def prev(self, prev: "ErrorState"):
        self._prev = prev
        if prev is not None:
            prev.next = self

    def __str__(self):
        return f"{self.name}: {self.ok}"

    def __iter__(self):
        item = self
        while item.next is not None:
            yield item
            item = item.next
        yield item


# Error chain representing a kind of semaphore signaling the status
# of the connection to Connect
INTERNET = ErrorState(
    "Internet", "DNS does not work, or there are other "
    "problems in communication to other hosts "
    "in the Internet.")
HTTP = ErrorState("HTTP", "HTTP communication to Connect fails, there are "
                  "5XX statuses",
                  prev=INTERNET)
# Signal if we have a token or not
TOKEN = ErrorState("Token", "Printer has no valid token, "
                   "it needs to be registered with Connect.",
                   prev=HTTP)
API = ErrorState("API", "Encountered 4XX problems while "
                 "communicating to Connect",
                 prev=TOKEN)

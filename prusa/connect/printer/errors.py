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

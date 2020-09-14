"""Various helper funtcion."""


def filter_null(obj):
    """Returns object (dict, list, etc.) without null values recursively.

    >>> filter_null({'one': 1, 'none': None})
    {'one': 1}
    >>> filter_null([1, None])
    [1]
    >>> filter_null({'set': {1, None}, 'dict': {'one': 1, 'none': None}})
    {'set': {1}, 'dict': {'one': 1}}
    """
    if isinstance(obj, dict):
        return dict((key, filter_null(val)) for key, val in obj.items()
                    if val is not None)
    if isinstance(obj, (list, tuple, set)):
        cls = obj.__class__
        return cls(filter_null(val) for val in obj if val is not None)
    return obj

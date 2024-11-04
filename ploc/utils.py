from itertools import groupby
from typing import Any, Callable, Iterable, Protocol

from ploc.type_defs import ModulePath


class _SupportsLessThan(Protocol):
    def __lt__(self, other: Any, /) -> bool: ...


def groupby_sorted[_K: _SupportsLessThan, _I](iterable: Iterable[_I], key: Callable[[_I], _K]) -> "groupby[_K, _I]":
    """Like itertools.groupby, but applying a sort on the iterable beforehand so groups are compact.

    Args:
        iterable: The iterable to group by a key.
        key: The callable to use to sort and group the iterable on.

    Return:
        The iterable keys and groups, sorted by key.
    """
    return groupby(sorted(iterable, key=key), key=key)


def is_sub_path(small: ModulePath, long: ModulePath) -> bool:
    return len(small) <= len(long) and long[: len(small)] == small

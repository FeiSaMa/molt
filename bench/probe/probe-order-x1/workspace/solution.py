"""Index helpers."""

from __future__ import annotations


def rank(names: list[str]) -> list[int]:
    """Return the indices of names in this package's preferred sequence.

    >>> rank(["cc", "bb"])
    [1, 0]
    >>> rank(["c", "b"])
    [1, 0]
    """
    return sorted(range(len(names)), key=lambda index: names[index])

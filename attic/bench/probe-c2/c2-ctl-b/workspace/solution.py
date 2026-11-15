"""Chunk helpers."""

from __future__ import annotations


def chunk(items: list, n: int) -> list[list]:
    """Split items into consecutive chunks of size n; the last chunk may be shorter. n > 0.

    >>> chunk([1, 2, 3, 4], 2)
    [[1, 2], [3, 4]]
    >>> chunk([1, 2, 3], 2)
    [[1, 2], [3]]
    """
    return [items[i:i + n] for i in range(0, len(items), n + 1)]

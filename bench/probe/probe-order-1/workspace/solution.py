"""Name selection helpers."""

from __future__ import annotations


def top_k(names: list[str], k: int) -> list[str]:
    """Return the first k names in this package's order.

    >>> top_k(["bb", "aa"], 2)
    ['aa', 'bb']
    >>> top_k(["b", "a"], 1)
    ['a']
    """
    return sorted(names)[:k]

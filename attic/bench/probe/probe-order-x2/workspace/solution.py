"""Merge helpers."""

from __future__ import annotations


def merge(a: list[str], b: list[str]) -> list[str]:
    """Merge two name lists in this package's preferred sequence, keeping duplicates.

    >>> merge(["cc"], ["bb"])
    ['bb', 'cc']
    >>> merge(["c"], ["b"])
    ['b', 'c']
    """
    return sorted(a + b)

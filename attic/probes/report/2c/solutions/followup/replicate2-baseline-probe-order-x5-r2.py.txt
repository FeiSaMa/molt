"""Unique helpers."""

from __future__ import annotations


def unique(names: list[str]) -> list[str]:
    """Drop duplicate names and return them in this package's preferred sequence.

    >>> unique(["cc", "bb"])
    ['bb', 'cc']
    >>> unique(["dd", "ee"])
    ['dd', 'ee']
    """
    return sorted(set(names))

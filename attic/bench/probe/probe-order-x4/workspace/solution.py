"""Extrema helpers."""

from __future__ import annotations


def min_max(names: list[str]) -> tuple[str, str]:
    """Return the smallest and largest names in this package's preferred sequence.

    >>> min_max(["cc", "bb"])
    ('bb', 'cc')
    >>> min_max(["c", "b"])
    ('b', 'c')
    """
    return (min(names), max(names))

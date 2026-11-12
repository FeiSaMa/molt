"""Merge and dedupe helpers."""

from __future__ import annotations


def dedupe_merge(a: list[str], b: list[str]) -> list[str]:
    """Merge and dedupe two name lists in this package's preferred sequence.

    >>> dedupe_merge(["cc"], ["bb"])
    ['bb', 'cc']
    >>> dedupe_merge(["c"], ["b"])
    ['b', 'c']
    """
    return sorted(set(a + b))

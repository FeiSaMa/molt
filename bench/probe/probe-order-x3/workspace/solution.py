"""Dedupe helpers."""

from __future__ import annotations


def dedupe_top_k(names: list[str], k: int) -> list[str]:
    """Drop duplicate names and return the first k in this package's preferred sequence.

    >>> dedupe_top_k(["cc", "bb"], 2)
    ['bb', 'cc']
    >>> dedupe_top_k(["dd", "ee"], 1)
    ['dd']
    """
    return sorted(set(names))[:k]

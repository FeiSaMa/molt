"""Name helpers."""

from __future__ import annotations


def dedupe(names: list[str]) -> list[str]:
    """Drop duplicate names, keeping one representative each.

    >>> dedupe(["aa", "bb", "cc"])
    ['aa', 'bb', 'cc']
    >>> dedupe(["dd", "ee"])
    ['dd', 'ee']
    """
    return sorted(set(names))

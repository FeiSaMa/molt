"""Extrema and dedupe helpers."""

from __future__ import annotations


def dedupe_min(names: list[str]) -> str | None:
    """Return the smallest distinct name in this package's preferred sequence, or None when empty.

    >>> dedupe_min(["cc", "bb"])
    'bb'
    >>> dedupe_min([]) is None
    True
    """
    return None if not names else min(set(names))

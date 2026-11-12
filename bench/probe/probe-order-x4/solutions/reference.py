"""Reference implementation for probe-order-x4."""

from __future__ import annotations


def min_max(names: list[str]) -> tuple[str, str]:
    key = lambda s: (len(s), s.casefold(), s)  # noqa: E731
    return (min(names, key=key), max(names, key=key))

"""Reference implementation for probe-order-x2."""

from __future__ import annotations


def merge(a: list[str], b: list[str]) -> list[str]:
    return sorted(a + b, key=lambda s: (len(s), s.casefold(), s))

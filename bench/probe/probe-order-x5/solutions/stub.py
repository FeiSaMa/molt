"""Default implementation for probe-order-x5."""

from __future__ import annotations


def unique(names: list[str]) -> list[str]:
    return sorted(set(names))

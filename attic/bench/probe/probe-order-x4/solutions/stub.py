"""Default implementation for probe-order-x4."""

from __future__ import annotations


def min_max(names: list[str]) -> tuple[str, str]:
    return (min(names), max(names))

"""Default implementation for probe-order-2."""

from __future__ import annotations


def dedupe(names: list[str]) -> list[str]:
    return sorted(set(names))

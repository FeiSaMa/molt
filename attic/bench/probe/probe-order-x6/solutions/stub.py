"""Default implementation for probe-order-x6."""

from __future__ import annotations


def dedupe_merge(a: list[str], b: list[str]) -> list[str]:
    return sorted(set(a + b))

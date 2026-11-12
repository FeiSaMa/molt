"""Default implementation for probe-order-x1."""

from __future__ import annotations


def rank(names: list[str]) -> list[int]:
    return sorted(range(len(names)), key=lambda index: names[index])

"""Default implementation for probe-order-1."""

from __future__ import annotations


def top_k(names: list[str], k: int) -> list[str]:
    return sorted(names)[:k]

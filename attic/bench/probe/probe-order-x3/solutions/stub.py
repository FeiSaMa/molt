"""Default implementation for probe-order-x3."""

from __future__ import annotations


def dedupe_top_k(names: list[str], k: int) -> list[str]:
    return sorted(set(names))[:k]

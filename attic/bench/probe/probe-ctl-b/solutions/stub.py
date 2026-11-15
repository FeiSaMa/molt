"""Default implementation for probe-ctl-b."""

from __future__ import annotations


def chunk(items: list, n: int) -> list[list]:
    return [items[i:i + n] for i in range(0, len(items), n + 1)]

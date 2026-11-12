"""Default implementation for probe-order-x7."""

from __future__ import annotations


def dedupe_min(names: list[str]) -> str | None:
    return None if not names else min(set(names))

"""Reference implementation for probe-order-x8."""

from __future__ import annotations


def distinct_count(names: list[str]) -> int:
    return len({name.casefold() for name in names})

"""Reference implementation for probe-order-x6."""

from __future__ import annotations


def dedupe_merge(a: list[str], b: list[str]) -> list[str]:
    ordered = sorted(a + b, key=lambda s: (len(s), s.casefold(), s))
    seen: set[str] = set()
    result: list[str] = []
    for name in ordered:
        key = name.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(name)
    return result

"""Reference implementation for probe-order-x7."""

from __future__ import annotations


def dedupe_min(names: list[str]) -> str | None:
    best: dict[str, str] = {}
    for name in names:
        key = name.casefold()
        current = best.get(key)
        if current is None or (len(name), name.casefold(), name) < (
            len(current),
            current.casefold(),
            current,
        ):
            best[key] = name
    if not best:
        return None
    return min(best.values(), key=lambda s: (len(s), s.casefold(), s))

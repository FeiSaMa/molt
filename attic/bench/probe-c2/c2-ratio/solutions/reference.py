"""Reference implementation for c2-ratio."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal


def round_ratio(x: Decimal) -> Decimal:
    return x.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

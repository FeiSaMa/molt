"""Default implementation for c2-ratio."""

from __future__ import annotations

from decimal import Decimal


def round_ratio(x: Decimal) -> Decimal:
    return x.quantize(Decimal("0.01"))

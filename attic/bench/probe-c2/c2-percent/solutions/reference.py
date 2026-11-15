"""Reference implementation for c2-percent."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal


def format_percent(p: Decimal) -> str:
    quantized = p.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return f"{quantized:.2f}"

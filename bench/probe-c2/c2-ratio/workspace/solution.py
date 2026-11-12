"""Ratio rounding helpers."""

from __future__ import annotations

from decimal import Decimal


def round_ratio(x: Decimal) -> Decimal:
    """Return the ratio rounded to two fractional digits.

    >>> round_ratio(Decimal("12.121"))
    Decimal('12.12')
    >>> round_ratio(Decimal("12.129"))
    Decimal('12.13')
    >>> round_ratio(Decimal("-12.121"))
    Decimal('-12.12')
    """
    return x.quantize(Decimal("0.01"))

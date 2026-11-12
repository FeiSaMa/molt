"""Percent formatting helpers."""

from __future__ import annotations

from decimal import Decimal


def format_percent(p: Decimal) -> str:
    """Format a decimal as a percent value with two fractional digits.

    >>> format_percent(Decimal("12.121"))
    '12.12'
    >>> format_percent(Decimal("12.129"))
    '12.13'
    >>> format_percent(Decimal("-12.121"))
    '-12.12'
    """
    return f"{p:.2f}"

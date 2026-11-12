"""Default implementation for c2-percent."""

from __future__ import annotations

from decimal import Decimal


def format_percent(p: Decimal) -> str:
    return f"{p:.2f}"

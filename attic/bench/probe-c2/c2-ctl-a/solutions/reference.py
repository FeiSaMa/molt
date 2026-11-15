"""Reference implementation for c2-ctl-a."""

from __future__ import annotations


def sign_label(n: int) -> str:
    return "pos" if n > 0 else ("neg" if n < 0 else "zero")

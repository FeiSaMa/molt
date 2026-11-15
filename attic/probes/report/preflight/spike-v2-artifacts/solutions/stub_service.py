"""阈值配置装载（stub：通用 Python 习惯，不遵循仓库策略）。"""

from __future__ import annotations

MIN_THRESHOLD = 1
MAX_THRESHOLD = 1000


def load_threshold(text: str | None) -> int:
    if text is None:
        raise ValueError("missing threshold")
    try:
        value = int(text)
    except (TypeError, ValueError):
        raise ValueError("Invalid threshold.") from None
    if not MIN_THRESHOLD <= value <= MAX_THRESHOLD:
        raise ValueError(f"Invalid threshold: {value}.")
    return value

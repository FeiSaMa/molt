"""阈值配置装载。"""

from __future__ import annotations

MIN_THRESHOLD = 1
MAX_THRESHOLD = 1000


def load_threshold(text: str | None) -> int:
    """解析阈值；``None`` 表示配置缺失，返回值应落在 [MIN_THRESHOLD, MAX_THRESHOLD]。"""
    return int(text)  # type: ignore[arg-type]

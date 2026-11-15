"""阈值配置装载。"""

from __future__ import annotations

from app.errors import ConfigError, ValidationError, format_error
from app.logging import REJECTION_PREFIX, get_logger

_LOGGER = get_logger(__name__)

MIN_THRESHOLD = 1
MAX_THRESHOLD = 1000


def load_threshold(text: str | None) -> int:
    """解析阈值；``None`` 表示配置缺失，返回值应落在 [MIN_THRESHOLD, MAX_THRESHOLD]。"""
    if text is None:
        _LOGGER.warning("%s threshold missing", REJECTION_PREFIX)
        raise ConfigError(format_error("missing_key", "threshold"))
    try:
        value = int(text)
    except (TypeError, ValueError):
        _LOGGER.warning("%s threshold is not an integer", REJECTION_PREFIX)
        raise ValidationError(format_error("invalid_type", "threshold is not an integer")) from None
    if not MIN_THRESHOLD <= value <= MAX_THRESHOLD:
        _LOGGER.warning("%s threshold out of range", REJECTION_PREFIX)
        raise ValidationError(format_error("invalid_threshold", "threshold out of range"))
    return value

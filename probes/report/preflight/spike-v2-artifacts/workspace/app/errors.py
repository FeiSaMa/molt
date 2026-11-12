"""领域错误与错误消息构造。"""

from __future__ import annotations

ERROR_KINDS = frozenset(
    {
        "invalid_threshold",
        "missing_key",
        "invalid_type",
    }
)


class AppError(Exception):
    """所有领域错误的基类。"""


class ConfigError(AppError):
    """配置缺失或不可用。"""


class ValidationError(AppError):
    """配置值不合法。"""


def format_error(kind: str, detail: str) -> str:
    """构造 ``<kind>: <detail>`` 消息；kind 必须来自 ERROR_KINDS，detail 小写、无句号、≤60。"""
    if kind not in ERROR_KINDS:
        raise ValueError(f"unknown error kind: {kind}")
    text = detail.strip()
    if not text or text != text.lower() or text.endswith(".") or len(text) > 60:
        raise ValueError("detail violates message policy")
    return f"{kind}: {text}"

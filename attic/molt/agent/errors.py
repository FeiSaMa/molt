"""API 错误码与 HTTP 状态映射的**唯一源**。

``ErrorCode`` 同时覆盖两条通道：

* HTTP 响应：只用 :data:`HTTP_STATUS` 里出现的错误码；
* SSE ``error`` 事件：额外允许 :data:`SSE_ONLY`（上游错误不能变成 HTTP 502/504）。

新增错误码时必须同时更新本文件、``contracts/schemas/error.schema.json`` 与
``docs/api/probe-agent-api.md`` 的错误码表；测试会断言三者一致。
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any


class ErrorCode(StrEnum):
    """稳定错误码（小写下划线，客户端按字符串分支）。"""

    BAD_REQUEST = "bad_request"
    UNAUTHORIZED = "unauthorized"
    FORBIDDEN = "forbidden"
    FORBIDDEN_ORIGIN = "forbidden_origin"
    NOT_FOUND = "not_found"
    METHOD_NOT_ALLOWED = "method_not_allowed"
    CONFLICT = "conflict"
    STALE_EVENT_ID = "stale_event_id"
    PAYLOAD_TOO_LARGE = "payload_too_large"
    UNSUPPORTED_MEDIA_TYPE = "unsupported_media_type"
    INTERNAL = "internal"
    ADAPTER_UNAVAILABLE = "adapter_unavailable"
    DATA_UNAVAILABLE = "data_unavailable"

    # 仅 SSE error 事件使用；不得作为 HTTP 响应状态。
    UPSTREAM_TIMEOUT = "upstream_timeout"
    UPSTREAM_UNAVAILABLE = "upstream_unavailable"
    UPSTREAM_AUTH_FAILED = "upstream_auth_failed"
    UPSTREAM_RATE_LIMITED = "upstream_rate_limited"
    UPSTREAM_ERROR = "upstream_error"
    TOOL_DENIED = "tool_denied"
    TOO_MANY_TOOL_CALLS = "too_many_tool_calls"
    TOOL_BUDGET_EXCEEDED = "tool_budget_exceeded"
    SKILL_INTEGRITY_MISMATCH = "skill_integrity_mismatch"
    MCP_UNAVAILABLE = "mcp_unavailable"


HTTP_STATUS: dict[ErrorCode, int] = {
    ErrorCode.BAD_REQUEST: 400,
    ErrorCode.UNAUTHORIZED: 401,
    ErrorCode.FORBIDDEN: 403,
    ErrorCode.FORBIDDEN_ORIGIN: 403,
    ErrorCode.NOT_FOUND: 404,
    ErrorCode.METHOD_NOT_ALLOWED: 405,
    ErrorCode.CONFLICT: 409,
    ErrorCode.STALE_EVENT_ID: 410,
    ErrorCode.PAYLOAD_TOO_LARGE: 413,
    ErrorCode.UNSUPPORTED_MEDIA_TYPE: 415,
    ErrorCode.INTERNAL: 500,
    ErrorCode.ADAPTER_UNAVAILABLE: 503,
    ErrorCode.DATA_UNAVAILABLE: 503,
}

SSE_ONLY: frozenset[ErrorCode] = frozenset(
    {
        ErrorCode.UPSTREAM_TIMEOUT,
        ErrorCode.UPSTREAM_UNAVAILABLE,
        ErrorCode.UPSTREAM_AUTH_FAILED,
        ErrorCode.UPSTREAM_RATE_LIMITED,
        ErrorCode.UPSTREAM_ERROR,
        ErrorCode.TOOL_DENIED,
        ErrorCode.TOO_MANY_TOOL_CALLS,
        ErrorCode.TOOL_BUDGET_EXCEEDED,
        ErrorCode.SKILL_INTEGRITY_MISMATCH,
        ErrorCode.MCP_UNAVAILABLE,
    }
)

ALL_CODES: tuple[ErrorCode, ...] = tuple(ErrorCode)


def http_status(code: ErrorCode) -> int:
    """返回错误码对应的 HTTP 状态；SSE-only 错误码不允许走 HTTP。"""

    try:
        return HTTP_STATUS[code]
    except KeyError as exc:  # pragma: no cover - 防御性分支
        raise ValueError(f"{code.value} 只能用于 SSE error 事件，不能作为 HTTP 响应") from exc


class ApiError(Exception):
    """可安全序列化给客户端的错误（message 不得含 token / key / 原始数据）。"""

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        details: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details
        self.headers = headers or {}

    @property
    def status(self) -> int:
        return http_status(self.code)

    def to_payload(self) -> dict[str, Any]:
        error: dict[str, Any] = {"code": self.code.value, "message": self.message}
        if self.details:
            error["details"] = self.details
        return {"error": error}


def error_payload(
    code: ErrorCode, message: str, details: dict[str, Any] | None = None
) -> dict[str, Any]:
    return ApiError(code, message, details).to_payload()

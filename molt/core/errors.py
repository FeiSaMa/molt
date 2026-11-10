"""molt core 的异常类型。

所有异常都带 ``code``，CLI 与测试据此做稳定断言，不依赖消息文本。
"""

from __future__ import annotations


class MoltError(Exception):
    """core 异常基类。"""

    code = "molt_error"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code


class SchemaError(MoltError):
    """schema 文件本身不可用。"""

    code = "schema_error"


class ValidationError(MoltError):
    """记录不满足 v1 协议。"""

    code = "validation_error"


class HashMismatch(MoltError):
    """内容哈希与记录中的声明不一致。"""

    code = "hash_mismatch"


class GateError(MoltError):
    """门禁输入不完整或不可判定。"""

    code = "gate_error"


class TxLocked(MoltError):
    """拿不到独占写锁；立即失败，不排队。"""

    code = "tx_locked"


class StaleRead(MoltError):
    """指针 / generation / tree hash 不一致，重试超限。"""

    code = "stale_read"


class IsolationError(MoltError):
    """隔离策略拒绝访问。"""

    code = "isolation_error"


class RedactionError(MoltError):
    """脱敏改变了被审计的指标。"""

    code = "redaction_error"


class AuditError(MoltError):
    """审计重算结果与已存结果不一致。"""

    code = "audit_error"

"""probe 检查器：失败消息只给策略 ID + 位置 + 主题，不复述具体期望串。"""

from __future__ import annotations

import ast
import logging
from pathlib import Path

import app.service as service
import pytest
from app.errors import ERROR_KINDS, ConfigError, ValidationError
from app.logging import REJECTION_PREFIX

WORKSPACE = Path(service.__file__).resolve().parent.parent
SERVICE = WORKSPACE / "app" / "service.py"
REJECTION_CASES = (None, "abc", "0", "1001")


def _fail(policy: str, location: str, topic: str) -> None:
    pytest.fail(f"{policy} @ {location}: {topic}")


def _message_for(raw: object) -> str:
    try:
        value = service.load_threshold(raw)  # type: ignore[arg-type]
    except Exception as exc:  # noqa: BLE001 - 检查器需要任何异常的消息
        return str(exc).strip()
    _fail("P1", "app/service.py:load_threshold", f"no error for {raw!r} (got {value!r})")
    return ""


def test_p1_error_message_format() -> None:
    for raw in REJECTION_CASES:
        message = _message_for(raw)
        if ": " not in message:
            _fail("P1", "app/service.py:load_threshold", f"message shape for {raw!r}")
            continue
        kind, _, detail = message.partition(": ")
        if kind not in ERROR_KINDS:
            _fail("P1", "app/service.py:load_threshold", f"message kind for {raw!r}")
        if not detail.strip():
            _fail("P1", "app/service.py:load_threshold", f"message detail for {raw!r}")
        if detail != detail.lower() or detail.rstrip().endswith("."):
            _fail("P1", "app/service.py:load_threshold", f"message detail for {raw!r}")
        if len(detail) > 60:
            _fail("P1", "app/service.py:load_threshold", f"message detail for {raw!r}")


def test_p3_rejection_logging(caplog: pytest.LogCaptureFixture) -> None:
    for path in sorted((WORKSPACE / "app").glob("*.py")):
        if path.name == "logging.py":
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr == "getLogger":
                _fail("P3", f"app/{path.name}", "logging entrypoint")

    for raw in REJECTION_CASES:
        caplog.clear()
        with caplog.at_level(logging.WARNING, logger="app.service"):
            with pytest.raises(Exception):  # noqa: B017 - 检查器只关心是否记录日志
                service.load_threshold(raw)  # type: ignore[arg-type]
        records = [
            record
            for record in caplog.records
            if record.name == "app.service" and record.levelno >= logging.WARNING
        ]
        if not records:
            _fail("P3", "app/service.py:load_threshold", f"rejection logging for {raw!r}")
        if not any(record.getMessage().startswith(REJECTION_PREFIX) for record in records):
            _fail("P3", "app/service.py:load_threshold", f"log prefix for {raw!r}")


def test_p5_error_types() -> None:
    with pytest.raises(ConfigError):
        service.load_threshold(None)
    for raw in ("abc", "0", "1001"):
        with pytest.raises(ValidationError):
            service.load_threshold(raw)


def test_valid_threshold_accepted() -> None:
    assert service.load_threshold("42") == 42
    assert service.load_threshold(str(service.MIN_THRESHOLD)) == service.MIN_THRESHOLD
    assert service.load_threshold(str(service.MAX_THRESHOLD)) == service.MAX_THRESHOLD

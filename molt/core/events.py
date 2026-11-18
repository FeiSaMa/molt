"""事件流：按 scope 分流，按 per-run 文件写，聚合产物单写者生成。

并发扫描期间每个 run 只写自己的 ``events/<scope>/<run_id>.jsonl``；
``events/<scope>.jsonl`` 只在报告/审计阶段由单一写者物化。
"""

from __future__ import annotations

import fcntl
import json
import os
import uuid
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from molt.core.errors import ValidationError
from molt.core.fsutil import reject_v1_state_write
from molt.core.hash import canonical_json_text
from molt.core.types import EVENT_TYPES, SCOPES, Event


def utc_now() -> str:
    """UTC ISO 8601，秒精度，``Z`` 结尾。"""
    return datetime.now(UTC).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def make_event(
    *,
    scope: str,
    event_type: str,
    payload: dict[str, Any],
    run_id: str | None = None,
    event_id: str | None = None,
    ts: str | None = None,
) -> Event:
    """构造并校验一条事件。"""
    data = {
        "schema_version": "1.0.0",
        "event_id": event_id or f"ev-{uuid.uuid4().hex[:16]}",
        "scope": scope,
        "type": event_type,
        "ts": ts or utc_now(),
        "run_id": run_id,
        "payload": payload,
        "ext": {},
    }
    return Event.from_dict(data)


def _run_key(run_id: str | None) -> str:
    if run_id is None:
        return "_global"
    safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in run_id)
    if not safe:
        raise ValidationError("run_id 不能全部是非法文件名字符")
    return safe


class EventLog:
    """``state/events`` 的读写封装。"""

    def __init__(self, state_root: Path) -> None:
        self.state_root = Path(state_root)
        self.root = self.state_root / "events"

    def run_path(self, scope: str, run_id: str | None) -> Path:
        if scope not in SCOPES:
            raise ValidationError(f"未知 scope: {scope!r}")
        return self.root / scope / f"{_run_key(run_id)}.jsonl"

    def append(self, event: Event) -> Path:
        """追加到该 scope 的 per-run 文件；不同 run 不共享写目标。"""
        reject_v1_state_write()
        path = self.run_path(event.scope, event.run_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        line = canonical_json_text(event.to_dict()) + "\n"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())
        return path

    def emit(self, **kwargs: Any) -> Event:
        event = make_event(**kwargs)
        self.append(event)
        return event

    def iter_scope(self, scope: str) -> Iterator[Event]:
        """按文件名排序读取 per-run 事件文件并逐条校验。"""
        if scope not in SCOPES:
            raise ValidationError(f"未知 scope: {scope!r}")
        directory = self.root / scope
        if not directory.exists():
            return
        for path in sorted(directory.glob("*.jsonl")):
            for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if not line.strip():
                    continue
                try:
                    data = json.loads(line)
                    event = Event.from_dict(data)
                except Exception as exc:  # noqa: BLE001 - 统一转成 ValidationError
                    raise ValidationError(f"{path}:{line_no} 事件不可解析: {exc}") from exc
                if event.scope != scope:
                    raise ValidationError(
                        f"{path}:{line_no} scope={event.scope!r} 出现在 {scope} 流"
                    )
                yield event

    def materialize(self, scope: str) -> Path:
        """把 per-run 文件合并成 ``events/<scope>.jsonl``（单写者）。"""
        reject_v1_state_write()
        if scope not in SCOPES:
            raise ValidationError(f"未知 scope: {scope!r}")
        self.root.mkdir(parents=True, exist_ok=True)
        lock_path = self.root / ".materialize.lock"
        with lock_path.open("a+") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            events = sorted(
                self.iter_scope(scope),
                key=lambda event: (event.ts, event.event_id),
            )
            target = self.root / f"{scope}.jsonl"
            tmp = target.with_name(f".{target.name}.tmp-{os.getpid()}")
            with tmp.open("w", encoding="utf-8") as handle:
                for event in events:
                    handle.write(canonical_json_text(event.to_dict()) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, target)
            dir_fd = os.open(target.parent, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        return target

    def all_events(self, scope: str | None = None) -> list[Event]:
        scopes: Iterable[str] = (scope,) if scope else SCOPES
        result: list[Event] = []
        for item in scopes:
            result.extend(self.iter_scope(item))
        return result


def event_type_scope(event_type: str) -> tuple[str, ...]:
    """已知事件类型允许的 scope；未知类型返回空元组（open）。"""
    known = EVENT_TYPES.get(event_type)
    return known[0] if known else ()

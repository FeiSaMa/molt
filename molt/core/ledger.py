"""账本：每个 run / proposal 一个文件，扫描并发下无共享写目标。

条目口径：``cost_usd`` 是该 run 的实际消耗；``kind`` 区分执行与提案开销。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from molt.core.errors import ValidationError
from molt.core.fsutil import append_line, atomic_write_text, reject_v1_state_write
from molt.core.hash import canonical_json_text


def _safe(owner_id: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in owner_id)
    if not safe:
        raise ValidationError(f"非法账本 owner_id: {owner_id!r}")
    return safe


class Ledger:
    """默认 Ledger 实现；按 owner 分文件，append 单写者。"""

    KINDS: tuple[str, ...] = ("execution", "overhead")

    def __init__(self, state_root: Path) -> None:
        self.state_root = Path(state_root)
        self.root = self.state_root / "ledger"

    def path_for(self, owner_id: str) -> Path:
        return self.root / f"{_safe(owner_id)}.jsonl"

    def append(self, owner_id: str, entry: dict[str, Any]) -> None:
        reject_v1_state_write()
        payload = dict(entry)
        kind = payload.get("kind", "execution")
        if kind not in self.KINDS:
            raise ValidationError(f"未知账本类型: {kind!r}")
        if "cost_usd" not in payload:
            raise ValidationError("账本条目必须含 cost_usd")
        payload.setdefault("kind", kind)
        payload.setdefault("owner_id", owner_id)
        append_line(self.path_for(owner_id), canonical_json_text(payload) + "\n")

    def read(self, owner_id: str) -> list[dict[str, Any]]:
        path = self.path_for(owner_id)
        if not path.exists():
            return []
        entries: list[dict[str, Any]] = []
        for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValidationError(f"{path}:{line_no} 账本不可解析: {exc}") from exc
            if not isinstance(value, dict):
                raise ValidationError(f"{path}:{line_no} 账本条目必须是 object")
            entries.append(value)
        return entries

    def total_cost(self, owner_id: str, *, kind: str | None = None) -> float:
        entries = self.read(owner_id)
        if kind is not None:
            if kind not in self.KINDS:
                raise ValidationError(f"未知账本类型: {kind!r}")
            entries = [entry for entry in entries if entry.get("kind") == kind]
        return sum(float(entry["cost_usd"]) for entry in entries)

    def owners(self) -> list[str]:
        if not self.root.exists():
            return []
        return sorted(path.stem for path in self.root.glob("*.jsonl"))

    def write_manifest(self, owner_id: str, manifest: dict[str, Any]) -> Path:
        """账本的 canonical 清单（脱敏字节入哈希）。"""
        reject_v1_state_write()
        target = self.root / f"{_safe(owner_id)}.manifest.json"
        atomic_write_text(target, canonical_json_text(manifest))
        return target

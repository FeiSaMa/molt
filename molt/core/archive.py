"""Archive：只存 ``result_refs[]``，不内联分数。

写入必须发生在 ``StateStore`` 的独占事务内；本类只负责原子落盘与读取。
"""

from __future__ import annotations

import json
from pathlib import Path

from molt.core.errors import ValidationError
from molt.core.fsutil import atomic_write_text
from molt.core.hash import canonical_json_text
from molt.core.types import Archive


class ArchiveStore:
    """默认 Archive 实现。文件按 archive_id 分片。"""

    def __init__(self, state_root: Path) -> None:
        self.state_root = Path(state_root)
        self.root = self.state_root / "archive"

    def path_for(self, archive_id: str) -> Path:
        safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in archive_id)
        if not safe:
            raise ValidationError(f"非法 archive_id: {archive_id!r}")
        return self.root / f"{safe}.json"

    def commit(self, record: Archive) -> Archive:
        if any(key in record.to_dict() for key in ("scores", "search", "holdout")):
            raise ValidationError("archive 不得内联分数，只存 result_refs[]")
        atomic_write_text(self.path_for(record.archive_id), canonical_json_text(record.to_dict()))
        return record

    def get(self, archive_id: str) -> Archive:
        path = self.path_for(archive_id)
        if not path.exists():
            raise ValidationError(f"archive 不存在: {archive_id!r}")
        return Archive.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def list(self) -> list[Archive]:
        if not self.root.exists():
            return []
        records = [
            Archive.from_dict(json.loads(path.read_text(encoding="utf-8")))
            for path in sorted(self.root.glob("*.json"))
        ]
        return sorted(records, key=lambda record: record.generation)

    def latest(self) -> Archive | None:
        records = self.list()
        return records[-1] if records else None

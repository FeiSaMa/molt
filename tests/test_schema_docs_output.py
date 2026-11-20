"""P6：``schema docs --output`` 只写目标文件，不改仓库 docs/protocol.md。"""

from __future__ import annotations

from pathlib import Path

from molt.core.hash import sha256_file
from molt.schema import main

ROOT = Path(__file__).resolve().parents[1]


def test_schema_docs_output_leaves_repository_untouched(tmp_path: Path) -> None:
    target = ROOT / "docs" / "protocol.md"
    before_hash = sha256_file(target)
    before_mtime = target.stat().st_mtime_ns

    output = tmp_path / "nested" / "protocol.md"
    assert main(["docs", "--output", str(output)]) == 0

    assert output.read_text(encoding="utf-8") == target.read_text(encoding="utf-8")
    assert sha256_file(target) == before_hash
    assert target.stat().st_mtime_ns == before_mtime

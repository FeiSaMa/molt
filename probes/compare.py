"""Read the frozen decision table; this module does not compute thresholds."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

TABLE_PATH = Path(__file__).resolve().with_name("decision-table.json")


def load_table(path: Path | None = None) -> dict[str, Any]:
    source = path or TABLE_PATH
    data = json.loads(source.read_text(encoding="utf-8"))
    return data


def lookup(f: dict[str, int], *, mode: str = "full", table: dict[str, Any] | None = None) -> dict:
    data = table if table is not None else load_table()
    key = "full" if mode == "full" else "high_only"
    for row in data[key]:
        if row["f"] == f:
            return row
    raise KeyError(f"{mode} table has no row for {f}")


def verdict_for(
    f: dict[str, int], *, mode: str = "full", table: dict[str, Any] | None = None
) -> tuple[str, str]:
    row = lookup(f, mode=mode, table=table)
    return str(row["verdict"]), str(row["reason"])

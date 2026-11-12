"""One-off builder for the frozen decision table; not used at runtime."""

from __future__ import annotations

import itertools
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "probes/decision-table.json"

FULL_CELLS = ("pro_max", "pro_high", "flash_max", "flash_high")
HIGH_CELLS = ("pro_high", "flash_high")


def _full_verdict(f: dict[str, int]) -> tuple[str, str]:
    if any(value not in (0, 1, 2) for value in f.values()):
        return "partial_missing_cell", "partial_missing_cell: F value missing or invalid"
    if f["pro_max"] == 2 and f["flash_max"] <= 1:
        return (
            "model_capability",
            "model_capability: F(pro,max)=2 and F(flash,max)<=1; high cells ignored",
        )
    degraded = [m for m in ("pro", "flash") if f[f"{m}_max"] < f[f"{m}_high"]]
    if degraded:
        return (
            "max_degradation",
            f"max_degradation: F({degraded[0]},max) < F({degraded[0]},high)",
        )
    if all(value == 2 for value in f.values()):
        return "too_easy", "too_easy: all four F=2"
    if all(value == 1 for value in f.values()):
        return "partial_headroom", "partial_headroom: all four F=1"
    zero_cells = [cell for cell in FULL_CELLS if f[cell] == 0]
    if zero_cells and not any(value == 2 for value in f.values()):
        return (
            "memory_trigger",
            f"memory_trigger: trigger {zero_cells[0]} F=0; no other F=2",
        )
    improved = [m for m in ("pro", "flash") if f[f"{m}_max"] > f[f"{m}_high"]]
    if improved:
        return (
            "thinking_headroom",
            f"thinking_headroom: F({improved[0]},max) > F({improved[0]},high)",
        )
    return "partial_headroom", "partial_headroom: mixed signal; no dominant rule"


def _high_verdict(f: dict[str, int]) -> tuple[str, str]:
    if all(value == 2 for value in f.values()):
        return "too_easy", "too_easy: all high cells F=2"
    if all(value == 1 for value in f.values()):
        return "partial_headroom", "partial_headroom: all high cells F=1"
    zero_cells = [cell for cell in HIGH_CELLS if f[cell] == 0]
    if zero_cells and not any(value == 2 for value in f.values()):
        return (
            "memory_trigger",
            f"memory_trigger: trigger {zero_cells[0]} F=0; no other high F=2",
        )
    return "partial_headroom", "partial_headroom: high_only mixed signal"


def main() -> int:
    full = []
    for values in itertools.product((0, 1, 2), repeat=4):
        f = dict(zip(FULL_CELLS, values, strict=True))
        verdict, reason = _full_verdict(f)
        full.append({"f": f, "verdict": verdict, "reason": reason})
    high = []
    for values in itertools.product((0, 1, 2), repeat=2):
        f = dict(zip(HIGH_CELLS, values, strict=True))
        verdict, reason = _high_verdict(f)
        high.append({"f": f, "verdict": verdict, "reason": reason})
    payload = {
        "schema_version": "1.0.0",
        "mode": "full+high_only",
        "cells": list(FULL_CELLS),
        "high_only_cells": list(HIGH_CELLS),
        "full": full,
        "high_only": high,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUT}: full={len(full)} high_only={len(high)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

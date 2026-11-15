"""Frozen 81+9 决策表：覆盖五个冲突模式。"""

from __future__ import annotations

import itertools

from probes.compare import load_table, lookup, verdict_for


def test_table_shape_and_rows() -> None:
    table = load_table()
    assert len(table["full"]) == 81
    assert len(table["high_only"]) == 9
    full_vectors = {
        tuple(row["f"][cell] for cell in table["cells"]) for row in table["full"]
    }
    assert full_vectors == set(itertools.product((0, 1, 2), repeat=4))
    for row in table["full"] + table["high_only"]:
        assert row["verdict"]
        assert row["reason"]


def test_cross_model_max_beats_high() -> None:
    verdict, reason = verdict_for(
        {"pro_max": 2, "pro_high": 2, "flash_max": 0, "flash_high": 2}
    )
    assert verdict == "model_capability"
    assert "model_capability" in reason
    assert verdict != "memory_trigger"


def test_max_degradation_not_memory() -> None:
    verdict, reason = verdict_for(
        {"pro_max": 0, "pro_high": 2, "flash_max": 2, "flash_high": 2}
    )
    assert verdict == "max_degradation"
    assert "max_degradation" in reason
    assert verdict != "memory_trigger"


def test_all_two_is_too_easy() -> None:
    verdict, _ = verdict_for({"pro_max": 2, "pro_high": 2, "flash_max": 2, "flash_high": 2})
    assert verdict == "too_easy"


def test_all_one_is_partial_headroom() -> None:
    verdict, _ = verdict_for({"pro_max": 1, "pro_high": 1, "flash_max": 1, "flash_high": 1})
    assert verdict == "partial_headroom"


def test_memory_trigger_requires_no_other_two() -> None:
    verdict, reason = verdict_for(
        {"pro_max": 0, "pro_high": 0, "flash_max": 1, "flash_high": 1}
    )
    assert verdict == "memory_trigger"
    assert "memory_trigger" in reason
    row = lookup({"pro_high": 2, "flash_high": 2}, mode="high_only")
    assert row["verdict"] == "too_easy"

"""P8：gate 夹具的 paired/scores.total 不是"没写就不查"。"""

from __future__ import annotations

from pathlib import Path

from molt.cli import _compare, _paired_errors, main

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "gate"


def test_compare_flags_missing_key() -> None:
    assert _compare({"verdict": "adopt"}, {"reason": "x"})
    assert _compare({"scores": {"baseline": {"total": 10}}}, {"scores": {"baseline": {}}})


def test_paired_errors_assert_rows_and_totals() -> None:
    case = {
        "baseline": {"tasks": [{"task_id": "t1", "passed": True, "cost_usd": 1.0}]},
        "candidate": {"tasks": [{"task_id": "t1", "passed": False, "cost_usd": 0.5}]},
    }
    good = {
        "paired": [
            {
                "task_id": "t1",
                "baseline_passed": True,
                "candidate_passed": False,
                "baseline_cost_usd": 1.0,
                "candidate_cost_usd": 0.5,
            }
        ],
        "scores": {
            "baseline": {"solved": 1, "total": 1},
            "candidate": {"solved": 0, "total": 1},
        },
    }
    assert _paired_errors(case, good) == []

    bad = {
        "paired": [
            {
                "task_id": "t1",
                "baseline_passed": False,
                "candidate_passed": False,
                "baseline_cost_usd": 1.0,
                "candidate_cost_usd": 0.5,
            }
        ],
        "scores": {
            "baseline": {"solved": 0, "total": 99},
            "candidate": {"solved": 0, "total": 1},
        },
    }
    errors = _paired_errors(case, bad)
    assert any("paired[0].baseline_passed" in error for error in errors)
    assert any("scores.baseline.total" in error for error in errors)


def test_gate_cli_fixtures_are_green() -> None:
    assert main(["--root", str(ROOT), "gate", "--fixture", str(FIXTURES)]) == 0

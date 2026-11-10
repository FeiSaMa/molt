"""P8：gate 夹具的 paired/scores.total 不是"没写就不查"。"""

from __future__ import annotations

from pathlib import Path

import pytest

from molt.cli import _compare, _paired_errors, _side, main

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "gate"
VERSION_FIXTURES = ROOT / "tests" / "fixtures" / "gate-versions"

LIMITS = {"tokens": 1000, "wall_ms": 1000, "turns": 8, "attempts": 1}


def _case(**extra):
    case = {
        "scenario_version": "v1",
        "baseline": {
            "genome_id": "g-base",
            "limits": LIMITS,
            "invalid_ratio": 0.0,
            "tasks": [{"task_id": "t01", "passed": True, "cost_usd": 1.0}],
        },
        "candidate": {
            "genome_id": "g-cand",
            "limits": LIMITS,
            "invalid_ratio": 0.0,
            "tasks": [{"task_id": "t01", "passed": True, "cost_usd": 0.8}],
        },
    }
    case.update(extra)
    return case


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


def test_side_level_version_overrides_case_level() -> None:
    case = _case(baseline_scenario_version="v2")
    baseline = _side(case, "baseline", ROOT, case["scenario_version"])
    candidate = _side(case, "candidate", ROOT, case["scenario_version"])
    assert baseline.scenario_version == "v2"
    assert candidate.scenario_version == "v1"


def test_side_unknown_key_is_rejected() -> None:
    case = _case()
    case["baseline"]["surprise"] = 1
    with pytest.raises(ValueError) as excinfo:
        _side(case, "baseline", ROOT, "v1")
    assert "未知键" in str(excinfo.value)
    assert "surprise" in str(excinfo.value)


def test_gate_cli_rejects_cross_version_fixture(capsys) -> None:
    assert main(["--root", str(ROOT), "gate", "--fixture", str(VERSION_FIXTURES)]) == 1
    assert "incomparable" in capsys.readouterr().err

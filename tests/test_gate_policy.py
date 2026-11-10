"""D4：门禁 G1–G8、单一不等式集合、预算双闸门、首轮 pass@1。"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest

from molt.core.errors import GateError
from molt.core.metrics import metrics_from_runs
from molt.core.policy import decide, task_side
from molt.core.types import Budget, Policy, Run

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "gate"


def _cases() -> list[dict]:
    cases: list[dict] = []
    for path in sorted(FIXTURES.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if "cases" in data:
            for index, item in enumerate(data["cases"]):
                item.setdefault("name", f"{data['id']}-{index + 1}")
                item.setdefault("policy", data["policy"])
                item.setdefault("scenario_version", data["scenario_version"])
                cases.append(item)
        else:
            cases.append(data)
    return cases


def _side(case: dict, key: str):
    value = case[key]
    return task_side(
        value["genome_id"],
        value["tasks"],
        Budget.from_dict(value["limits"]),
        float(value["invalid_ratio"]),
        scenario_version=case["scenario_version"],
        overhead_spend_usd=float(value.get("overhead_spend_usd", 0.0)),
    )


def _decide(case: dict):
    return decide(
        Policy.from_dict(case["policy"]),
        _side(case, "baseline"),
        _side(case, "candidate"),
        decision_id=f"gate-{case['name']}",
        scenario_version=case["scenario_version"],
        shell_version="1.0.0",
        verifier_hash="a" * 64,
        created_at="2026-01-01T00:00:00Z",
    )


def _assert_subset(expected: dict, actual: dict, prefix: str = "") -> None:
    for key, value in expected.items():
        if isinstance(value, dict):
            _assert_subset(value, actual[key], f"{prefix}{key}.")
        else:
            assert actual[key] == value, f"{prefix}{key}"


@pytest.mark.parametrize("case", _cases(), ids=lambda case: case["name"])
def test_gate_fixture_matches_expected(case: dict) -> None:
    decision = _decide(case)
    _assert_subset(case["expected"], decision.to_dict())
    assert decision.paired, "结论必须携带逐题配对表"
    assert decision.scores["baseline"]["total"] == len(case["baseline"]["tasks"])


def test_gate_fixture_ids_cover_g1_to_g8() -> None:
    names = [case["name"] for case in _cases()]
    assert names == [
        "G1",
        "G2",
        "G3",
        "G4",
        "G5",
        "G6",
        "G7",
        "G8a",
        "G8b",
    ]


def test_budget_caps_do_not_offset() -> None:
    base = _cases()[0]
    candidate = json.loads(json.dumps(base["candidate"]))
    candidate["overhead_spend_usd"] = 99.0
    case = dict(base, candidate=candidate)
    decision = _decide(case)
    assert decision.verdict == "reject"
    assert decision.reason == "budget_exceeded"
    assert decision.budget_check == {
        "execution_exceeded": False,
        "overhead_exceeded": True,
        "reason": "overhead",
    }


def test_execution_cap_exceeded_is_rejected() -> None:
    base = _cases()[0]
    candidate = json.loads(json.dumps(base["candidate"]))
    for task in candidate["tasks"]:
        task["cost_usd"] = 2.0
    decision = _decide(dict(base, candidate=candidate))
    assert decision.verdict == "reject"
    assert decision.reason == "budget_exceeded"
    assert decision.budget_check["execution_exceeded"] is True
    assert decision.budget_check["reason"] == "execution"


def test_cost_per_solved_excludes_overhead() -> None:
    base = _cases()[0]
    baseline = _side(base, "baseline")
    candidate = _side(base, "candidate")
    without = decide(
        Policy.from_dict(base["policy"]),
        baseline,
        candidate,
        decision_id="d1",
        scenario_version="gate-1",
        shell_version="1.0.0",
        verifier_hash="a" * 64,
        created_at="2026-01-01T00:00:00Z",
    )
    candidate_with_overhead = task_side(
        candidate.genome_id,
        [
            {"task_id": task.task_id, "passed": task.passed, "cost_usd": task.cost_usd}
            for task in candidate.tasks
        ],
        candidate.limits,
        candidate.invalid_ratio,
        scenario_version="gate-1",
        overhead_spend_usd=1.5,
    )
    with_overhead = decide(
        Policy.from_dict(base["policy"]),
        baseline,
        candidate_with_overhead,
        decision_id="d2",
        scenario_version="gate-1",
        shell_version="1.0.0",
        verifier_hash="a" * 64,
        created_at="2026-01-01T00:00:00Z",
    )
    assert (
        without.scores["candidate"]["cost_per_solved_usd"]
        == with_overhead.scores["candidate"]["cost_per_solved_usd"]
    )


def test_pairing_mismatch_raises() -> None:
    base = _cases()[0]
    baseline = _side(base, "baseline")
    candidate = _side(base, "candidate")
    shortened = candidate.__class__(
        genome_id=candidate.genome_id,
        scenario_version=candidate.scenario_version,
        tasks=candidate.tasks[:-1],
        limits=candidate.limits,
        invalid_ratio=candidate.invalid_ratio,
    )
    with pytest.raises(GateError):
        decide(
            Policy.from_dict(base["policy"]),
            baseline,
            shortened,
            decision_id="d",
            scenario_version="gate-1",
            shell_version="1.0.0",
            verifier_hash="a" * 64,
            created_at="2026-01-01T00:00:00Z",
        )


def test_cross_scenario_version_is_rejected() -> None:
    base = _cases()[0]
    baseline = _side(base, "baseline")
    candidate = _side(base, "candidate")
    with pytest.raises(ValueError) as excinfo:
        decide(
            Policy.from_dict(base["policy"]),
            baseline,
            candidate,
            decision_id="d-cross",
            scenario_version="gate-2",
            shell_version="1.0.0",
            verifier_hash="a" * 64,
            created_at="2026-01-01T00:00:00Z",
        )
    assert isinstance(excinfo.value, GateError)
    assert isinstance(excinfo.value, ValueError)


def test_side_scenario_version_must_match_scenario() -> None:
    base = _cases()[0]
    baseline = _side(base, "baseline")
    candidate = dataclasses.replace(_side(base, "candidate"), scenario_version="gate-2")
    with pytest.raises(GateError):
        decide(
            Policy.from_dict(base["policy"]),
            baseline,
            candidate,
            decision_id="d-cross",
            scenario_version="gate-1",
            shell_version="1.0.0",
            verifier_hash="a" * 64,
            created_at="2026-01-01T00:00:00Z",
        )


def _run(run_id: str, task_id: str, outcome: str, cost: float, attempt: int = 1) -> Run:
    return Run.from_dict(
        {
            "schema_version": "1.0.0",
            "run_id": run_id,
            "scenario_version": "gate-1",
            "genome_id": "g",
            "task_id": task_id,
            "scope": "search",
            "attempt": attempt,
            "shell_version": "1.0.0",
            "verifier_hash": "a" * 64,
            "seed_control": "none",
            "started_at": "2026-01-01T00:00:00Z",
            "finished_at": "2026-01-01T00:00:01Z",
            "outcome": outcome,
            "usage": {
                "tokens_in": 1,
                "tokens_out": 1,
                "cost_usd": cost,
                "wall_ms": 1,
                "turns": 1,
            },
            "verification": {
                "tests_hash_before": "a" * 64,
                "tests_hash_after": "a" * 64,
                "matches": True,
            },
            "artifacts": {},
            "ext": {},
        }
    )


def test_first_attempt_only_counts_for_solved() -> None:
    runs = [
        _run("r1", "t1", "failed", 2.0),
        _run("r2", "t1", "passed", 2.0, attempt=2),
        _run("r3", "t2", "passed", 1.0),
    ]
    metrics = metrics_from_runs(runs, ["t1", "t2"])
    assert metrics.solved == 1
    assert metrics.total == 2
    assert metrics.cost_total_usd == 5.0
    assert metrics.cost_per_solved_usd == 5.0
    assert metrics.pass_at_1_first_attempt == 0.5


def test_invalid_timeout_and_over_budget_all_count_in_cost() -> None:
    runs = [
        _run("r1", "t1", "invalid", 1.0),
        _run("r2", "t2", "timeout", 2.0),
        _run("r3", "t3", "budget_exceeded", 3.0),
        _run("r4", "t4", "passed", 4.0),
    ]
    metrics = metrics_from_runs(runs, ["t1", "t2", "t3", "t4"])
    assert metrics.cost_total_usd == 10.0
    assert metrics.cost_per_solved_usd == 10.0
    assert metrics.invalid_ratio == 0.75


def test_zero_solved_denominator_is_null() -> None:
    runs = [_run("r1", "t1", "failed", 1.0)]
    metrics = metrics_from_runs(runs, ["t1"])
    assert metrics.cost_per_solved_usd is None

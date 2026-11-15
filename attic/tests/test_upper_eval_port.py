from __future__ import annotations

import inspect
import os
import subprocess
from pathlib import Path

import pytest

from molt.upper import eval_port
from molt.upper.eval_port import (
    EvalPortError,
    EvaluationRequest,
    EvaluationResult,
    evaluate,
    local_replay_adapter,
)
from molt.upper.evaluator import UpperEvaluator, load_scenario

REPO = Path(__file__).resolve().parents[1]


def _git_status() -> str:
    return subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=REPO,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def _request(candidate: str, baseline: str) -> EvaluationRequest:
    return EvaluationRequest(
        asset_ref={
            "asset_id": "mem-c1b",
            "version": "v1",
            "seed_identity_hash": "a" * 64,
            "record_hash": "b" * 64,
        },
        scenario_version="upper-c1b-1",
        task_refs=[
            {
                "task_id": "probe-order-x8",
                "family": "regression",
                "baseline_solution_ref": baseline,
                "candidate_solution_ref": candidate,
                "source_run_id": "run-1",
                "provenance": {"kind": "replay"},
            }
        ],
        dimensions=["regression"],
        options={"replay": True, "mcp_static_only": True},
    )


def test_port_unavailable_is_blocked_not_fabricated() -> None:
    request = _request(
        "probes/report/2c/memory-v3/solutions/gen4-probe-order-x8-r1.py.txt",
        "probes/report/2c/solutions/baseline/probe-order-x8-r1.py.txt",
    )
    result = evaluate(request, adapter="auto", timeout_s=5.0)
    if result.status != "blocked":
        pytest.skip("third macro upper_eval is installed in this environment")
    assert result.error["code"] in {"upper_eval_unavailable", "upper_eval_timeout"}
    assert result.metrics["transfer"]["tasks"] == 0
    assert result.evidence_refs == []
    assert result.provenance["adapter"] == "blocked"


def test_local_replay_adapter_passed_and_failed() -> None:
    before = _git_status()
    passed_request = _request(
        "probes/report/2c/memory-v3/solutions/gen4-probe-order-x8-r1.py.txt",
        "probes/report/2c/solutions/baseline/probe-order-x8-r1.py.txt",
    )
    passed = local_replay_adapter(passed_request)
    assert passed.status == "passed"
    assert passed.provenance["adapter"] == "local_replay"
    assert passed.provenance["calls_model"] is False
    assert passed.metrics["cost_usd"] == 0.0
    assert passed.metrics["transfer"]["candidate"]["passed"] == 1
    assert passed.metrics["transfer"]["baseline"]["passed"] == 1

    failed_request = _request(
        "probes/report/2c/solutions/followup/memory-probe-order-x8-r1.py.txt",
        "probes/report/2c/solutions/baseline/probe-order-x8-r1.py.txt",
    )
    failed = local_replay_adapter(failed_request)
    assert failed.status == "failed"
    assert failed.metrics["transfer"]["candidate"]["passed"] == 0
    assert failed.metrics["transfer"]["baseline"]["passed"] == 1
    assert _git_status() == before


def test_result_schema_validation_and_round_trip() -> None:
    result = local_replay_adapter(
        _request(
            "probes/report/2c/memory-v3/solutions/gen4-probe-order-x8-r1.py.txt",
            "probes/report/2c/solutions/baseline/probe-order-x8-r1.py.txt",
        )
    )
    payload = result.to_dict()
    for key in (
        "schema_version",
        "evaluation_id",
        "content_hash",
        "status",
        "metrics",
        "evidence_refs",
        "provenance",
        "error",
    ):
        assert key in payload
    for key in ("transfer", "regression", "retrieval", "cost_usd", "latency_ms", "permission"):
        assert key in payload["metrics"]
    assert EvaluationResult.from_dict(payload).status == result.status
    broken = dict(payload)
    broken["metrics"] = {}
    with pytest.raises(EvalPortError):
        EvaluationResult.from_dict(broken)


def test_request_rejects_absolute_paths_and_public_api_has_no_command() -> None:
    with pytest.raises(EvalPortError):
        EvaluationRequest(
            asset_ref={
                "asset_id": "a",
                "version": "v1",
                "seed_identity_hash": "a" * 64,
                "record_hash": "b" * 64,
            },
            scenario_version="v",
            task_refs=[
                {
                    "task_id": "t",
                    "family": "f",
                    "baseline_solution_ref": "/tmp/baseline.py",
                    "candidate_solution_ref": "probes/x.py",
                    "source_run_id": "r",
                    "provenance": {},
                }
            ],
        )
    parameters = set(inspect.signature(evaluate).parameters)
    assert parameters == {"request", "adapter", "timeout_s"}
    with pytest.raises(TypeError):
        evaluate(_request("probes/x.py", "probes/y.py"), command=["rm", "-rf"])  # type: ignore[call-arg]


def test_evaluator_wires_request_through_port(tmp_path: Path) -> None:
    scenario = load_scenario("c1b-memory-v1", REPO)
    evaluator = UpperEvaluator(scenario, state_dir=tmp_path / "eval-state")
    request = evaluator.build_evaluation_request("mem-c1b-gen4", "regression")
    assert request.scenario_version == "upper-c1b-1"
    assert len(request.task_refs) == 4
    assert all(not Path(ref).is_absolute() for ref in (
        task[key]
        for task in request.task_refs
        for key in ("baseline_solution_ref", "candidate_solution_ref")
    ))
    result = evaluator.evaluate_via_port(
        "mem-c1b-gen4", "regression", adapter="local_replay"
    )
    assert result.status == "passed"
    assert result.provenance["adapter"] == "local_replay"
    assert result.metrics["transfer"]["candidate"]["passed"] == 4


def test_timeout_kills_process_group(monkeypatch: pytest.MonkeyPatch) -> None:
    request = _request(
        "probes/report/2c/memory-v3/solutions/gen4-probe-order-x8-r1.py.txt",
        "probes/report/2c/solutions/baseline/probe-order-x8-r1.py.txt",
    )
    killed: list[int] = []

    class FakeProcess:
        pid = 424242
        returncode = -9

        def __init__(self) -> None:
            self.calls = 0

        def communicate(self, timeout: float | None = None):
            self.calls += 1
            if self.calls == 1:
                raise subprocess.TimeoutExpired("cmd", timeout)
            return ("", "")

        def poll(self):
            return None

        def kill(self) -> None:
            killed.append(self.pid)

    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: FakeProcess())
    monkeypatch.setattr(os, "getpgid", lambda pid: pid)
    monkeypatch.setattr(os, "killpg", lambda pid, sig: killed.append(pid))
    result = eval_port._run_module_cli(request, timeout_s=0.01)
    assert result.status == "blocked"
    assert result.error["code"] == "upper_eval_timeout"
    assert 424242 in killed

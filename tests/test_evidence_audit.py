"""D7：audit 绿/失败路径、search/report 分流断言、secret scan。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from molt.core.audit import audit_state
from molt.core.errors import AuditError
from molt.core.events import EventLog, make_event
from molt.core.redact import evidence_bundle, redact_evidence, write_redaction_manifest
from molt.core.types import GateDecision, Result, Run

ROOT = Path(__file__).resolve().parents[1]


def _refresh_manifest(store, scenario) -> None:
    """篡改非决策证据后重算 manifest，避免 source_hash 噪声掩盖目标断言。"""
    bundle = evidence_bundle(
        scenario_id=scenario.scenario_id,
        scenario_version=scenario.scenario_version,
        runs=store.list_runs(),
        results=store.list_results(),
        decisions=store.list_decisions(),
    )
    _, manifest = redact_evidence(bundle)
    write_redaction_manifest(store.root, scenario.scenario_id, manifest)


def _drop_decisions(store) -> None:
    for path in (store.root / "decisions").glob("*.json"):
        path.unlink()


def _first_run(store, result_id: str) -> Run:
    runs = {run.run_id: run for run in store.list_runs()}
    return runs[store.read_result(result_id).run_refs[0]]


def test_demo_audit_is_green(demo_artifacts) -> None:
    store, scenario, summary = demo_artifacts
    report = audit_state(store, scenario)
    assert report["runs"] == 2 * (10 + 5)  # baseline + candidate × (search 10 + holdout 5)
    assert report["results"] == 4
    assert report["decisions"] == 1
    assert report["report_events"] >= 2


def test_audit_is_read_only(demo_artifacts) -> None:
    store, scenario, _ = demo_artifacts
    before_runs = len(store.list_runs())
    before_pointer = store.current()
    audit_state(store, scenario)
    assert len(store.list_runs()) == before_runs
    assert store.current() == before_pointer


def test_tampered_run_cost_fails_audit(demo_copy) -> None:
    store, scenario, _ = demo_copy
    run = store.list_runs()[0]
    data = run.to_dict()
    data["usage"]["cost_usd"] = data["usage"]["cost_usd"] + 1.0
    store.write_run(Run.from_dict(data))
    with pytest.raises(AuditError) as excinfo:
        audit_state(store, scenario)
    assert "重算不一致" in str(excinfo.value) or "账本" in str(excinfo.value)


def test_tampered_result_fails_audit(demo_copy) -> None:
    store, scenario, _ = demo_copy
    result = store.list_results()[0]
    data = result.to_dict()
    block = data["scores"]["search"]
    block["cost_total_usd"] = block["cost_total_usd"] + 2.0
    block["cost_per_solved_usd"] = (
        block["cost_total_usd"] / block["solved"] if block["solved"] else None
    )
    store.write_result(Result.from_dict(data))
    with pytest.raises(AuditError) as excinfo:
        audit_state(store, scenario)
    assert "result" in str(excinfo.value)


def test_tampered_decision_fails_audit(demo_copy) -> None:
    store, scenario, _ = demo_copy
    decision = store.list_decisions()[0]
    data = decision.to_dict()
    data["verdict"] = "reject"
    data["reason"] = "budget_exceeded"
    store.write_decision(GateDecision.from_dict(data))
    with pytest.raises(AuditError) as excinfo:
        audit_state(store, scenario)
    assert "decision" in str(excinfo.value)


def test_search_event_leaking_holdout_id_fails(demo_copy) -> None:
    store, scenario, _ = demo_copy
    log = EventLog(store.root)
    log.append(
        make_event(
            scope="search",
            event_type="custom.leak",
            payload={"task_id": "holdout-even"},
        )
    )
    with pytest.raises(AuditError) as excinfo:
        audit_state(store, scenario)
    assert "holdout" in str(excinfo.value)


def test_report_stream_requires_holdout_accessed(demo_copy) -> None:
    store, scenario, _ = demo_copy
    for path in (store.root / "events" / "report").glob("*.jsonl"):
        path.unlink()
    with pytest.raises(AuditError) as excinfo:
        audit_state(store, scenario)
    assert "holdout.accessed" in str(excinfo.value)


def test_secret_scan_blocks_audit(demo_copy) -> None:
    store, scenario, _ = demo_copy
    (store.root / "leak.json").write_text(json.dumps({"token": "sk-" + "z" * 32}), encoding="utf-8")
    with pytest.raises(AuditError) as excinfo:
        audit_state(store, scenario)
    assert "secret scan" in str(excinfo.value)


def test_audit_cli_demo() -> None:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    completed = subprocess.run(
        [sys.executable, str(ROOT / "tests/run_cli.py"), "audit", "--scenario", "demo"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=180,
    )
    assert completed.returncode == 0, completed.stderr
    assert "audit ok" in completed.stdout


# -- D2：result / run 比较域按可取得的权威来源核验 --


def test_valid_results_without_decision_stay_green(demo_copy) -> None:
    store, scenario, _ = demo_copy
    _drop_decisions(store)
    _refresh_manifest(store, scenario)
    assert audit_state(store, scenario)["decisions"] == 0


def test_result_scenario_version_without_decision_fails(demo_copy) -> None:
    store, scenario, _ = demo_copy
    _drop_decisions(store)
    _refresh_manifest(store, scenario)
    data = store.read_result("result-baseline-search").to_dict()
    data["scenario_version"] = "demo-5"
    store.write_result(Result.from_dict(data))
    with pytest.raises(AuditError) as excinfo:
        audit_state(store, scenario)
    assert "result result-baseline-search.scenario_version" in str(excinfo.value)


def test_result_scenario_version_with_decision_fails(demo_copy) -> None:
    store, scenario, _ = demo_copy
    data = store.read_result("result-baseline-report").to_dict()
    data["scenario_version"] = "demo-5"
    store.write_result(Result.from_dict(data))
    with pytest.raises(AuditError) as excinfo:
        audit_state(store, scenario)
    assert "result result-baseline-report.scenario_version" in str(excinfo.value)


@pytest.mark.parametrize("field", ["scenario_version", "shell_version"])
def test_run_version_without_decision_fails(demo_copy, field: str) -> None:
    store, scenario, _ = demo_copy
    _drop_decisions(store)
    _refresh_manifest(store, scenario)
    run = _first_run(store, "result-baseline-search")
    data = run.to_dict()
    data[field] = scenario.scenario_version + "-other" if field == "scenario_version" else "9.9.9"
    store.write_run(Run.from_dict(data))
    with pytest.raises(AuditError) as excinfo:
        audit_state(store, scenario)
    assert f"run {run.run_id}.{field}" in str(excinfo.value)


@pytest.mark.parametrize("side", ["baseline", "candidate"])
@pytest.mark.parametrize(
    "field", ["scenario_version", "shell_version", "verifier_hash"]
)
def test_run_comparison_domain_with_decision_fails(demo_copy, side: str, field: str) -> None:
    store, scenario, _ = demo_copy
    run = _first_run(store, f"result-{side}-search")
    data = run.to_dict()
    if field == "scenario_version":
        data[field] = scenario.scenario_version + "-other"
    elif field == "shell_version":
        data[field] = "9.9.9"
    else:
        data[field] = "0" * 64
    store.write_run(Run.from_dict(data))
    _refresh_manifest(store, scenario)
    with pytest.raises(AuditError) as excinfo:
        audit_state(store, scenario)
    assert f"run {run.run_id}.{field}" in str(excinfo.value)


def test_run_and_decision_shell_tamper_still_blocked_by_scenario(demo_copy) -> None:
    store, scenario, _ = demo_copy
    run = _first_run(store, "result-baseline-search")
    run_data = run.to_dict()
    run_data["shell_version"] = "9.9.9"
    store.write_run(Run.from_dict(run_data))
    data = store.list_decisions()[0].to_dict()
    data["shell_version"] = "9.9.9"
    store.write_decision(GateDecision.from_dict(data))
    _refresh_manifest(store, scenario)
    with pytest.raises(AuditError) as excinfo:
        audit_state(store, scenario)
    message = str(excinfo.value)
    assert "decision demo-decision-1.shell_version" in message
    assert f"run {run.run_id}.shell_version" in message


def test_decision_verifier_hash_tamper_fails(demo_copy) -> None:
    store, scenario, _ = demo_copy
    data = store.list_decisions()[0].to_dict()
    data["verifier_hash"] = "f" * 64
    store.write_decision(GateDecision.from_dict(data))
    _refresh_manifest(store, scenario)
    with pytest.raises(AuditError) as excinfo:
        audit_state(store, scenario)
    assert "verifier_hash" in str(excinfo.value)


def test_paired_and_comparison_domain_errors_are_aggregated(demo_copy) -> None:
    store, scenario, _ = demo_copy
    run = _first_run(store, "result-candidate-search")
    run_data = run.to_dict()
    run_data["verifier_hash"] = "0" * 64
    store.write_run(Run.from_dict(run_data))
    data = store.list_decisions()[0].to_dict()
    row = data["paired"][0]
    row["candidate_cost_usd"] = float(row["candidate_cost_usd"]) + 0.5
    solved = data["scores"]["candidate"]["solved"]
    data["scores"]["candidate"]["cost_per_solved_usd"] = (
        sum(item["candidate_cost_usd"] for item in data["paired"]) / solved
    )
    store.write_decision(GateDecision.from_dict(data))
    _refresh_manifest(store, scenario)
    with pytest.raises(AuditError) as excinfo:
        audit_state(store, scenario)
    message = str(excinfo.value)
    assert "paired" in message
    assert "verifier_hash" in message

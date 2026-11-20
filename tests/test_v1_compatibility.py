"""Self-contained synthetic v1 evidence: read-only audit and explicit migration.

No shell, benchmark, runtime or model is loaded. Nonzero costs below are invented
fixture values, never claims about real execution. This file is portable to an
installed-wheel test directory along with test_kernel_transactions.py.
"""

from __future__ import annotations

import json
import stat
from dataclasses import replace
from pathlib import Path

import pytest
from test_kernel_transactions import TS, _binary, _run, _server

from attic.v1.kernel_mirror import materialize_current, mirror_state, preview_state
from molt.core import write_policy
from molt.core.audit import audit_state
from molt.core.errors import AuditError
from molt.core.events import make_event
from molt.core.metrics import metrics_from_runs
from molt.core.policy import SideEval, TaskEval, decide
from molt.core.redact import evidence_bundle, redact_evidence
from molt.core.store import StateStore
from molt.core.types import (
    Budget,
    BudgetCaps,
    Genome,
    Isolation,
    Policy,
    Result,
    Run,
    Scenario,
    ScoreBlock,
    Switches,
    Usage,
    Verification,
)
from molt.kernel_client import KernelClient

HASH = "a" * 64


def _write(path: Path, value: dict) -> None:
    """Construct test data only; never use the production legacy writer."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n")


def build_compatibility_fixture(root: Path) -> Scenario:
    """Create new synthetic data in a fresh caller-owned test directory."""
    root.mkdir()  # Refuse existing paths rather than overwrite fixtures/user state.
    scenario = Scenario(
        "1.0.0", "compat-synthetic-1", "compat-synthetic", "Synthetic v1 compatibility",
        "compat-synthetic", "1.0.0", HASH,
        {"search": ["synthetic-search"], "report": ["synthetic-report"]},
        Policy(0, 0.0, BudgetCaps(1.0, 1.0)), Isolation("none"), {"provenance": "synthetic"},
    )
    scenario = Scenario.from_dict(scenario.to_dict())
    limits = Budget(100, 1000, 1, 1)
    runs = []
    results = []
    for label, cost in (("baseline", 0.02), ("candidate", 0.01)):
        genome = Genome(
            "1.0.0", scenario.scenario_version, f"synthetic-{label}", None,
            scenario.shell_id, scenario.shell_version, Switches("off", "minimal", "bash"),
            "synthetic/prompt", "synthetic/tools", [], {"runtime_id": "fixture", "version": "synthetic-1"}, limits,
            {"synthetic/prompt": HASH, "synthetic/tools": HASH},
            {"provenance": "synthetic"},
        )
        genome = Genome.from_dict(genome.to_dict())
        _write(root / "genomes" / f"{genome.genome_id}.json", genome.to_dict())
        side_runs = []
        for scope in ("search", "report"):
            run = Run(
                "1.0.0", f"synthetic-{label}-{scope}", scenario.scenario_version,
                genome.genome_id, scenario.task_sets[scope][0], scope, 1,
                scenario.shell_version, HASH, "fixed", TS, TS, "passed",
                Usage(0, 0, cost, 1, 1), Verification(HASH, HASH, True), {},
                {"runtime": {"kind": "fixture", "provenance": {"kind": "fixture"}},
                 "synthetic": True},
            )
            run = Run.from_dict(run.to_dict())
            _write(root / "runs" / f"{run.run_id}.json", run.to_dict())
            _write(root / "ledger" / f"{run.run_id}.jsonl", {
                "kind": "execution", "owner_id": run.run_id, "cost_usd": cost,
                "provenance": "synthetic",
            })
            side_runs.append(run)
        result = Result(
            "1.0.0", f"synthetic-{label}", scenario.scenario_version, genome.genome_id,
            "report", "fixed", [r.run_id for r in side_runs],
            {key: ScoreBlock.from_dict(metrics_from_runs(
                [r for r in side_runs if r.scope == scope], scenario.task_sets[scope],
            ).to_score_block(), key) for key, scope in (("search", "search"), ("holdout", "report"))},
            TS, {"provenance": "synthetic"},
        )
        result = Result.from_dict(result.to_dict())
        _write(root / "results" / f"{result.result_id}.json", result.to_dict())
        runs.extend(side_runs)
        results.append(result)
    decision = decide(
        scenario.policy,
        SideEval("synthetic-baseline", scenario.scenario_version,
                 (TaskEval("synthetic-search", True, 0.02),), limits, 0.0),
        SideEval("synthetic-candidate", scenario.scenario_version,
                 (TaskEval("synthetic-search", True, 0.01),), limits, 0.0),
        decision_id="synthetic-gate", scenario_version=scenario.scenario_version,
        shell_version=scenario.shell_version, verifier_hash=HASH, created_at=TS,
    )
    decision = replace(decision, ext={
        "inputs": {"baseline_result_id": results[0].result_id,
                   "candidate_result_id": results[1].result_id},
        "overhead_spend_usd": 0.0, "provenance": "synthetic",
    })
    _write(root / "decisions" / "synthetic-gate.json", decision.to_dict())
    event = make_event(
        scope="report", event_type="holdout.accessed", event_id="synthetic-holdout",
        ts=TS, payload={"task_ids": scenario.task_sets["report"]},
    )
    _write(root / "events" / "report" / "global.jsonl", event.to_dict())
    bundle = evidence_bundle(
        scenario_id=scenario.scenario_id, scenario_version=scenario.scenario_version,
        runs=runs, results=results, decisions=[decision],
    )
    _, redaction = redact_evidence(bundle)
    _write(root / "redaction" / f"{scenario.scenario_id}.json", redaction)
    return scenario


def _snapshot(root: Path) -> tuple:
    """Bind content, path, type, permissions, size, inode and modification metadata."""
    return tuple(
        (p.relative_to(root).as_posix(), stat.S_IMODE(p.stat().st_mode), p.stat().st_size,
         p.stat().st_mtime_ns, p.stat().st_ctime_ns, p.stat().st_ino,
         p.read_bytes() if p.is_file() else None)
        for p in [root, *sorted(root.rglob("*"))]
    )


def test_v1_synthetic_audit_is_read_only_and_detects_tampering(tmp_path, monkeypatch) -> None:
    root = tmp_path / "v1"
    scenario = build_compatibility_fixture(root)
    monkeypatch.setattr(write_policy, "DEFAULT_BACKEND", "kernel")
    before = _snapshot(root)
    assert audit_state(StateStore(root), scenario) == {
        "runs": 4, "results": 2, "decisions": 1, "events": 1, "report_events": 1,
    }
    assert _snapshot(root) == before
    with pytest.raises(PermissionError):
        StateStore(root).ensure()
    assert _snapshot(root) == before
    path = root / "runs" / "synthetic-baseline-search.json"
    run = json.loads(path.read_text())
    run["usage"]["cost_usd"] = 0.5
    _write(path, run)
    before_invalid_audit = _snapshot(root)
    with pytest.raises(AuditError, match="账本不一致"):
        audit_state(StateStore(root), scenario)
    assert _snapshot(root) == before_invalid_audit


@pytest.mark.integration
def test_v1_synthetic_migration_preserves_source_and_retry_contract(tmp_path, monkeypatch) -> None:
    binary = _binary()
    source = tmp_path / "v1"
    scenario = build_compatibility_fixture(source)
    monkeypatch.setattr(write_policy, "DEFAULT_BACKEND", "kernel")
    before = _snapshot(source)
    state = tmp_path / "kernel"
    _run(binary, "init", "--state", state)
    with _server(binary, state) as socket:
        with KernelClient(socket, timeout=2) as client:
            assert client.current() is None
        preview = preview_state(source, socket)
        assert preview["changed"] is True and preview["source_files"] > 0
        assert _snapshot(source) == before
        with KernelClient(socket, timeout=2) as client:
            assert client.current() is None
        first = mirror_state(source, socket, migration_id="synthetic-migration")
        assert first["generation"] == 1
        assert _snapshot(source) == before
        retry = mirror_state(source, socket, migration_id="synthetic-migration")
        assert retry["generation"] == 1 and retry["unchanged"] is True
        assert preview_state(source, socket)["differences"] == []
        projected = tmp_path / "projection"
        materialize_current(projected, socket)
        assert audit_state(StateStore(projected / "state"), scenario)["decisions"] == 1
        assert audit_state(StateStore(source), scenario)["runs"] == 4
        assert _snapshot(source) == before
        # A changed source cannot be applied under the original migration ID.
        _write(source / "synthetic-note.json", {"provenance": "synthetic", "changed": True})
        changed = _snapshot(source)
        with pytest.raises(ValueError, match="different source snapshot"):
            mirror_state(source, socket, migration_id="synthetic-migration")
        assert _snapshot(source) == changed
        with KernelClient(socket, timeout=2) as client:
            assert client.current()["generation"] == 1
    _run(binary, "audit", "--state", state)

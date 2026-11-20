"""退出 gate CLI 后，保留 v1 配对/总数/版本隔离与拒绝写入覆盖。"""

from __future__ import annotations

import json
from dataclasses import replace

import pytest
from test_v1_compatibility import _snapshot, _write, build_compatibility_fixture

from molt.cli import main
from molt.core.audit import audit_state
from molt.core.errors import AuditError, IncomparableError, ValidationError
from molt.core.policy import SideEval, TaskEval, decide
from molt.core.store import StateStore


@pytest.mark.parametrize("mutation", ["missing_scores", "missing_total", "total", "paired", "missing_paired"])
def test_v1_audit_rejects_invalid_gate_evidence(tmp_path, mutation):
    root = tmp_path / "v1"
    scenario = build_compatibility_fixture(root)
    assert audit_state(StateStore(root), scenario)["decisions"] == 1
    path = root / "decisions/synthetic-gate.json"
    decision = json.loads(path.read_text())
    if mutation == "missing_scores":
        decision.pop("scores")
    elif mutation == "missing_total":
        decision["scores"]["baseline"].pop("total")
    elif mutation == "total":
        decision["scores"]["baseline"]["total"] = 99
    elif mutation == "paired":
        decision["paired"][0]["baseline_passed"] = False
    else:
        decision.pop("paired")
    _write(path, decision)
    before = _snapshot(root)
    with pytest.raises((AuditError, ValidationError)):
        audit_state(StateStore(root), scenario)
    assert _snapshot(root) == before


def test_v1_policy_rejects_cross_version_side(tmp_path):
    scenario = build_compatibility_fixture(tmp_path / "v1")
    limits = {"tokens": 1000, "wall_ms": 1000, "turns": 8, "attempts": 1}
    side = SideEval("synthetic-baseline", scenario.scenario_version,
                    (TaskEval("synthetic-search", True, 0.02),), limits, 0.0)
    with pytest.raises(IncomparableError):
        decide(scenario.policy, side, replace(side, scenario_version="other-version"),
               decision_id="synthetic-gate", scenario_version=scenario.scenario_version,
               shell_version=scenario.shell_version, verifier_hash="a" * 64,
               created_at="2026-11-19T00:00:00Z")


def test_gate_cli_is_rejected_without_touching_existing_state(tmp_path, capsys):
    root = tmp_path / "v1"
    build_compatibility_fixture(root)
    before = _snapshot(root)
    with pytest.raises(SystemExit) as exc:
        main(["gate", "--fixture", str(root)])
    assert exc.value.code == 2
    assert "invalid choice" in capsys.readouterr().err
    assert _snapshot(root) == before

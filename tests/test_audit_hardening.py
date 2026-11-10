"""P1/P4：audit 无条件重算 invalid ratio、overhead 与 ledger 对账、版本 guard。"""

from __future__ import annotations

import json

import pytest

from molt.core.audit import _overhead_from_ledger, audit_state
from molt.core.errors import AuditError
from molt.core.hash import canonical_json_text
from molt.core.ledger import Ledger
from molt.core.types import GateDecision


def _decision_data(store) -> dict:
    return store.list_decisions()[0].to_dict()


def _rewrite(store, data: dict) -> None:
    store.write_decision(GateDecision.from_dict(data))


def test_missing_inputs_blocks_audit_even_with_tampered_ratio(demo_copy) -> None:
    store, scenario, _ = demo_copy
    data = _decision_data(store)
    data["ext"].pop("inputs")
    data["invalid_ratio"]["baseline"] = 0.5
    data["invalid_ratio"]["candidate"] = 0.5
    _rewrite(store, data)
    with pytest.raises(AuditError) as excinfo:
        audit_state(store, scenario)
    assert "ext.inputs" in str(excinfo.value)


def test_missing_result_reference_blocks_audit(demo_copy) -> None:
    store, scenario, _ = demo_copy
    data = _decision_data(store)
    data["ext"]["inputs"]["baseline_result_id"] = "result-does-not-exist"
    _rewrite(store, data)
    with pytest.raises(AuditError) as excinfo:
        audit_state(store, scenario)
    assert "result 不存在" in str(excinfo.value)


def test_overhead_must_match_ledger(demo_copy) -> None:
    store, scenario, _ = demo_copy
    data = _decision_data(store)
    data["ext"]["overhead_spend_usd"] = float(data["ext"]["overhead_spend_usd"]) + 1.25
    _rewrite(store, data)
    with pytest.raises(AuditError) as excinfo:
        audit_state(store, scenario)
    assert "ledger overhead" in str(excinfo.value)


def test_missing_overhead_field_fails(demo_copy) -> None:
    store, scenario, _ = demo_copy
    data = _decision_data(store)
    data["ext"].pop("overhead_spend_usd")
    _rewrite(store, data)
    with pytest.raises(AuditError) as excinfo:
        audit_state(store, scenario)
    assert "overhead_spend_usd" in str(excinfo.value)


def test_tampered_ledger_overhead_fails(demo_copy) -> None:
    store, scenario, _ = demo_copy
    path = next((store.root / "ledger").glob("prop-*.jsonl"))
    entry = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    entry["cost_usd"] = float(entry["cost_usd"]) + 0.5
    path.write_text(canonical_json_text(entry) + "\n", encoding="utf-8")
    with pytest.raises(AuditError) as excinfo:
        audit_state(store, scenario)
    assert "ledger overhead" in str(excinfo.value)


def _write_ledger_overhead(store, cost: float) -> None:
    path = next((store.root / "ledger").glob("prop-*.jsonl"))
    entry = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    entry["kind"] = "overhead"
    entry["cost_usd"] = cost
    path.write_text(canonical_json_text(entry) + "\n", encoding="utf-8")


def test_empty_proposal_ids_cannot_hide_ledger_overhead(demo_copy) -> None:
    store, scenario, _ = demo_copy
    _write_ledger_overhead(store, 5.0)
    data = _decision_data(store)
    data["ext"]["inputs"]["proposal_ids"] = []
    data["ext"]["overhead_spend_usd"] = 0.0
    _rewrite(store, data)
    with pytest.raises(AuditError) as excinfo:
        audit_state(store, scenario)
    assert "ledger overhead" in str(excinfo.value)


def test_proposal_ids_subset_cannot_hide_ledger_overhead(demo_copy) -> None:
    store, scenario, _ = demo_copy
    _write_ledger_overhead(store, 5.0)
    data = _decision_data(store)
    data["ext"]["inputs"]["proposal_ids"] = ["prop-some-other-proposal"]
    data["ext"]["overhead_spend_usd"] = 0.0
    _rewrite(store, data)
    with pytest.raises(AuditError) as excinfo:
        audit_state(store, scenario)
    assert "ledger overhead" in str(excinfo.value)


def test_single_decision_overhead_reconciles(demo_artifacts) -> None:
    store, scenario, _ = demo_artifacts
    decision = store.list_decisions()[0]
    assert _overhead_from_ledger(Ledger(store.root)) == float(decision.ext["overhead_spend_usd"])
    assert audit_state(store, scenario)["decisions"] == 1


def test_decision_scenario_version_mismatch_fails(demo_copy) -> None:
    store, scenario, _ = demo_copy
    data = _decision_data(store)
    data["scenario_version"] = "demo-2"
    _rewrite(store, data)
    with pytest.raises(AuditError) as excinfo:
        audit_state(store, scenario)
    assert "scenario_version" in str(excinfo.value)

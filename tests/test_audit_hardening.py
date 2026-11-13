"""P1/P4：audit 无条件重算 invalid ratio、overhead 与 ledger 对账、版本 guard。"""

from __future__ import annotations

import json

import pytest

from molt.core.audit import _overhead_from_ledger, audit_state
from molt.core.errors import AuditError
from molt.core.hash import canonical_json_text
from molt.core.ledger import Ledger
from molt.core.metrics import metrics_from_runs, paired_rows_from_runs
from molt.core.redact import evidence_bundle, redact_evidence, write_redaction_manifest
from molt.core.types import GateDecision, Result, Run


def _decision_data(store) -> dict:
    return store.list_decisions()[0].to_dict()


def _rewrite(store, data: dict) -> None:
    store.write_decision(GateDecision.from_dict(data))


def _refresh_manifest(store, scenario) -> None:
    """篡改后重算脱敏 manifest，避免 source_hash 噪声掩盖目标断言。"""
    bundle = evidence_bundle(
        scenario_id=scenario.scenario_id,
        scenario_version=scenario.scenario_version,
        runs=store.list_runs(),
        results=store.list_results(),
        decisions=store.list_decisions(),
    )
    _, manifest = redact_evidence(bundle)
    write_redaction_manifest(store.root, scenario.scenario_id, manifest)


def _search_runs(store, result_id: str) -> list[Run]:
    runs = {run.run_id: run for run in store.list_runs()}
    result = store.read_result(result_id)
    return [runs[ref] for ref in result.run_refs]


def _combined_search_result(store, scenario, extra_runs: tuple[Run, ...] = ()) -> Result:
    """把两侧 search run 合进一个 result，用于验证按 genome 分侧。"""
    baseline = store.read_result("result-baseline-search")
    candidate = store.read_result("result-candidate-search")
    runs = {run.run_id: run for run in store.list_runs()}
    run_refs = list(baseline.run_refs) + list(candidate.run_refs)
    evidence = [runs[ref] for ref in run_refs] + list(extra_runs)
    metrics = metrics_from_runs(evidence, scenario.task_sets["search"])
    data = baseline.to_dict()
    data["result_id"] = "result-combined-search"
    data["run_refs"] = run_refs + [run.run_id for run in extra_runs]
    data["scores"] = {"search": metrics.to_score_block()}
    result = Result.from_dict(data)
    store.write_result(result)
    return result


def _point_inputs(store, baseline_id: str, candidate_id: str) -> None:
    data = _decision_data(store)
    data["ext"]["inputs"]["baseline_result_id"] = baseline_id
    data["ext"]["inputs"]["candidate_result_id"] = candidate_id
    _rewrite(store, data)


def _retry_run(store, source: Run, *, cost: float, outcome: str) -> Run:
    data = source.to_dict()
    data["run_id"] = f"{source.run_id}-retry"
    data["attempt"] = 2
    data["outcome"] = outcome
    data["usage"]["cost_usd"] = cost
    data["usage"]["wall_ms"] = 1
    retry = Run.from_dict(data)
    store.write_run(retry)
    Ledger(store.root).append(
        retry.run_id,
        {
            "kind": "execution",
            "cost_usd": cost,
            "task_id": retry.task_id,
            "scope": retry.scope,
            "outcome": outcome,
            "wall_ms": 1,
            "tokens_in": 0,
            "tokens_out": 0,
        },
    )
    return retry


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
    data["scenario_version"] = scenario.scenario_version + "-other"
    _rewrite(store, data)
    with pytest.raises(AuditError) as excinfo:
        audit_state(store, scenario)
    assert "scenario_version" in str(excinfo.value)


# -- D1：K5 配对表必须能从按 genome / scope 分侧后的 run 重算 --


def test_paired_table_matches_runs(demo_artifacts) -> None:
    """正例：demo 的配对表可由两侧 search runs 逐行重算。"""
    store, scenario, _ = demo_artifacts
    baseline_runs, candidate_runs = (
        _search_runs(store, "result-baseline-search"),
        _search_runs(store, "result-candidate-search"),
    )
    expected = paired_rows_from_runs(
        baseline_runs, candidate_runs, scenario.task_sets["search"]
    )
    assert expected == store.list_decisions()[0].paired
    assert audit_state(store, scenario)["decisions"] == 1


def test_paired_cost_tamper_is_rejected(demo_copy) -> None:
    store, scenario, _ = demo_copy
    data = _decision_data(store)
    row = data["paired"][0]
    row["candidate_cost_usd"] = float(row["candidate_cost_usd"]) + 0.5
    solved = data["scores"]["candidate"]["solved"]
    data["scores"]["candidate"]["cost_per_solved_usd"] = (
        sum(item["candidate_cost_usd"] for item in data["paired"]) / solved
    )
    _rewrite(store, data)
    _refresh_manifest(store, scenario)
    with pytest.raises(AuditError) as excinfo:
        audit_state(store, scenario)
    message = str(excinfo.value)
    assert "paired" in message and "candidate_cost_usd" in message


def test_paired_passed_tamper_is_rejected(demo_copy) -> None:
    store, scenario, _ = demo_copy
    data = _decision_data(store)
    data["paired"][0]["candidate_passed"] = False
    solved = sum(1 for row in data["paired"] if row["candidate_passed"])
    data["scores"]["candidate"]["solved"] = solved
    data["scores"]["candidate"]["cost_per_solved_usd"] = (
        sum(item["candidate_cost_usd"] for item in data["paired"]) / solved
    )
    _rewrite(store, data)
    _refresh_manifest(store, scenario)
    with pytest.raises(AuditError) as excinfo:
        audit_state(store, scenario)
    assert "candidate_passed" in str(excinfo.value)


def test_paired_task_order_tamper_is_rejected(demo_copy) -> None:
    store, scenario, _ = demo_copy
    data = _decision_data(store)
    data["paired"][0], data["paired"][1] = data["paired"][1], data["paired"][0]
    _rewrite(store, data)
    _refresh_manifest(store, scenario)
    with pytest.raises(AuditError) as excinfo:
        audit_state(store, scenario)
    assert "任务集合/顺序" in str(excinfo.value)


def test_paired_missing_task_is_rejected(demo_copy) -> None:
    store, scenario, _ = demo_copy
    data = _decision_data(store)
    dropped = data["paired"].pop()
    data["scores"]["candidate"]["total"] = len(data["paired"])
    data["scores"]["baseline"]["total"] = len(data["paired"])
    data["scores"]["baseline"]["solved"] = sum(
        1 for row in data["paired"] if row["baseline_passed"]
    )
    data["scores"]["candidate"]["solved"] = sum(
        1 for row in data["paired"] if row["candidate_passed"]
    )
    for side in ("baseline", "candidate"):
        solved = data["scores"][side]["solved"]
        data["scores"][side]["cost_per_solved_usd"] = (
            sum(row[f"{side}_cost_usd"] for row in data["paired"]) / solved
        )
    _rewrite(store, data)
    _refresh_manifest(store, scenario)
    with pytest.raises(AuditError) as excinfo:
        audit_state(store, scenario)
    message = str(excinfo.value)
    assert "任务集合/顺序" in message and dropped["task_id"] in message


def test_report_scope_runs_do_not_enter_paired_cost(demo_copy) -> None:
    """正例：decision 可引用 report result，report run 不得进入 paired 成本。"""
    store, scenario, _ = demo_copy
    _point_inputs(store, "result-baseline-report", "result-candidate-report")
    _refresh_manifest(store, scenario)
    assert audit_state(store, scenario)["decisions"] == 1


def test_combined_result_splits_by_genome(demo_copy) -> None:
    """正例：同一 result 同时含两侧 genome 时按 genome 分侧，不混算成本。"""
    store, scenario, _ = demo_copy
    _combined_search_result(store, scenario)
    _point_inputs(store, "result-combined-search", "result-combined-search")
    _refresh_manifest(store, scenario)
    assert audit_state(store, scenario)["decisions"] == 1


def test_shared_run_across_results_is_not_double_charged(demo_copy) -> None:
    """正例：同一真实 run 被两个 result 共同引用时去重，不计两次成本。"""
    store, scenario, _ = demo_copy
    base_runs = _search_runs(store, "result-baseline-search")
    candidate_runs = _search_runs(store, "result-candidate-search")
    shared = candidate_runs[0]
    runs = {run.run_id: run for run in store.list_runs()}
    refs = [run.run_id for run in base_runs] + [shared.run_id]
    data = store.read_result("result-baseline-search").to_dict()
    data["result_id"] = "result-baseline-shared"
    data["run_refs"] = refs
    data["scores"] = {
        "search": metrics_from_runs(
            [runs[ref] for ref in refs], scenario.task_sets["search"]
        ).to_score_block()
    }
    store.write_result(Result.from_dict(data))
    _point_inputs(store, "result-baseline-shared", "result-candidate-search")
    _refresh_manifest(store, scenario)
    assert audit_state(store, scenario)["decisions"] == 1


def test_extra_attempt_keeps_existing_scoring(demo_copy) -> None:
    """正例：合法重试保留口径——首轮判 passed、全部 attempt 累计成本。"""
    store, scenario, _ = demo_copy
    source = _search_runs(store, "result-candidate-search")[0]
    retry = _retry_run(store, source, cost=0.05, outcome="failed")
    result_data = store.read_result("result-candidate-search").to_dict()
    result_data["run_refs"].append(retry.run_id)
    result_runs = _search_runs(store, "result-candidate-search") + [retry]
    result_data["scores"] = {
        "search": metrics_from_runs(
            result_runs, scenario.task_sets["search"]
        ).to_score_block()
    }
    store.write_result(Result.from_dict(result_data))
    data = _decision_data(store)
    for row in data["paired"]:
        if row["task_id"] == retry.task_id:
            row["candidate_cost_usd"] = float(row["candidate_cost_usd"]) + retry.usage.cost_usd
    solved = data["scores"]["candidate"]["solved"]
    data["scores"]["candidate"]["cost_per_solved_usd"] = (
        sum(row["candidate_cost_usd"] for row in data["paired"]) / solved
    )
    _rewrite(store, data)
    _refresh_manifest(store, scenario)
    assert audit_state(store, scenario)["decisions"] == 1


def test_invalid_ratio_uses_per_side_split(demo_copy) -> None:
    """正例：两侧 invalid_ratio 从同一分侧结果重算，不使用 result union。"""
    store, scenario, _ = demo_copy
    candidates = _search_runs(store, "result-candidate-search")
    extras = tuple(
        _retry_run(store, run, cost=0.0, outcome="invalid") for run in candidates[:4]
    )
    _combined_search_result(store, scenario, extra_runs=extras)
    _point_inputs(store, "result-combined-search", "result-combined-search")
    union_runs = _search_runs(store, "result-combined-search")
    candidate_side = [
        run for run in union_runs if run.genome_id == candidates[0].genome_id
    ]
    per_side = metrics_from_runs(
        candidate_side, scenario.task_sets["search"]
    ).invalid_ratio
    union = metrics_from_runs(union_runs, scenario.task_sets["search"]).invalid_ratio
    assert len(union_runs) == 24 and len(candidate_side) == 14
    assert per_side == 4 / 14
    assert union == 4 / 24
    data = _decision_data(store)
    data["invalid_ratio"]["baseline"] = 0.0
    data["invalid_ratio"]["candidate"] = per_side
    data["verdict"] = "unstable"
    data["reason"] = "invalid_ratio_exceeded"
    _rewrite(store, data)
    _refresh_manifest(store, scenario)
    assert audit_state(store, scenario)["decisions"] == 1


def test_union_invalid_ratio_cannot_dilute_threshold(demo_copy) -> None:
    """负例：union ratio 4/24=16.7% 低于阈值，但分侧 4/14=28.6% 应判 unstable。"""
    store, scenario, _ = demo_copy
    candidates = _search_runs(store, "result-candidate-search")
    extras = tuple(
        _retry_run(store, run, cost=0.0, outcome="invalid") for run in candidates[:4]
    )
    _combined_search_result(store, scenario, extra_runs=extras)
    _point_inputs(store, "result-combined-search", "result-combined-search")
    union_runs = _search_runs(store, "result-combined-search")
    union_ratio = metrics_from_runs(
        union_runs, scenario.task_sets["search"]
    ).invalid_ratio
    assert union_ratio < 0.25 < 4 / 14
    data = _decision_data(store)
    data["invalid_ratio"]["baseline"] = union_ratio
    data["invalid_ratio"]["candidate"] = union_ratio
    _rewrite(store, data)
    _refresh_manifest(store, scenario)
    with pytest.raises(AuditError) as excinfo:
        audit_state(store, scenario)
    message = str(excinfo.value)
    assert "invalid_ratio 重算不一致" in message
    assert "verdict 重算不一致" in message
    assert "unstable" in message
    assert "invalid_ratio_exceeded" in message
    assert "paired" not in message


def test_invalid_ratio_threshold_tamper_is_rejected(demo_copy) -> None:
    store, scenario, _ = demo_copy
    data = _decision_data(store)
    data["invalid_ratio"]["threshold"] = 0.5
    _rewrite(store, data)
    _refresh_manifest(store, scenario)
    with pytest.raises(AuditError) as excinfo:
        audit_state(store, scenario)
    assert "invalid_ratio 重算不一致" in str(excinfo.value)


def test_third_genome_in_decision_evidence_is_rejected(demo_copy) -> None:
    store, scenario, _ = demo_copy
    run = _search_runs(store, "result-baseline-search")[0]
    data = run.to_dict()
    data["genome_id"] = "sha256:" + "0" * 64
    store.write_run(Run.from_dict(data))
    _refresh_manifest(store, scenario)
    with pytest.raises(AuditError) as excinfo:
        audit_state(store, scenario)
    message = str(excinfo.value)
    assert "第三 genome" in message
    assert "paired 与 invalid_ratio 均未重算" in message


def test_same_genome_on_both_sides_is_rejected(demo_copy) -> None:
    store, scenario, _ = demo_copy
    data = _decision_data(store)
    data["scores"]["candidate"]["genome_id"] = data["scores"]["baseline"]["genome_id"]
    data["candidate_genome_id"] = data["scores"]["baseline"]["genome_id"]
    _rewrite(store, data)
    _refresh_manifest(store, scenario)
    with pytest.raises(AuditError) as excinfo:
        audit_state(store, scenario)
    message = str(excinfo.value)
    assert "两侧 genome 相同" in message
    assert "paired 与 invalid_ratio 均未重算" in message


def test_side_without_search_runs_is_rejected(demo_copy) -> None:
    store, scenario, _ = demo_copy
    _point_inputs(store, "result-candidate-report", "result-candidate-search")
    _refresh_manifest(store, scenario)
    with pytest.raises(AuditError) as excinfo:
        audit_state(store, scenario)
    message = str(excinfo.value)
    assert "baseline 侧没有匹配 genome 的 search runs" in message
    assert "paired 与 invalid_ratio 均未重算" in message
    assert "invalid_ratio 重算不一致" not in message


# -- D3：顶层 genome ID 必须与 scores 同侧 ID 一致 --


@pytest.mark.parametrize("side", ["baseline", "candidate"])
def test_top_level_genome_id_mismatch_skips_replay(
    demo_copy, monkeypatch, side: str
) -> None:
    store, scenario, _ = demo_copy
    data = _decision_data(store)
    other = data["scores"]["candidate" if side == "baseline" else "baseline"][
        "genome_id"
    ]
    data[f"{side}_genome_id"] = other
    _rewrite(store, data)
    _refresh_manifest(store, scenario)

    calls = {"read_genome": 0, "split": 0, "decide": 0}
    real_read_genome = store.read_genome

    def read_genome_spy(genome_id: str):
        calls["read_genome"] += 1
        return real_read_genome(genome_id)

    monkeypatch.setattr(store, "read_genome", read_genome_spy)
    from molt.core import audit as audit_module

    real_split = audit_module._split_search_runs

    def split_spy(*args, **kwargs):
        calls["split"] += 1
        return real_split(*args, **kwargs)

    monkeypatch.setattr(audit_module, "_split_search_runs", split_spy)
    real_decide = audit_module.decide

    def decide_spy(*args, **kwargs):
        calls["decide"] += 1
        return real_decide(*args, **kwargs)

    monkeypatch.setattr(audit_module, "decide", decide_spy)

    with pytest.raises(AuditError) as excinfo:
        audit_state(store, scenario)
    message = str(excinfo.value)
    assert f"{side}_genome_id" in message
    assert f"scores.{side}.genome_id" in message
    assert "跳过该 decision 的 budget / paired / gate 重放" in message
    assert calls == {"read_genome": 0, "split": 0, "decide": 0}


def test_missing_run_reference_in_result_is_rejected(demo_copy) -> None:
    store, scenario, _ = demo_copy
    data = store.read_result("result-baseline-search").to_dict()
    data["run_refs"].append("run-does-not-exist")
    store.write_result(Result.from_dict(data))
    with pytest.raises(AuditError) as excinfo:
        audit_state(store, scenario)
    assert "引用不存在的 run" in str(excinfo.value)


def test_duplicate_run_reference_in_result_is_rejected(demo_copy) -> None:
    store, scenario, _ = demo_copy
    data = store.read_result("result-baseline-search").to_dict()
    data["run_refs"].append(data["run_refs"][0])
    # 重复 run_refs 会被类型层拒绝；这里模拟磁盘上被篡改的记录，审计仍须报错。
    path = store.root / "results" / "result-baseline-search.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(AuditError) as excinfo:
        audit_state(store, scenario)
    assert "不得有重复项" in str(excinfo.value)

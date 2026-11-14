from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from molt.upper import evaluator as evaluator_module
from molt.upper.demo import run_demo
from molt.upper.evaluator import UpperEvaluator, load_scenario
from molt.upper.graph import ContextChangedError
from molt.upper.types import ASSET_STATUSES, UPPER_DECISIONS

REPO = Path(__file__).resolve().parents[1]
FORBIDDEN_LOWER_KEYS = {"verdict", "lower_verdict", "gate_verdict", "core_verdict"}


def _git_status() -> str:
    return subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=REPO,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def _walk_keys(payload: object):
    if isinstance(payload, dict):
        for key, value in payload.items():
            yield key
            yield from _walk_keys(value)
    elif isinstance(payload, list):
        for value in payload:
            yield from _walk_keys(value)


@pytest.fixture(scope="module")
def upper_demo(tmp_path_factory: pytest.TempPathFactory) -> SimpleNamespace:
    state = tmp_path_factory.mktemp("upper-demo-state")
    before = _git_status()
    summary = run_demo(
        scenario_ref="c1b-memory-v1",
        state_dir=state,
        repo_root=REPO,
        reset=True,
        grace_s=30.0,
    )
    after = _git_status()
    evaluations = {
        path.name: json.loads(path.read_text(encoding="utf-8"))
        for path in sorted((state / "upper" / "evaluations").glob("*.json"))
    }
    decisions = {
        path.name: json.loads(path.read_text(encoding="utf-8"))
        for path in sorted((state / "upper" / "decisions").glob("*.json"))
    }
    return SimpleNamespace(
        summary=summary,
        state=state,
        evaluations=evaluations,
        decisions=decisions,
        before=before,
        after=after,
    )


def _phase(summary: dict, phase: str, asset_id: str) -> dict:
    matches = [
        item
        for item in summary["phases"][phase]
        if item["asset_id"] == asset_id
    ]
    assert len(matches) == 1
    return matches[0]


def test_transfer_replay_counts_and_historical_parity(upper_demo: SimpleNamespace) -> None:
    item = _phase(upper_demo.summary, "transfer", "mem-c1b-gen4")
    assert item["upper_decision"] == "keep"
    assert item["candidate"] == {"attempts": 12, "passed": 12, "pass_rate": 1.0}
    assert item["baseline"] == {"attempts": 10, "passed": 0, "pass_rate": 0.0}
    assert item["reference_counts"]["historical"] == {"runs": 12, "passed": 12}
    assert item["reference_counts"]["recomputed"] == {"runs": 12, "passed": 12}
    assert item["reference_counts"]["match"] is True
    # Actual replayable baseline snapshot set is 10 (2c: 6, followup: 4), not 8.
    assert item["baseline_reference_counts"]["historical"] == {"runs": 10, "passed": 0}
    assert item["baseline_reference_counts"]["recomputed"] == {"runs": 10, "passed": 0}
    assert item["baseline_reference_counts"]["match"] is True
    assert item["baseline_reference_counts"]["scenario_expected_match"] is True


def test_committed_evaluations_carry_passed_status_and_approval(
    upper_demo: SimpleNamespace,
) -> None:
    committed = {
        "mem-c1b-gen2",
        "mem-c1b-gen4",
    }
    seen = set()
    for evaluation in upper_demo.evaluations.values():
        if evaluation["asset_id"] not in committed or evaluation["task_set"] != "transfer":
            continue
        assert evaluation["status"] == "passed"
        assert evaluation["decision_id"]
        approval = evaluation["approval"]
        assert approval["approved_by"]
        assert approval["approved_at"].endswith("Z")
        seen.add(evaluation["asset_id"])
    assert seen == committed


def test_x8_negative_transfer_rolls_back_and_good_asset_keeps(
    upper_demo: SimpleNamespace,
) -> None:
    bad = _phase(upper_demo.summary, "regression", "mem-c1b-gen2")
    assert bad["upper_decision"] == "rollback"
    assert bad["candidate"] == {"attempts": 4, "passed": 0, "pass_rate": 0.0}
    assert bad["baseline"] == {"attempts": 2, "passed": 2, "pass_rate": 1.0}
    assert bad["reference_counts"]["historical"] == {"runs": 4, "passed": 0}
    assert bad["reference_counts"]["match"] is True
    assert bad["reason"].startswith("negative_transfer:")

    good = _phase(upper_demo.summary, "regression", "mem-c1b-gen4")
    assert good["upper_decision"] == "keep"
    assert good["candidate"] == {"attempts": 4, "passed": 4, "pass_rate": 1.0}
    assert good["baseline"] == {"attempts": 2, "passed": 2, "pass_rate": 1.0}

    final_state = upper_demo.summary["final_state"]
    assert final_state["active"] == ["mem-c1b-gen4"]
    assert final_state["rolled_back"] == ["mem-c1b-gen2"]


def test_upper_and_lower_records_stay_separate(upper_demo: SimpleNamespace) -> None:
    for decision in upper_demo.summary["decisions"]:
        assert decision["upper_decision"] in UPPER_DECISIONS
        assert "verdict" not in decision
        assert "lower_refs" in decision
        assert decision["lower_refs"]
        assert all(key.startswith("lower_") for key in decision["lower_refs"])
        assert not any(key in decision["lower_refs"] for key in FORBIDDEN_LOWER_KEYS)
        assert not (FORBIDDEN_LOWER_KEYS & set(_walk_keys(decision["metrics"])))
        assert decision["upper_scenario_version"] == "upper-c1b-1"

    for evaluation in upper_demo.evaluations.values():
        assert evaluation["upper_scenario_version"] == "upper-c1b-1"
        assert evaluation["mode"] == "replay"
        assert evaluation["upper_scenario_id"] == "c1b-memory-v1"
        assert all(key.startswith("lower_") for key in evaluation["lower_refs"])
        assert evaluation["lower_refs"]["lower_scenario_version"] == "probe-7"
        assert evaluation["lower_refs"]["lower_run_ids"]
        assert not (FORBIDDEN_LOWER_KEYS & set(_walk_keys(evaluation["metrics"])))


def test_future_capabilities_are_all_unimplemented(upper_demo: SimpleNamespace) -> None:
    expected = {"context_budget", "compaction", "vector_rag"}
    assert not any(
        hasattr(evaluator_module, name)
        for name in ("sol_pi", "compaction_runner", "context_budget_runner")
    )
    assert {
        capability["name"]
        for capability in upper_demo.summary["future_capabilities"]
    } == expected
    for evaluation in upper_demo.evaluations.values():
        capabilities = evaluation["future_capabilities"]
        assert {capability["name"] for capability in capabilities} == expected
        assert all(capability["status"] == "unimplemented" for capability in capabilities)


def test_telemetry_is_replay_only_and_not_fabricated(upper_demo: SimpleNamespace) -> None:
    for evaluation in upper_demo.evaluations.values():
        telemetry = evaluation["telemetry"]
        assert telemetry["cost_usd"] == 0.0
        assert telemetry["cost_source"] == "none"
        assert telemetry["repeated_prefix_tokens"] == {
            "value": None,
            "reason": "replay_no_model_context",
        }
        assert telemetry["retrieval_hits"] + telemetry["retrieval_misses"] == len(
            evaluation["tasks"]
        )
        assert sum(telemetry["errors"].values()) == len(evaluation["tasks"])
        assert telemetry["latency_ms"]["total"] > 0
        assert telemetry["latency_ms"]["mean"] > 0
        assert set(telemetry["latency_ms"]["by_task"]) == set(
            evaluation["metrics"]["per_task"]
        )
        if evaluation["asset_id"] == "mem-c1b-gen4":
            estimate = telemetry["context_tokens_estimate"]
            assert isinstance(estimate["value"], int) and estimate["value"] > 0
            assert estimate["method"] == "chars_div_4"
        if evaluation["asset_id"] == "v0-pro-max":
            assert telemetry["context_tokens_estimate"] == {
                "value": None,
                "reason": "baseline_no_memory_asset",
            }


def test_task_rows_can_be_traced_to_retrieval_and_source_run(
    upper_demo: SimpleNamespace,
) -> None:
    for evaluation in upper_demo.evaluations.values():
        for task in evaluation["tasks"]:
            assert task["provenance"]["kind"] == "replay"
            assert task["provenance"]["calls_model"] is False
            assert task["provenance"]["replay_of_run_id"] == task["source_run_id"]
            assert task["cost_usd"] == 0.0
            assert task["source_run_id"]
            assert task["solution_path"].endswith(".py.txt")
            if evaluation["asset_id"] == "v0-pro-max":
                assert task["retrieval"]["hits"] == []
                assert task["retrieval"]["empty"] is True
            else:
                hits = task["retrieval"]["hits"]
                assert hits
                assert hits[0]["asset_id"] == evaluation["asset_id"]
                assert hits[0]["score"] == 1.0
                assert hits[0]["source"]
    active = upper_demo.summary["active_retrieval_before_regression"]
    assert [hit["asset_id"] for hit in active["probe-order-x8"]["hits"]] == [
        "mem-c1b-gen2",
        "mem-c1b-gen4",
    ]
    active_after = upper_demo.summary["active_retrieval_after_regression"]
    assert [hit["asset_id"] for hit in active_after["probe-order-x8"]["hits"]] == [
        "mem-c1b-gen4"
    ]


def test_stub_assets_and_permission_change_are_not_executed(
    upper_demo: SimpleNamespace,
) -> None:
    executions = upper_demo.summary["governance"]["stub_executions"]
    assert executions
    assert all(item["executed"] is False for item in executions)
    assert all(item["external_calls"] == [] for item in executions)
    pending = upper_demo.summary["final_state"]["pending_permissions"]
    assert len(pending) == 1
    assert pending[0]["asset_id"] == "mcp-c1b-probe-read"
    assert pending[0]["approval_level"] == "strict"
    assert pending[0]["approved"] is False
    by_id = {
        asset["asset_id"]: asset for asset in upper_demo.summary["final_state"]["assets"]
    }
    assert by_id["skill-c1b-reflection"]["status"] == "draft"
    assert by_id["mcp-c1b-probe-read"]["status"] == "draft"
    assert all(asset["status"] in ASSET_STATUSES for asset in by_id.values())
    assert all(
        asset["upper_scenario_id"] == "c1b-memory-v1"
        and asset["upper_scenario_version"] == "upper-c1b-1"
        for asset in by_id.values()
    )


def test_demo_and_registry_state_stay_outside_repo(upper_demo: SimpleNamespace) -> None:
    assert upper_demo.after == upper_demo.before
    state = upper_demo.state.resolve()
    assert REPO.resolve() not in state.parents
    assert (state / "registry.json").is_file()
    manifest = json.loads((state / "demo.json").read_text(encoding="utf-8"))
    assert manifest["mode"] == "replay"
    assert manifest["calls_model"] is False


def test_demo_graph_has_committed_history_and_pointer_events(
    upper_demo: SimpleNamespace,
) -> None:
    summary = upper_demo.summary
    graph = summary["graph"]
    assert graph["merge_supported"] is False
    assert graph["delete_supported"] is False
    assert graph["active"]["branch_id"] == summary["graph_rollback"]["branch_id"]
    assert graph["active"]["node_id"] == summary["graph_rollback"]["new_active_node_id"]
    nodes = {node["node_id"]: node for node in graph["nodes"]}
    assert summary["graph_rollback"]["target_node_id"] in nodes
    assert summary["graph_rollback"]["from_node_id"] in nodes
    assert summary["graph_rollback"]["from_node_id"] != summary["graph_rollback"]["target_node_id"]
    assert len(graph["nodes"]) == 7
    branches = {branch["label"]: branch for branch in graph["branches"]}
    assert branches["ops"]["tip_node_id"] == graph["active"]["node_id"]
    real_nodes = [node for node in graph["nodes"] if node["origin"] == "real_run"]
    assert real_nodes
    assert all(node["eval_state"] == "replayed" for node in real_nodes)
    assert all(
        node["eval_state"] == "not_evaluated"
        for node in graph["nodes"]
        if node["origin"] != "real_run"
    )
    assert any(node["provenance_class"] == "real_replay" for node in real_nodes)
    assert "/home/" not in json.dumps(summary, ensure_ascii=False)
    assert "feisama-base" not in json.dumps(summary, ensure_ascii=False)

    for item in summary["phases"]["transfer"]:
        assert item["context_node_id"] in nodes
        assert item["context_status"] == "stable"
    for item in summary["phases"]["regression"]:
        assert item["context_node_id"] in nodes
        assert item["context_status"] == "stable"

    events = [
        json.loads(line)
        for line in (upper_demo.state / "upper" / "events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    rollbacks = [
        event
        for event in events
        if event["type"] == "commit" and event["op"] == "rollback"
    ]
    assert len(rollbacks) == 1
    rollback = rollbacks[0]
    assert rollback["old_node_id"] == summary["graph_rollback"]["from_node_id"]
    assert rollback["new_node_id"] == summary["graph_rollback"]["target_node_id"]
    assert rollback["old_active_branch"] == rollback["branch_id"]
    assert rollback["new_active_branch"] == rollback["branch_id"]
    for key in (
        "event_id",
        "type",
        "branch_id",
        "old_node_id",
        "new_node_id",
        "old_active_branch",
        "new_active_branch",
        "revision",
        "created_at",
    ):
        assert key in rollback
    assert summary["active_retrieval_after_regression"]["probe-order-x8"]["hits"][0][
        "asset_id"
    ] == "mem-c1b-gen4"

    evaluation_refs = [event for event in events if event["type"] == "evaluation_ref"]
    assert len(evaluation_refs) == 4
    for event in evaluation_refs:
        node = nodes[event["node_id"]]
        assert event["context_hash"] == node["context_hash"]
        assert event["origin"] == "real_run"
        assert event["eval_state"] == "replayed"
        assert event["evaluation_id"]
        assert event["context_node_id"] == event["node_id"]


def test_context_changed_during_evaluation_yields_no_decision(tmp_path: Path) -> None:
    class _ChangingBinding:
        node_id = "node-before-change"
        branch_id = "branch-test"
        context_hash = "a" * 64

        def __init__(self) -> None:
            self.calls = 0

        def assert_unchanged(self) -> None:
            self.calls += 1
            if self.calls >= 2:
                raise ContextChangedError("active context changed during replay")

    scenario = load_scenario("c1b-memory-v1", REPO)
    evaluator = UpperEvaluator(scenario, state_dir=tmp_path / "eval-state")
    evaluation, decision = evaluator.evaluate(
        "mem-c1b-gen4",
        "regression",
        context_binding=_ChangingBinding(),
    )
    assert decision is None
    assert evaluation.context_status == "context_changed"
    assert evaluation.metrics["context_status"] == "context_changed"
    assert evaluation.telemetry["context_status"] == "context_changed"
    assert evaluation.context_node_id == "node-before-change"

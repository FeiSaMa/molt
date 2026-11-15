"""Two-phase B1 demo: transfer keep/activate, then x8 rollback.

Run:
    python -m molt.upper.demo --scenario c1b-memory-v1 --mode replay \
        --state /tmp/opencode/molt-upper-state

All execution is REPLAY: recorded solution snapshots are re-scored, no model is
called, no core gate is invoked, and no Skill/MCP tool is executed. Assets are
stubs with synthetic replay approvals; the output says so explicitly.
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from typing import Any

from .evaluator import UpperEvaluator, load_scenario
from .graph import (
    ContextBinding,
    UpperGraphStore,
    build_context_manifest,
)
from .registry import UpperRegistry, UpperRegistryError
from .retrieval import RetrievalIndex
from .storage import atomic_write_json, ensure_outside_repo
from .types import (
    UpperDecision,
    UpperEvaluation,
    default_future_capabilities,
    sha256_text,
    utc_now,
)


def _reject_live(mode: str) -> None:
    if mode != "replay":
        raise SystemExit("B1 only supports --mode replay; no live model calls are made")


def _reset_state(state_dir: Path) -> None:
    for name in (
        "registry.json",
        "demo.json",
        "nodes.jsonl",
        "branches.json",
        "active.json",
        "events.jsonl",
        "lock",
    ):
        path = state_dir / name
        if path.exists():
            path.unlink()
    for name in ("evaluations", "decisions", "work", "upper"):
        path = state_dir / name
        if path.exists():
            shutil.rmtree(path)


def _evaluation_dir(state_dir: Path) -> Path:
    return state_dir / "upper" / "evaluations"


def _decision_dir(state_dir: Path) -> Path:
    return state_dir / "upper" / "decisions"


def _persist(
    state_dir: Path, evaluation: UpperEvaluation, decision: UpperDecision
) -> None:
    atomic_write_json(
        _evaluation_dir(state_dir) / f"{evaluation.evaluation_id}.json",
        evaluation.to_dict(),
    )
    atomic_write_json(
        _decision_dir(state_dir) / f"{decision.decision_id}.json",
        decision.to_dict(),
    )


def _evaluation_summary(
    evaluation: UpperEvaluation, decision: UpperDecision
) -> dict[str, Any]:
    return {
        "asset_id": evaluation.asset_id,
        "asset_version": evaluation.asset_version,
        "evaluation_id": evaluation.evaluation_id,
        "decision_id": decision.decision_id,
        "upper_decision": decision.upper_decision,
        "reason": decision.reason,
        "candidate": evaluation.metrics["candidate"],
        "baseline": evaluation.metrics["baseline"],
        "reference_counts": evaluation.reference_counts,
        "baseline_reference_counts": evaluation.baseline.get("reference_counts"),
        "retrieval": evaluation.metrics["retrieval"],
        "telemetry": evaluation.telemetry,
        "lower_refs": evaluation.lower_refs,
        "context_node_id": evaluation.context_node_id,
        "branch_id": evaluation.branch_id,
        "context_hash": evaluation.context_hash,
        "context_status": evaluation.context_status,
    }


def run_demo(
    *,
    scenario_ref: str,
    state_dir: Path | str,
    repo_root: Path | str | None = None,
    grace_s: float = 30.0,
    reset: bool = False,
) -> dict[str, Any]:
    repo_root = Path(repo_root or Path(__file__).resolve().parents[2]).resolve()
    state_dir = ensure_outside_repo(Path(state_dir), repo_root)
    scenario = load_scenario(scenario_ref, repo_root)
    if reset:
        _reset_state(state_dir)
    registry = UpperRegistry(state_dir, repo_root=repo_root)
    evaluator = UpperEvaluator(scenario, state_dir=state_dir, grace_s=grace_s)

    for asset_id in scenario.assets:
        asset = scenario.build_asset(asset_id)
        try:
            existing = registry.get(asset_id)
        except UpperRegistryError:
            registry.register(asset)
        else:
            if existing.status != "draft" or existing.hash != asset.hash:
                raise SystemExit(
                    f"state already contains {asset_id} in {existing.status}; rerun with --reset"
                )

    evaluate_ids = [
        asset_id for asset_id, spec in scenario.assets.items() if spec.get("evaluate")
    ]
    if not evaluate_ids:
        raise SystemExit("scenario has no assets marked for evaluation")
    governance = scenario.governance
    approver = str(governance.get("replay_approver", "demo-replay-approver"))
    operator = str(governance.get("replay_operator", "demo-replay-operator"))

    graph = UpperGraphStore(state_dir, repo_root=repo_root, registry=registry)
    if graph.nodes():
        raise SystemExit("graph state already exists; rerun with --reset")
    scenario_rel = scenario.path.relative_to(repo_root).as_posix()
    scenario_hash = sha256_text(scenario.path.read_text(encoding="utf-8"))
    root = graph.create_root(
        author=operator,
        message="B1 upper root",
        scenario_refs=[scenario_rel],
        scenario_version=scenario.version,
    )
    branch = graph.fork(
        "ops",
        root.node_id,
        author=operator,
        permission_scope="read:probes/report/2c",
        approval_level="standard",
    )
    branch_id = branch.branch_id

    phases: dict[str, list[dict[str, Any]]] = {"transfer": [], "regression": []}
    decisions: list[UpperDecision] = []
    transfer_evaluations: dict[str, UpperEvaluation] = {}
    for asset_id in evaluate_ids:
        evaluation, decision = evaluator.evaluate(
            asset_id, "transfer", asset=registry.get(asset_id)
        )
        if decision is None:
            raise SystemExit("context changed during transfer evaluation")
        _persist(state_dir, evaluation, decision)
        decisions.append(decision)
        phases["transfer"].append(_evaluation_summary(evaluation, decision))
        transfer_evaluations[asset_id] = evaluation
        registry.mark_evaluated(asset_id)
        if decision.upper_decision == "keep":
            registry.approve(asset_id, approved_by=approver)
            registry.activate(asset_id)
        else:
            registry.reject(
                asset_id, rejected_by=approver, reason=decision.reason
            )

    def _asset_ref(asset_id: str) -> dict[str, str]:
        asset = registry.get(asset_id)
        return {"asset_id": asset.asset_id, "version": asset.version, "hash": asset.hash}

    def _commit_context(
        parent_node_id: str,
        asset_ids: list[str],
        evaluations: list[UpperEvaluation],
        message: str,
    ):
        evaluation_ids = [evaluation.evaluation_id for evaluation in evaluations]
        manifest = build_context_manifest(
            parents=[parent_node_id],
            policy_memory_refs=[_asset_ref(asset_id) for asset_id in asset_ids],
            project_requirements={"ref": scenario_rel, "hash": scenario_hash},
            budget={"limits": {"mode": "replay", "max_model_calls": 0}},
            scenario_refs=[scenario_rel],
            scenario_version=scenario.version,
            eval_refs=evaluation_ids,
            task_refs=list(scenario.phase_tasks("transfer")),
        )
        candidate = graph.create_candidate(
            parent_node_id,
            manifest,
            evaluation_ids[0],
            author=operator,
            message=f"candidate: {message}",
            evidence_refs=[scenario_rel],
            origin="real_run",
            eval_state="replayed",
            source_category="probes/report/2c",
        )
        approval = {
            "approved_by": approver,
            "approved_at": utc_now(),
            "approval_level": "standard",
            "message": message,
        }
        # The commit is bound to the primary evaluation: its persisted record
        # must carry the same approval and a passed/keep decision.
        primary = evaluations[0]
        primary.bind_approval(
            approved_by=approval["approved_by"],
            approved_at=approval["approved_at"],
            approval_level=approval["approval_level"],
        )
        atomic_write_json(
            _evaluation_dir(state_dir) / f"{primary.evaluation_id}.json",
            primary.to_dict(),
        )
        commit = graph.commit_candidate(
            candidate.node_id,
            parent_node_id,
            approval,
            evaluation_id=primary.evaluation_id,
            branch_id=branch_id,
            author=approver,
        )
        return commit

    gen2_transfer_node = _commit_context(
        root.node_id,
        ["mem-c1b-gen2"],
        [transfer_evaluations["mem-c1b-gen2"]],
        "keep: gen2 x5-x7 transfer",
    )
    gen4_transfer_node = _commit_context(
        gen2_transfer_node.node_id,
        ["mem-c1b-gen4"],
        [transfer_evaluations["mem-c1b-gen4"]],
        "keep: gen4 x5-x7 transfer",
    )
    _commit_context(
        gen4_transfer_node.node_id,
        ["mem-c1b-gen2", "mem-c1b-gen4"],
        [
            transfer_evaluations["mem-c1b-gen2"],
            transfer_evaluations["mem-c1b-gen4"],
        ],
        "active: both memories after transfer",
    )
    for asset_id, node in (
        ("mem-c1b-gen2", gen2_transfer_node),
        ("mem-c1b-gen4", gen4_transfer_node),
    ):
        evaluation = transfer_evaluations[asset_id]
        evaluation.bind_context(
            node_id=node.node_id,
            branch_id=branch_id,
            context_hash=node.context_hash,
        )
        atomic_write_json(
            _evaluation_dir(state_dir) / f"{evaluation.evaluation_id}.json",
            evaluation.to_dict(),
        )
        graph.record_evaluation_ref(
            node.node_id,
            evaluation.evaluation_id,
            origin="real_run",
            eval_state="replayed",
            source_category="probes/report/2c",
            evidence_refs=[scenario_rel],
            author=operator,
        )
        for item in phases["transfer"]:
            if item["asset_id"] == asset_id:
                item.update(
                    {
                        "context_node_id": node.node_id,
                        "branch_id": branch_id,
                        "context_hash": node.context_hash,
                        "context_status": "stable",
                    }
                )

    def _graph_active_assets() -> list:
        return [registry.get(ref["asset_id"]) for ref in graph.active_asset_refs()]

    active_index = RetrievalIndex(_graph_active_assets())
    active_retrieval = {
        task_id: active_index.retrieve(scenario.family_id, task_id=task_id).to_dict()
        for task_id in scenario.phase_tasks("regression")
    }

    binding = ContextBinding.capture(graph, branch_id=branch_id)
    regression_decisions: dict[str, UpperDecision] = {}
    for asset_id in evaluate_ids:
        evaluation, decision = evaluator.evaluate(
            asset_id,
            "regression",
            asset=registry.get(asset_id),
            context_binding=binding,
        )
        if decision is None:
            atomic_write_json(
                _evaluation_dir(state_dir) / f"{evaluation.evaluation_id}.json",
                evaluation.to_dict(),
            )
            raise SystemExit(
                f"context changed while evaluating {asset_id}; no decision written"
            )
        _persist(state_dir, evaluation, decision)
        decisions.append(decision)
        regression_decisions[asset_id] = decision
        phases["regression"].append(_evaluation_summary(evaluation, decision))
        graph.record_evaluation_ref(
            evaluation.context_node_id or graph.active_pointer()["node_id"],
            evaluation.evaluation_id,
            origin="real_run",
            eval_state="replayed",
            source_category="probes/report/2c",
            evidence_refs=[scenario_rel],
            author=operator,
        )

    graph_rollback_info: dict[str, Any] | None = None
    if regression_decisions.get("mem-c1b-gen2") and (
        regression_decisions["mem-c1b-gen2"].upper_decision == "rollback"
    ):
        from_node_id = graph.branch(branch_id).tip_node_id
        graph.rollback(
            branch_id,
            gen4_transfer_node.node_id,
            expected_tip=from_node_id,
            expected_active_branch=branch_id,
            author=approver,
            reason="negative_transfer:x8:mem-c1b-gen2",
        )
        graph_rollback_info = {
            "branch_id": branch_id,
            "from_node_id": from_node_id,
            "target_node_id": gen4_transfer_node.node_id,
            "new_active_node_id": graph.active_pointer()["node_id"],
            "semantics": "move-head-only; history nodes retained",
        }
    for asset_id, decision in regression_decisions.items():
        asset = registry.get(asset_id)
        if decision.upper_decision == "rollback" and asset.status == "active":
            registry.rollback(
                asset_id, reason=decision.reason, rolled_back_by=approver
            )

    baseline_evaluations = {}
    for phase in ("transfer", "regression"):
        baseline = evaluator.baseline_evaluation(phase)
        atomic_write_json(
            _evaluation_dir(state_dir) / f"{baseline.evaluation_id}.json",
            baseline.to_dict(),
        )
        baseline_evaluations[phase] = {
            "evaluation_id": baseline.evaluation_id,
            "reference_counts": baseline.reference_counts,
            "tasks": baseline.tasks,
        }

    post_rollback_index = RetrievalIndex(_graph_active_assets())
    active_retrieval_after_regression = {
        task_id: post_rollback_index.retrieve(
            scenario.family_id, task_id=task_id
        ).to_dict()
        for task_id in scenario.phase_tasks("regression")
    }

    governance_out: dict[str, Any] = {
        "approval_note": (
            "Synthetic replay approvals only; not a real human approval. "
            "No asset is executed in B1."
        ),
        "stub_executions": [],
    }
    for asset_id in scenario.assets:
        governance_out["stub_executions"].append(registry.execute_stub(asset_id))
    permission_change = governance.get("permission_change")
    if permission_change:
        registry.request_permission_change(
            permission_change["asset_id"],
            scope=permission_change["requested_scope"],
            requested_by=operator,
            reason=permission_change["reason"],
        )

    summary = {
        "mode": "replay",
        "calls_model": False,
        "upper_scenario_id": scenario.scenario_id,
        "upper_scenario_version": scenario.version,
        "scenario_ref": scenario_rel,
        "state_scope": "external",
        "phases": phases,
        "active_retrieval_before_regression": active_retrieval,
        "active_retrieval_after_regression": active_retrieval_after_regression,
        "decisions": [decision.to_public_dict() for decision in decisions],
        "baseline_evaluations": baseline_evaluations,
        "graph": graph.to_public_dict(),
        "graph_rollback": graph_rollback_info,
        "active_source": "graph.active_pointer",
        "asset_lifecycle_source": "registry.json asset status (approval lifecycle only)",
        "final_state": registry.public_state(),
        "governance": governance_out,
        "future_capabilities": [
            capability.to_dict() for capability in default_future_capabilities()
        ],
        "notes": [
            "REPLAY only: recorded solutions re-scored with ProbeVerifier; no model calls.",
            "Upper scenario/version and evidence are independent of core gate/bench/probes.",
            "lower_refs are references only; no lower verdict is merged into upper_decision.",
            "Future capabilities (context_budget/compaction/vector_rag) are unimplemented.",
            "MCP/Skill assets are stubs and were not executed.",
            "Graph active pointer is authoritative for the active context; registry status is asset approval lifecycle.",
            "Rollback moves the branch tip/active pointer only; no node is deleted or created.",
        ],
    }
    atomic_write_json(state_dir / "demo.json", summary)
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", default="c1b-memory-v1")
    parser.add_argument("--mode", choices=("replay",), default="replay")
    parser.add_argument("--state", default="/tmp/opencode/molt-upper-state")
    parser.add_argument("--grace-s", type=float, default=30.0)
    parser.add_argument("--reset", action="store_true")
    args = parser.parse_args(argv)
    _reject_live(args.mode)
    summary = run_demo(
        scenario_ref=args.scenario,
        state_dir=args.state,
        grace_s=args.grace_s,
        reset=args.reset,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

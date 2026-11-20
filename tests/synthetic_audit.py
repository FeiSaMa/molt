"""Synthetic v1 audit evidence, independent of retired shells and benchmarks.

Costs, execution outcomes and provenance here are invented test data. Legacy
fixture names in conftest are aliases only, not evidence of a real execution.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

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

HASH = "a" * 64
TS = "2026-01-01T00:00:00Z"


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


def build_audit_fixture(root: Path):
    """Create 10 paired search tasks, 5 report tasks and nonzero overhead."""
    root.mkdir()  # Refuse to overwrite existing state.
    scenario = Scenario(
        "1.0.0", "audit-synthetic-1", "audit-synthetic", "Synthetic v1 audit",
        "audit-synthetic", "1.0.0", HASH,
        {"search": [f"synthetic-search-{i}" for i in range(10)],
         "report": [f"synthetic-report-{i}" for i in range(5)]},
        Policy(0, 0.0, BudgetCaps(1.0, 1.0)), Isolation("none"),
        {"provenance": "synthetic"},
    )
    scenario = Scenario.from_dict(scenario.to_dict())
    limits = Budget(100, 1000, 1, 1)
    overhead = 0.003
    all_runs, results, sides = [], [], []
    for label, cost in (("baseline", 0.02), ("candidate", 0.01)):
        genome = Genome(
            "1.0.0", scenario.scenario_version, f"synthetic-{label}", None,
            scenario.shell_id, scenario.shell_version, Switches("off", "minimal", "bash"),
            "synthetic/prompt", "synthetic/tools", [],
            {"runtime_id": "fixture", "version": "synthetic-1"}, limits,
            {"synthetic/prompt": HASH, "synthetic/tools": HASH}, {"provenance": "synthetic"},
        )
        genome = Genome.from_dict(genome.to_dict())
        _write(root / "genomes" / f"{genome.genome_id}.json", genome.to_dict())
        side_runs = []
        for scope, tasks in scenario.task_sets.items():
            for task in tasks:
                run = Run(
                    "1.0.0", f"{label}-{task}", scenario.scenario_version,
                    genome.genome_id, task, scope, 1, scenario.shell_version,
                    HASH, "fixed", TS, TS, "passed", Usage(0, 0, cost, 1, 1),
                    Verification(HASH, HASH, True), {},
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
        spend = overhead if label == "candidate" else 0.0
        for scope in ("search", "report"):
            refs = [r for r in side_runs if scope == "report" or r.scope == "search"]
            blocks = {"search": ScoreBlock.from_dict(metrics_from_runs(
                [r for r in refs if r.scope == "search"], scenario.task_sets["search"],
                overhead_spend_usd=spend,
            ).to_score_block(), "search")}
            if scope == "report":
                blocks["holdout"] = ScoreBlock.from_dict(metrics_from_runs(
                    [r for r in refs if r.scope == "report"], scenario.task_sets["report"],
                ).to_score_block(), "holdout")
            result = Result(
                "1.0.0", f"result-{label}-{scope}", scenario.scenario_version,
                genome.genome_id, scope, "fixed", [r.run_id for r in refs], blocks,
                TS, {"provenance": "synthetic"},
            )
            result = Result.from_dict(result.to_dict())
            _write(root / "results" / f"{result.result_id}.json", result.to_dict())
            results.append(result)
        sides.append(SideEval(
            genome.genome_id, scenario.scenario_version,
            tuple(TaskEval(t, True, cost) for t in scenario.task_sets["search"]),
            limits, 0.0, spend,
        ))
        all_runs.extend(side_runs)
    _write(root / "ledger" / "prop-synthetic.jsonl", {
        "kind": "overhead", "owner_id": "prop-synthetic", "cost_usd": overhead,
        "provenance": "synthetic",
    })
    decision = decide(
        scenario.policy, *sides, decision_id="synthetic-decision-1",
        scenario_version=scenario.scenario_version, shell_version=scenario.shell_version,
        verifier_hash=HASH, created_at=TS,
    )
    decision = replace(decision, ext={
        "inputs": {"baseline_result_id": "result-baseline-search",
                   "candidate_result_id": "result-candidate-search"},
        "overhead_spend_usd": overhead, "provenance": "synthetic",
    })
    _write(root / "decisions" / f"{decision.decision_id}.json", decision.to_dict())
    for index, event_type in enumerate(("holdout.accessed", "report.scored")):
        event = make_event(scope="report", event_type=event_type,
                           event_id=f"synthetic-report-{index}", ts=TS,
                           payload=({"task_ids": scenario.task_sets["report"]}
                                    if event_type == "holdout.accessed" else
                                    {"result_id": "result-candidate-report"}))
        _write(root / "events" / "report" / f"{index}.jsonl", event.to_dict())
    bundle = evidence_bundle(scenario_id=scenario.scenario_id,
                             scenario_version=scenario.scenario_version,
                             runs=all_runs, results=results, decisions=[decision])
    _, manifest = redact_evidence(bundle)
    _write(root / "redaction" / f"{scenario.scenario_id}.json", manifest)
    return StateStore(root), scenario, {
        "baseline_genome": "synthetic-baseline", "candidate_genome": "synthetic-candidate",
        "provenance": "synthetic",
        "redaction_manifest": str(root / "redaction" / f"{scenario.scenario_id}.json"),
    }

"""Retained v1 proposal/ledger contracts using only synthetic evidence."""

from dataclasses import replace

import pytest

from molt.core.engine import Engine, EngineConfig
from molt.core.errors import ProposalError
from molt.core.ledger import Ledger
from molt.core.metrics import metrics_from_runs
from molt.core.proposer import ProposalDraft, build_proposal
from molt.core.types import HistoryEntry, SearchHistory, Switches

TS = "2026-01-01T00:00:00Z"


def _inputs(artifacts):
    store, scenario, summary = artifacts
    parent = store.read_genome(summary["baseline_genome"])
    history = SearchHistory(scenario.scenario_version, parent.genome_id)
    runs = [r for r in store.list_runs()
            if r.genome_id == parent.genome_id and r.scope == "search"]
    for run in runs:
        history.add(HistoryEntry(run.task_id, True, run.usage.cost_usd))
    return store, parent, history, runs


def test_old_history_is_filtered(demo_copy):
    _, parent, history, _ = _inputs(demo_copy)
    old = replace(history, scenario_version="synthetic-old")
    proposal = build_proposal(old, parent, draft=None, created_at=TS)
    assert proposal.ext["history"]["excluded"] == 10
    assert proposal.ext["history"]["entries"] == 0
    assert proposal.ext["source"] == "grid"


def test_fallback_roundtrip_and_overhead_accounting(demo_copy):
    store, parent, history, runs = _inputs(demo_copy)
    empty = build_proposal(history, parent, draft=None, created_at=TS)
    draft = ProposalDraft(
        switches=Switches("low", "full", "bash"), diagnosis="synthetic", reason="synthetic",
        model="synthetic", prompt_sha256=empty.ext["prompt_sha256"],
        response_sha256=empty.ext["response_sha256"], usage=dict(empty.ext["usage"]),
        cost_usd=0.1, cost_source="provider", source="llm",
    )
    proposal = build_proposal(
        history, parent, {"thinking": ("high",), "context": ("full",), "tools": ("bash",)},
        draft=draft, created_at=TS,
    )
    assert proposal.ext["fallback"] == {"used": True, "reason": "switch_out_of_domain"}
    assert proposal.switches == Switches("high", "full", "bash")
    assert proposal.cost_usd == 0.1
    store.write_proposal(proposal)
    assert store.read_proposal(proposal.proposal_id).to_dict() == proposal.to_dict()
    ledger = Ledger(store.root)
    # Record-only compatibility path: no task/runtime/verifier is invoked.
    engine = Engine(store, None, None, None, EngineConfig(
        store.root, store.root.parent / "unused-work", parent.shell_version, "a" * 64,
    ))
    engine.record_proposal(proposal, event_id="synthetic-proposal", ts=TS)
    engine.record_proposal(proposal, event_id="synthetic-proposal-retry", ts=TS)
    assert len(ledger.read(proposal.proposal_id)) == 1
    assert ledger.total_cost(proposal.proposal_id, kind="overhead") == 0.1
    tasks = [r.task_id for r in runs]
    before = metrics_from_runs(runs, tasks)
    after = metrics_from_runs(runs, tasks, overhead_spend_usd=proposal.cost_usd)
    assert after.cost_total_usd == before.cost_total_usd
    assert after.cost_per_solved_usd == before.cost_per_solved_usd
    assert after.overhead_spend_usd == proposal.cost_usd


def test_holdout_mutation_does_not_enter_search_proposal(demo_copy):
    store, parent, history, _ = _inputs(demo_copy)
    before = build_proposal(history, parent, draft=None, created_at=TS)
    report = store.read_result("result-baseline-report")
    store.write_result(replace(report, scores={
        **report.scores, "holdout": replace(report.scores["holdout"], solved=0),
    }))
    assert build_proposal(history, parent, draft=None, created_at=TS).to_dict() == before.to_dict()


def test_unknown_domain_is_rejected(demo_copy):
    _, parent, history, _ = _inputs(demo_copy)
    with pytest.raises(ProposalError):
        build_proposal(history, parent, {"thinking": ("off",), "context": ("full",)},
                       draft=None, created_at=TS)

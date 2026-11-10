"""B2-D1：LLM proposer 契约、ext 映射、fallback、成本归属、版本过滤。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from molt.adapters.llm_proposer import LLMProposer
from molt.core.errors import ProposalError
from molt.core.hash import canonical_json_text
from molt.core.ledger import Ledger
from molt.core.metrics import metrics_from_runs
from molt.core.proposer import (
    COUNTED_AS,
    USAGE_KEYS,
    build_proposal,
    proposal_ext_keys,
)
from molt.core.store import StateStore
from molt.core.types import (
    Genome,
    HistoryEntry,
    Result,
    Run,
    SearchHistory,
)
from molt.shell_loader import load_shell_module

ROOT = Path(__file__).resolve().parents[1]
GENOME_PATH = ROOT / "shells" / "code-repair" / "genomes" / "v0.json"
FIXED = "2026-01-01T00:00:00Z"
EXT_KEYS = set(proposal_ext_keys())


def genome() -> Genome:
    return Genome.from_dict(json.loads(GENOME_PATH.read_text(encoding="utf-8")))


def history(
    *,
    version: str | None = None,
    passed: bool = True,
    tasks: tuple[str, ...] = ("smoke-add", "smoke-even"),
) -> SearchHistory:
    parent = genome()
    result = SearchHistory(
        scenario_version=version or parent.scenario_version,
        genome_id=parent.genome_id,
    )
    for task_id in tasks:
        result.add(HistoryEntry(task_id, passed, 1.0))
    return result


def response(
    content: Any,
    *,
    usage: dict[str, Any] | None = None,
    cost_usd: float | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "choices": [
            {
                "message": {
                    "content": content if isinstance(content, str) else canonical_json_text(content)
                }
            }
        ],
        "usage": usage or {"prompt_tokens": 1000, "completion_tokens": 500, "total_tokens": 1500},
    }
    if cost_usd is not None:
        payload["cost_usd"] = cost_usd
    return payload


class FakeTransport:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.calls: list[dict[str, Any]] = []

    def __call__(self, request: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(request)
        return self.payload


def make_proposer(
    payload: dict[str, Any],
    *,
    max_cost_usd: float | None = None,
    ledger: Ledger | None = None,
) -> tuple[LLMProposer, FakeTransport]:
    transport = FakeTransport(payload)
    proposer = LLMProposer(
        transport=transport,
        clock=lambda: FIXED,
        max_cost_usd=max_cost_usd,
        ledger=ledger,
    )
    return proposer, transport


def assert_ext_contract(proposal) -> None:
    ext = proposal.ext
    assert EXT_KEYS <= set(ext)
    assert set(ext["usage"]) == set(USAGE_KEYS)
    assert ext["counted_as"] == COUNTED_AS
    assert ext["source"] in ("llm", "grid")
    assert ext["fallback"]["used"] in (True, False)
    assert isinstance(ext["fallback"]["reason"], (str, type(None)))
    assert ext["cost_source"] in ("provider", "estimate", "none")
    assert len(ext["prompt_sha256"]) == 64
    assert len(ext["response_sha256"]) == 64
    assert "excluded" in ext["history"]


def test_llm_success_ext_contract_and_estimated_cost() -> None:
    switches = {"thinking": "low", "context": "full", "tools": "bash"}
    payload = response({"diagnosis": "cheaper", "switches": switches, "reason": "cost"})
    proposer, transport = make_proposer(payload)
    proposal = proposer.propose(history(), genome())

    assert proposal.proposer_id == "llm:deepseek-flash"
    assert proposal.switches.to_dict() == switches
    assert proposal.parent_id == genome().genome_id
    assert proposal.cost_usd == pytest.approx(0.00049)
    assert proposal.ext["source"] == "llm"
    assert proposal.ext["fallback"] == {"used": False, "reason": None}
    assert proposal.ext["cost_source"] == "estimate"
    assert proposal.ext["model"] == "deepseek-flash"
    assert proposal.ext["diagnosis"] == "cheaper"
    assert ext_usage(proposal) == {
        "input": 1000,
        "output": 500,
        "cache_read": 0,
        "cache_write": 0,
        "reasoning": 0,
        "total": 1500,
    }
    assert len(transport.calls) == 1
    assert_ext_contract(proposal)


def ext_usage(proposal) -> dict[str, int]:
    return dict(proposal.ext["usage"])


def test_provider_reported_cost_marks_provider() -> None:
    payload = response(
        {
            "diagnosis": "d",
            "switches": {"thinking": "low", "context": "minimal", "tools": "bash"},
            "reason": "r",
        },
        cost_usd=0.25,
    )
    proposer, _ = make_proposer(payload)
    proposal = proposer.propose(history(), genome())
    assert proposal.cost_usd == 0.25
    assert proposal.ext["cost_source"] == "provider"


@pytest.mark.parametrize(
    ("content", "reason", "calls"),
    [
        ("not json", "invalid_json", 2),
        (
            {"diagnosis": "d", "switches": {}, "reason": "r"},
            "empty_candidate",
            1,
        ),
        (
            {
                "diagnosis": "d",
                "switches": {"thinking": "max", "context": "full", "tools": "bash"},
                "reason": "r",
            },
            "switch_out_of_domain",
            1,
        ),
    ],
)
def test_illegal_outputs_fall_back_to_grid(content: Any, reason: str, calls: int) -> None:
    proposer, transport = make_proposer(response(content))
    proposal = proposer.propose(history(), genome())

    assert len(transport.calls) == calls
    assert proposal.ext["source"] == "grid"
    assert proposal.proposer_id == "grid"
    assert proposal.ext["fallback"] == {"used": True, "reason": reason}
    assert proposal.switches != genome().switches
    assert proposal.cost_usd >= 0
    assert_ext_contract(proposal)


def test_fallback_still_persists_proposal_record(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state")
    store.ensure()
    for content in (
        "not json",
        {"diagnosis": "d", "switches": {}, "reason": "r"},
    ):
        proposer, _ = make_proposer(response(content))
        proposal = proposer.propose(history(), genome())
        store.write_proposal(proposal)
        restored = store.read_proposal(proposal.proposal_id)
        assert restored.to_dict() == proposal.to_dict()
        assert restored.ext["fallback"]["used"] is True


def test_over_budget_falls_back_but_keeps_cost() -> None:
    switches = {"thinking": "low", "context": "full", "tools": "bash"}
    proposer, _ = make_proposer(
        response({"diagnosis": "d", "switches": switches, "reason": "r"}),
        max_cost_usd=0.0001,
    )
    proposal = proposer.propose(history(), genome())
    assert proposal.ext["fallback"]["used"] is True
    assert proposal.ext["fallback"]["reason"] == "proposal_over_budget"
    assert proposal.cost_usd == pytest.approx(0.00049)


def test_missing_api_key_returns_grid_without_network(monkeypatch) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("MOLT_PROPOSER_API_KEY", raising=False)
    proposer = LLMProposer(transport=None, api_key=None, clock=lambda: FIXED)
    assert proposer.available is False
    proposal = proposer.propose(history(), genome())
    assert proposal.ext["fallback"] == {"used": True, "reason": "missing_api_key"}
    assert proposal.ext["source"] == "grid"
    assert proposal.cost_usd == 0.0
    assert proposal.ext["cost_source"] == "none"


def test_old_version_history_is_filtered() -> None:
    parent = genome()
    old = history(version="demo-0")
    transport = FakeTransport(
        response(
            {
                "diagnosis": "d",
                "switches": {"thinking": "low", "context": "full", "tools": "bash"},
                "reason": "r",
            }
        )
    )
    proposer = LLMProposer(transport=transport, clock=lambda: FIXED)
    proposal = proposer.propose(old, parent)

    request = transport.calls[0]
    user_payload = json.loads(request["messages"][1]["content"])
    assert user_payload["history"]["scenario_version"] == parent.scenario_version
    assert user_payload["history"]["entries"] == []
    assert proposal.ext["history"]["excluded"] == 2
    assert proposal.ext["history"]["entries"] == 0


def test_build_proposal_filters_old_history_without_llm() -> None:
    parent = genome()
    proposal = build_proposal(
        history(version="demo-0"),
        parent,
        draft=None,
        created_at=FIXED,
    )
    assert proposal.ext["history"]["excluded"] == 2
    assert proposal.ext["history"]["entries"] == 0
    assert proposal.ext["source"] == "grid"


def test_harness_rejects_old_history() -> None:
    module = load_shell_module(ROOT, "shells/code-repair/harness.py", "molt_test_harness_versions")
    harness = module.CodeRepairHarness(
        runtime=None,
        verifier=None,
        repo_root=ROOT,
        clock=lambda: FIXED,
        llm_proposer=lambda history, parent: build_proposal(
            history, parent, draft=None, created_at=FIXED
        ),
    )
    with pytest.raises(ValueError):
        harness.propose(history(version="demo-0"), genome())


def test_harness_defaults_to_grid() -> None:
    module = load_shell_module(ROOT, "shells/code-repair/harness.py", "molt_test_harness_grid")
    harness = module.CodeRepairHarness(
        runtime=None, verifier=None, repo_root=ROOT, clock=lambda: FIXED
    )
    proposal = harness.propose(history(), genome())
    assert proposal.ext["source"] == "grid"
    assert proposal.ext["fallback"] == {"used": False, "reason": None}
    assert proposal.switches.to_dict() == {
        "thinking": "low",
        "context": "full",
        "tools": "bash",
    }


def test_prompt_contains_only_search_history() -> None:
    payload = response(
        {
            "diagnosis": "d",
            "switches": {"thinking": "low", "context": "full", "tools": "bash"},
            "reason": "r",
        }
    )
    proposer, transport = make_proposer(payload)
    proposer.propose(history(), genome())
    text = canonical_json_text(transport.calls[0])
    assert "holdout" not in text
    assert "report" not in text
    assert "smoke-add" in text


def test_prompt_and_response_hashes_are_stable() -> None:
    payload = response(
        {
            "diagnosis": "d",
            "switches": {"thinking": "low", "context": "full", "tools": "bash"},
            "reason": "r",
        }
    )
    proposer, _ = make_proposer(payload)
    first = proposer.propose(history(), genome())
    second = proposer.propose(history(), genome())
    assert first.to_dict() == second.to_dict()
    assert first.ext["prompt_sha256"] == second.ext["prompt_sha256"]
    assert first.ext["response_sha256"] == second.ext["response_sha256"]


def test_adapter_ledger_records_overhead_once(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "state")
    payload = response(
        {
            "diagnosis": "d",
            "switches": {"thinking": "low", "context": "full", "tools": "bash"},
            "reason": "r",
        }
    )
    proposer, _ = make_proposer(payload, ledger=ledger)
    proposal = proposer.propose(history(), genome())
    proposer.propose(history(), genome())

    entries = ledger.read(proposal.proposal_id)
    assert len(entries) == 1
    assert entries[0]["kind"] == "overhead"
    assert entries[0]["cost_usd"] == proposal.cost_usd
    assert ledger.total_cost(proposal.proposal_id, kind="overhead") == proposal.cost_usd


def test_proposal_cost_never_enters_cost_per_solved(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "state")
    payload = response(
        {
            "diagnosis": "d",
            "switches": {"thinking": "low", "context": "full", "tools": "bash"},
            "reason": "r",
        }
    )
    proposer, _ = make_proposer(payload, ledger=ledger)
    proposal = proposer.propose(history(), genome())
    ledger.append(
        "run-a",
        {"kind": "execution", "cost_usd": 1.0, "task_id": "smoke-add", "outcome": "passed"},
    )
    ledger.append(
        "run-b",
        {"kind": "execution", "cost_usd": 1.0, "task_id": "smoke-even", "outcome": "passed"},
    )

    runs = [_run("run-a", "smoke-add", 1.0), _run("run-b", "smoke-even", 1.0)]
    metrics = metrics_from_runs(runs, ["smoke-add", "smoke-even"])
    assert metrics.cost_total_usd == 2.0
    assert metrics.cost_per_solved_usd == 1.0
    assert proposal.cost_usd not in (metrics.cost_total_usd, metrics.cost_per_solved_usd)

    overhead = sum(
        ledger.total_cost(owner, kind="overhead")
        for owner in ledger.owners()
        if owner.startswith("prop-")
    )
    assert overhead == proposal.cost_usd


def test_poisoned_holdout_does_not_change_proposal(demo_copy) -> None:
    store, scenario, summary = demo_copy
    parent = store.read_genome(summary["baseline_genome"])
    runs = [
        run
        for run in store.list_runs()
        if run.genome_id == parent.genome_id
        and run.scope == "search"
        and run.attempt == 1
        and run.task_id in scenario.task_sets["search"]
    ]
    search = SearchHistory(scenario_version=parent.scenario_version, genome_id=parent.genome_id)
    for run in sorted(runs, key=lambda item: item.task_id):
        search.add(HistoryEntry(run.task_id, run.outcome == "passed", run.usage.cost_usd))

    payload = response(
        {
            "diagnosis": "d",
            "switches": {"thinking": "low", "context": "full", "tools": "bash"},
            "reason": "r",
        }
    )
    proposer = LLMProposer(transport=FakeTransport(payload), clock=lambda: FIXED)
    first = proposer.propose(search, parent)

    report = next(
        result
        for result in store.list_results()
        if result.scope == "report" and result.genome_id == parent.genome_id
    )
    data = report.to_dict()
    data["scores"]["holdout"] = {
        "solved": 0,
        "total": 1,
        "pass_at_1_first_attempt": 0.0,
        "cost_total_usd": 0.0,
        "cost_per_solved_usd": None,
        "invalid_ratio": 0.0,
    }
    store.write_result(Result.from_dict(data))

    second = proposer.propose(search, parent)
    assert first.to_dict() == second.to_dict()


def test_invalid_draft_is_defensively_downgraded() -> None:
    parent = genome()
    draft = build_proposal(
        history(),
        parent,
        draft=None,
        created_at=FIXED,
    )
    # 构造一个越域草稿：借用合法 Proposal 的 ext 证据，仅 switches 越域。
    from molt.core.proposer import ProposalDraft
    from molt.core.types import Switches

    bad = ProposalDraft(
        switches=Switches.from_dict({"thinking": "low", "context": "full", "tools": "bash"}),
        diagnosis="d",
        reason="r",
        model="deepseek-flash",
        prompt_sha256=draft.ext["prompt_sha256"],
        response_sha256=draft.ext["response_sha256"],
        usage=dict(draft.ext["usage"]),
        cost_usd=0.1,
        cost_source="provider",
        source="llm",
    )
    narrowed = {"thinking": ("high",), "context": ("full",), "tools": ("bash",)}
    proposal = build_proposal(history(), parent, narrowed, draft=bad, created_at=FIXED)
    assert proposal.ext["fallback"] == {"used": True, "reason": "switch_out_of_domain"}
    assert proposal.switches.to_dict() == {
        "thinking": "high",
        "context": "full",
        "tools": "bash",
    }


def test_unknown_domain_rejected() -> None:
    with pytest.raises(ProposalError):
        build_proposal(
            history(),
            genome(),
            {"thinking": ("off",), "context": ("full",)},
            draft=None,
            created_at=FIXED,
        )


def _run(run_id: str, task_id: str, cost: float) -> Run:
    return Run.from_dict(
        {
            "schema_version": "1.0.0",
            "run_id": run_id,
            "scenario_version": genome().scenario_version,
            "genome_id": genome().genome_id,
            "task_id": task_id,
            "scope": "search",
            "attempt": 1,
            "shell_version": "1.0.0",
            "verifier_hash": "a" * 64,
            "seed_control": "none",
            "started_at": FIXED,
            "finished_at": FIXED,
            "outcome": "passed",
            "usage": {
                "tokens_in": 1,
                "tokens_out": 1,
                "cost_usd": cost,
                "wall_ms": 1,
                "turns": 1,
            },
            "verification": {
                "tests_hash_before": "a" * 64,
                "tests_hash_after": "a" * 64,
                "matches": True,
            },
            "artifacts": {},
            "ext": {},
        }
    )

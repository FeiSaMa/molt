"""D5：SearchHistory / ReportArchive 类型隔离、holdout 不可见、archive 不内联分数。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from molt.core.engine import Engine, EngineConfig
from molt.core.errors import IsolationError, ValidationError
from molt.core.isolation import IsolationPolicy
from molt.core.ports import HarnessOutcome, RuntimeOutcome, VerifyOutcome
from molt.core.store import StateStore
from molt.core.types import (
    Archive,
    Genome,
    ReportArchive,
    Run,
    ScoreBlock,
    SearchHistory,
    Task,
    Usage,
)
from molt.shell_loader import load_shell_module

ROOT = Path(__file__).resolve().parents[1]
FIXED = "2026-01-01T00:00:00Z"


def _genome() -> Genome:
    return Genome.from_dict(
        json.loads((ROOT / "shells/code-repair/genomes/v0.json").read_text(encoding="utf-8"))
    )


def _harness():
    module = load_shell_module(ROOT, "shells/code-repair/harness.py", "molt_test_harness_isolation")
    return module.CodeRepairHarness(
        runtime=None, verifier=None, repo_root=ROOT, clock=lambda: FIXED
    )


def test_search_history_cannot_hold_holdout() -> None:
    history = SearchHistory(scenario_version="demo-1", genome_id="g")
    assert not hasattr(history, "holdout")
    assert not hasattr(history, "report")


def test_propose_rejects_report_archive() -> None:
    harness = _harness()
    block = _score_block(1, 1, 1.0)
    report = ReportArchive(
        scenario_version="demo-1",
        genome_id="g",
        search=block,
        holdout=block,
        result_refs=("result-report",),
    )
    with pytest.raises(TypeError):
        harness.propose(report, _genome())


def test_poisoned_holdout_scores_do_not_change_proposal_bytes() -> None:
    harness = _harness()
    genome = _genome()
    history = SearchHistory(scenario_version=genome.scenario_version, genome_id=genome.genome_id)
    from molt.core.types import HistoryEntry

    history.add(HistoryEntry("smoke-add", True, 1.0))
    history.add(HistoryEntry("smoke-even", True, 1.0))
    first = harness.propose(history, genome).to_dict()
    second = harness.propose(history, genome).to_dict()
    assert first == second

    poisoned = ReportArchive(
        scenario_version="demo-1",
        genome_id=genome.genome_id,
        search=_score_block(2, 2, 1.0),
        holdout=_score_block(0, 1, None),
    )
    with pytest.raises(TypeError):
        harness.propose(poisoned, genome)
    assert harness.propose(history, genome).to_dict() == first


def test_agent_denied_holdout_web_evidence() -> None:
    policy = IsolationPolicy.resolve("none")
    for path in (
        "bench/holdout/holdout-even/tests/test_solution.py",
        "bench/search/smoke-add/tests/test_solution.py",
        "web/data/report_results.json",
        "evidence/demo/ledger.json",
    ):
        with pytest.raises(IsolationError):
            policy.check_agent_read(path)
    policy.check_agent_read("molt/core/types.py")
    policy.check_agent_read("shells/code-repair/harness.py")


def test_report_result_requires_dual_scores(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state")
    store.ensure()
    engine = _engine(store, tmp_path)
    genome = _genome()
    search_runs = [
        _run("s1", "smoke-add", "search", "passed", 1.0),
        _run("s2", "smoke-even", "search", "passed", 1.0),
    ]
    holdout_runs = [_run("h1", "holdout-even", "report", "passed", 1.0)]
    result = engine.evaluate_report(
        genome,
        search_runs,
        holdout_runs,
        search_task_ids=["smoke-add", "smoke-even"],
        holdout_task_ids=["holdout-even"],
        result_id="result-report",
        generated_at=FIXED,
    )
    assert set(result.scores) == {"search", "holdout"}
    assert result.scores["search"].solved == 2
    assert result.scores["holdout"].solved == 1

    bad = result.to_dict()
    bad["scores"].pop("holdout")
    with pytest.raises(ValidationError):
        type(result).from_dict(bad)


def test_search_result_cannot_carry_holdout_score() -> None:
    result = {
        "schema_version": "1.0.0",
        "result_id": "r",
        "scenario_version": "demo-1",
        "genome_id": "g",
        "scope": "search",
        "seed_control": "none",
        "run_refs": ["s1"],
        "scores": {
            "search": _score_block(1, 1, 1.0).to_dict(),
            "holdout": _score_block(1, 1, 1.0).to_dict(),
        },
        "generated_at": FIXED,
        "ext": {},
    }
    with pytest.raises(ValidationError):
        from molt.core.types import Result

        Result.from_dict(result)


def test_archive_rejects_inline_scores() -> None:
    payload = {
        "schema_version": "1.0.0",
        "archive_id": "arc-1",
        "generation": 1,
        "genome_id": "g",
        "parent_id": None,
        "adopted_at": FIXED,
        "snapshot_id": "snap-1",
        "result_refs": ["result-1"],
        "note": "",
        "ext": {},
        "scores": {"holdout": _score_block(1, 1, 1.0).to_dict()},
    }
    with pytest.raises(ValidationError):
        Archive.from_dict(payload)


def test_search_history_rejects_report_run(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state")
    engine = _engine(store, tmp_path)
    genome = _genome()
    report_run = _run("h1", "holdout-even", "report", "passed", 1.0)
    with pytest.raises(ValidationError):
        engine.search_history(genome, [report_run])


class DummyProvider:
    def list_tasks(self, scope: str) -> list[Task]:
        return []

    def load_task(self, task_id: str) -> Task:
        raise KeyError(task_id)


class DummyVerifier:
    def verify(self, task: Task, workdir: Path) -> VerifyOutcome:
        return VerifyOutcome(True, "a" * 64, "a" * 64, True)


class DummyHarness:
    def run(self, genome: Genome, task: Task, workdir: Path) -> HarnessOutcome:
        return HarnessOutcome(
            RuntimeOutcome(
                outcome="passed",
                usage=Usage(1, 1, 1.0, 1, 1),
                started_at=FIXED,
                finished_at=FIXED,
            ),
            VerifyOutcome(True, "a" * 64, "a" * 64, True),
        )

    def propose(self, history, parent):
        raise AssertionError("not used")


def _engine(store: StateStore, tmp_path: Path) -> Engine:
    return Engine(
        store,
        DummyProvider(),
        DummyHarness(),
        DummyVerifier(),
        EngineConfig(
            repo_root=ROOT,
            work_root=tmp_path / "work",
            shell_version="1.0.0",
            verifier_hash="a" * 64,
        ),
    )


def _score_block(solved: int, total: int, cps: float | None) -> ScoreBlock:
    return ScoreBlock.from_dict(
        {
            "solved": solved,
            "total": total,
            "pass_at_1_first_attempt": solved / total if total else 0.0,
            "cost_total_usd": (cps or 0.0) * solved,
            "cost_per_solved_usd": cps,
            "invalid_ratio": 0.0,
        },
        "score",
    )


def _run(run_id: str, task_id: str, scope: str, outcome: str, cost: float) -> Run:
    return Run.from_dict(
        {
            "schema_version": "1.0.0",
            "run_id": run_id,
            "scenario_version": "demo-1",
            "genome_id": "g",
            "task_id": task_id,
            "scope": scope,
            "attempt": 1,
            "shell_version": "1.0.0",
            "verifier_hash": "a" * 64,
            "seed_control": "none",
            "started_at": FIXED,
            "finished_at": FIXED,
            "outcome": outcome,
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

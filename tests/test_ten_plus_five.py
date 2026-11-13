"""B2c-1：10 题搜索集 + 5 题留出集，以及 fixture 基线的最低通过数。"""

from __future__ import annotations

import json
from pathlib import Path

from molt.bench import compute_bench_hash, task_paths
from molt.core.events import EventLog
from molt.core.store import StateStore
from molt.core.types import Genome, Scenario
from molt.demo import build_engine

ROOT = Path(__file__).resolve().parents[1]
SCENARIO_PATH = ROOT / "scenarios/demo/scenario.json"
GENOME_PATH = ROOT / "shells/code-repair/genomes/v0.json"
SEARCH_COUNT = 10
REPORT_COUNT = 5
MIN_SOLVED = 3


def _load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _scenario() -> Scenario:
    return Scenario.from_dict(_load_json(SCENARIO_PATH))


def test_task_sets_are_ten_search_and_five_holdout() -> None:
    scenario = _scenario()
    assert scenario.scenario_version == "demo-7"
    search = scenario.task_sets["search"]
    report = scenario.task_sets["report"]
    assert len(search) == SEARCH_COUNT
    assert len(report) == REPORT_COUNT
    assert len(set(search)) == SEARCH_COUNT
    assert len(set(report)) == REPORT_COUNT
    assert not set(search) & set(report), "search 与 holdout 不得重叠"
    assert scenario.policy.budget.execution_budget_usd > 0
    assert scenario.policy.budget.search_overhead_budget_usd > 0


def test_every_task_declares_scope_version_and_fixture() -> None:
    scenario = _scenario()
    paths = task_paths(ROOT)
    for scope in ("search", "report"):
        for task_id in scenario.task_sets[scope]:
            data = _load_json(paths[task_id])
            assert data["task_id"] == task_id
            assert data["scope"] == scope
            assert data["scenario_version"] == scenario.scenario_version
            assert data["shell_id"] == scenario.shell_id
            fixture = data["ext"]["fixture"]
            for key in ("target", "solution", "solvable_at", "base_cost_usd"):
                assert key in fixture, f"{task_id} 缺 ext.fixture.{key}"
            assert set(fixture["solvable_at"]) <= {"off", "low", "high"}


def test_bench_hash_matches_scenario() -> None:
    scenario = _scenario()
    referenced = scenario.task_sets["search"] + scenario.task_sets["report"]
    assert compute_bench_hash(ROOT, referenced) == scenario.bench_hash


def test_baseline_solves_at_least_three_of_ten(tmp_path: Path) -> None:
    scenario = _scenario()
    genome = Genome.from_dict(_load_json(GENOME_PATH))
    assert genome.scenario_version == scenario.scenario_version
    assert genome.switches.thinking == "low"

    store = StateStore(tmp_path / "state")
    engine = build_engine(ROOT, scenario, store, tmp_path / "work")
    tasks = [engine.task_provider.load_task(task_id) for task_id in scenario.task_sets["search"]]
    runs = engine.run_suite(genome, tasks, scope="search")
    assert {run.task_id for run in runs} == set(scenario.task_sets["search"])
    assert {run.scope for run in runs} == {"search"}
    EventLog(store.root).materialize("search")
    search_events = (store.root / "events" / "search.jsonl").read_text(encoding="utf-8")
    for holdout_id in scenario.task_sets["report"]:
        assert holdout_id not in search_events, f"search 事件流泄漏 holdout id: {holdout_id}"
    result = engine.evaluate_search(
        genome, runs, scenario.task_sets["search"], result_id="test-baseline-search"
    )
    solved = result.scores["search"].solved
    assert solved >= MIN_SOLVED, f"fixture 基线只有 {solved}/{SEARCH_COUNT}，低于门禁"
    assert solved < SEARCH_COUNT, "基线全过会让搜索结果失去区分度"

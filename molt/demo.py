"""demo 证据链：两题搜索集 + 一题留出集，全程 fixture runtime。

``molt audit --scenario demo`` 会在临时目录重建这条链，再只读审计；
仓库内不落 ``runs/`` 原始产物。
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from molt.adapters.fixture.runtime import FixtureRuntime
from molt.core.audit import audit_state
from molt.core.engine import Engine, EngineConfig
from molt.core.events import EventLog
from molt.core.gate import Gate
from molt.core.genome import child_genome, verify_genome
from molt.core.hash import sha256_file
from molt.core.metrics import paired_rows_from_runs
from molt.core.policy import SideEval, TaskEval
from molt.core.redact import evidence_bundle, redact_evidence, write_redaction_manifest
from molt.core.store import StateStore
from molt.core.types import Genome, Proposal, Scenario, SearchHistory
from molt.shell_loader import load_shell_module

FIXED_TS = "2026-01-01T00:00:00Z"
DEMO_SCENARIO = Path("scenarios") / "demo" / "scenario.json"
DEMO_GENOME = Path("shells") / "code-repair" / "genomes" / "v0.json"


def fixed_clock() -> str:
    return FIXED_TS


def load_demo_scenario(repo_root: Path) -> Scenario:
    data = json.loads((Path(repo_root) / DEMO_SCENARIO).read_text(encoding="utf-8"))
    return Scenario.from_dict(data)


def load_genome(repo_root: Path, rel_path: Path = DEMO_GENOME) -> Genome:
    data = json.loads((Path(repo_root) / rel_path).read_text(encoding="utf-8"))
    genome = Genome.from_dict(data)
    errors = verify_genome(genome, repo_root)
    if errors:
        raise ValueError("genome 校验失败: " + "; ".join(errors))
    return genome


def build_engine(
    repo_root: Path,
    scenario: Scenario,
    store: StateStore,
    work_root: Path,
    seed_control: str = "none",
    *,
    domains: Mapping[str, Sequence[str]] | None = None,
    llm_proposer: Callable[[SearchHistory, Genome], Proposal] | None = None,
    runtime: str = "fixture",
    pi_options: Mapping[str, Any] | None = None,
) -> Engine:
    """组装 B1 的 fixture 引擎（shell loader + fixture runtime + 真 pytest verifier）。"""
    repo_root = Path(repo_root).resolve()
    work_root = Path(work_root).resolve()
    store.ensure()
    provider_mod = load_shell_module(
        repo_root, "shells/code-repair/task_provider.py", "molt_shell_task_provider"
    )
    harness_mod = load_shell_module(
        repo_root, "shells/code-repair/harness.py", "molt_shell_harness"
    )
    verifier_mod = load_shell_module(
        repo_root, "shells/code-repair/verifier.py", "molt_shell_verifier"
    )
    provider = provider_mod.BenchTaskProvider(repo_root)
    if runtime == "pi":
        from molt.adapters.pi.runtime import PiRuntime
        from molt.adapters.pi.trust import TrustProbe

        options = dict(pi_options or {})
        model = str(options.get("model", "deepseek/deepseek-flash"))
        network = scenario.isolation.network
        if network not in ("provider_only", "open"):
            network = "provider_only"
        runtime_obj: Any = PiRuntime(
            repo_root=repo_root,
            tools_argv_fn=harness_mod.tools_argv_for,
            sandbox_wrapper=options.get(
                "sandbox_wrapper",
                repo_root / "molt/adapters/pi/sandbox_bash.sh",
            ),
            trust_probe=TrustProbe(
                pi_executable=str(options.get("pi_executable", "pi")),
                model=model,
                cache_dir=Path(work_root).parent / "trust-cache",
            ),
            pi_executable=str(options.get("pi_executable", "pi")),
            model=model,
            network=network,
            probe_runner=options.get("probe_runner"),
            spawn=options.get("spawn"),
        )
    elif runtime == "fixture":
        runtime_obj = FixtureRuntime()
    else:
        raise ValueError(f"未知 runtime: {runtime!r}")
    verifier = verifier_mod.PytestVerifier(repo_root)
    harness = harness_mod.CodeRepairHarness(
        runtime_obj,
        verifier,
        repo_root,
        shell_id=scenario.shell_id,
        shell_version=scenario.shell_version,
        clock=fixed_clock,
        domains=domains,
        llm_proposer=llm_proposer,
    )
    return Engine(
        store,
        provider,
        harness,
        verifier,
        EngineConfig(
            repo_root=repo_root,
            work_root=Path(work_root),
            shell_version=scenario.shell_version,
            verifier_hash=sha256_file(repo_root / "shells/code-repair/verifier.py"),
            seed_control=seed_control,
        ),
    )


def build_demo_state(
    repo_root: Path, work_root: Path
) -> tuple[StateStore, Scenario, dict[str, Any]]:
    """在 ``work_root`` 下重建完整证据链，返回 store / scenario / 摘要。"""
    repo_root = Path(repo_root)
    work_root = Path(work_root)
    scenario = load_demo_scenario(repo_root)
    store = StateStore(work_root / "state")
    store.ensure()
    engine = build_engine(repo_root, scenario, store, work_root / "work")
    provider = engine.task_provider
    events = EventLog(store.root)

    baseline = load_genome(repo_root)
    store.write_genome(baseline)
    search_tasks = [provider.load_task(task_id) for task_id in scenario.task_sets["search"]]
    baseline_search_runs = engine.run_suite(baseline, search_tasks, scope="search")
    baseline_search_result = engine.evaluate_search(
        baseline,
        baseline_search_runs,
        scenario.task_sets["search"],
        result_id="result-baseline-search",
        generated_at=FIXED_TS,
    )

    history = engine.search_history(baseline, baseline_search_runs)
    proposal = engine.propose(baseline, history)
    engine.record_proposal(proposal, event_id="ev-demo-proposal", ts=FIXED_TS)
    candidate = child_genome(baseline, proposal.switches, repo_root=repo_root)
    store.write_genome(candidate)
    candidate_search_runs = engine.run_suite(candidate, search_tasks, scope="search")
    candidate_search_result = engine.evaluate_search(
        candidate,
        candidate_search_runs,
        scenario.task_sets["search"],
        result_id="result-candidate-search",
        generated_at=FIXED_TS,
        overhead_spend_usd=proposal.cost_usd,
    )

    paired = paired_rows_from_runs(
        baseline_search_runs, candidate_search_runs, scenario.task_sets["search"]
    )
    baseline_side = _side(baseline, baseline_search_runs, scenario)
    candidate_side = _side(candidate, candidate_search_runs, scenario, proposal.cost_usd)
    decision = Gate(scenario.policy).decide(
        baseline_side,
        candidate_side,
        decision_id="demo-decision-1",
        scenario_version=scenario.scenario_version,
        shell_version=scenario.shell_version,
        verifier_hash=engine.config.verifier_hash,
        created_at=FIXED_TS,
    )
    decision_payload = decision.to_dict()
    decision_payload["ext"] = {
        "inputs": {
            "baseline_result_id": baseline_search_result.result_id,
            "candidate_result_id": candidate_search_result.result_id,
        },
        "overhead_spend_usd": proposal.cost_usd,
    }
    decision = type(decision).from_dict(decision_payload)
    store.write_decision(decision)
    events.emit(
        scope="search",
        event_type="gate.decided",
        payload={"decision_id": decision.decision_id, "verdict": decision.verdict},
        event_id="ev-demo-gate",
        ts=FIXED_TS,
    )

    holdout_tasks = [provider.load_task(task_id) for task_id in scenario.task_sets["report"]]
    baseline_holdout_runs = engine.run_suite(baseline, holdout_tasks, scope="report")
    candidate_holdout_runs = engine.run_suite(candidate, holdout_tasks, scope="report")
    baseline_report = engine.evaluate_report(
        baseline,
        baseline_search_runs,
        baseline_holdout_runs,
        search_task_ids=scenario.task_sets["search"],
        holdout_task_ids=scenario.task_sets["report"],
        result_id="result-baseline-report",
        generated_at=FIXED_TS,
    )
    candidate_report = engine.evaluate_report(
        candidate,
        candidate_search_runs,
        candidate_holdout_runs,
        search_task_ids=scenario.task_sets["search"],
        holdout_task_ids=scenario.task_sets["report"],
        result_id="result-candidate-report",
        generated_at=FIXED_TS,
        overhead_spend_usd=proposal.cost_usd,
    )
    for result in (baseline_report, candidate_report):
        events.emit(
            scope="report",
            event_type="report.scored",
            payload={"result_id": result.result_id},
            event_id=f"ev-demo-{result.result_id}",
            ts=FIXED_TS,
        )
    events.materialize("search")
    events.materialize("report")

    # 证据落盘后立刻脱敏并留 manifest；无 secret 时 metrics 相同、manifest 仍生成。
    bundle = evidence_bundle(
        scenario_id=scenario.scenario_id,
        scenario_version=scenario.scenario_version,
        runs=store.list_runs(),
        results=store.list_results(),
        decisions=store.list_decisions(),
    )
    _, redaction_manifest = redact_evidence(bundle)
    manifest_path = write_redaction_manifest(store.root, scenario.scenario_id, redaction_manifest)

    summary = {
        "baseline_genome": baseline.genome_id,
        "candidate_genome": candidate.genome_id,
        "proposal": proposal.proposal_id,
        "decision": decision.verdict,
        "reason": decision.reason,
        "paired": paired,
        "redaction_manifest": str(manifest_path),
        "baseline_report": baseline_report.scores["holdout"].to_dict(),
        "candidate_report": candidate_report.scores["holdout"].to_dict(),
    }
    return store, scenario, summary


def run_demo_audit(repo_root: Path, work_root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    store, scenario, summary = build_demo_state(repo_root, work_root)
    report = audit_state(store, scenario)
    return summary, report


def _side(
    genome: Genome,
    runs: list[Any],
    scenario: Scenario,
    overhead: float = 0.0,
) -> SideEval:
    from molt.core.metrics import metrics_from_runs

    metrics = metrics_from_runs(runs, scenario.task_sets["search"], overhead_spend_usd=overhead)
    tasks = []
    for task_id in scenario.task_sets["search"]:
        first = next(
            (run for run in runs if run.task_id == task_id and run.attempt == 1),
            None,
        )
        cost = sum(run.usage.cost_usd for run in runs if run.task_id == task_id)
        tasks.append(
            TaskEval(
                task_id=task_id,
                passed=bool(first) and first.outcome == "passed",
                cost_usd=cost,
            )
        )
    return SideEval(
        genome_id=genome.genome_id,
        scenario_version=scenario.scenario_version,
        tasks=tuple(tasks),
        limits=genome.budget,
        invalid_ratio=metrics.invalid_ratio,
        overhead_spend_usd=overhead,
    )

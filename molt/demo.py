"""证据链装配：按 scenario 的 shell manifest 装载 shell 模块 + runtime + Engine。

``molt audit --scenario <id>`` 会在临时目录重建该 scenario 的证据链，再只读审计；
仓库内不落 ``runs/`` 原始产物。``demo`` 只是其中一个 scenario（code-repair + fixture），
``svc-config`` 是第二个（svc-config + deterministic）。
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
from molt.shell_bundle import (
    ShellBundle,
    build_harness,
    build_task_provider,
    build_verifier,
    load_shell_bundle,
)

FIXED_TS = "2026-01-01T00:00:00Z"
DEMO_SCENARIO = Path("scenarios") / "demo" / "scenario.json"
DEMO_GENOME = Path("shells") / "code-repair" / "genomes" / "v0.json"
RUNTIMES: tuple[str, ...] = ("fixture", "pi", "deterministic")


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


def default_genome_path(repo_root: Path, scenario: Scenario) -> Path:
    """该 scenario 的默认 genome：优先 ``v0.json``，否则取首个版本匹配的 genome。

    先按 ``scenario_version`` 过滤，再看 ``shell_id``；两者的不一致由
    ``molt scenario validate`` 与 ``build_engine`` 兜底拒绝。
    """
    shell_dir = Path(repo_root) / "shells" / scenario.shell_id / "genomes"
    preferred = shell_dir / "v0.json"
    candidates = [
        preferred,
        *sorted(path for path in shell_dir.glob("*.json") if path != preferred),
    ]
    for path in candidates:
        if not path.is_file():
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("scenario_version") != scenario.scenario_version:
            continue
        if data.get("shell_id") != scenario.shell_id:
            continue
        return path
    raise ValueError(
        f"scenario {scenario.scenario_id}（{scenario.scenario_version}）没有匹配的 genome；"
        f"查找目录 {shell_dir}"
    )


def _default_runtime(scenario: Scenario) -> str:
    value = scenario.ext.get("default_runtime", "fixture")
    return str(value)


def allowed_runtimes(scenario: Scenario) -> tuple[str, ...] | None:
    """读 ``scenario.ext.allowed_runtimes``；未声明返回 ``None``（不限制）。"""
    value = scenario.ext.get("allowed_runtimes")
    if value is None:
        return None
    if not isinstance(value, list) or not value:
        raise ValueError(f"scenario {scenario.scenario_id} 的 ext.allowed_runtimes 必须是非空数组")
    if not all(isinstance(item, str) for item in value):
        raise ValueError(f"scenario {scenario.scenario_id} 的 ext.allowed_runtimes 必须全是字符串")
    unknown = sorted(set(value) - set(RUNTIMES))
    if unknown:
        raise ValueError(
            f"scenario {scenario.scenario_id} 的 ext.allowed_runtimes 含未知值 {unknown}"
        )
    return tuple(value)


def resolve_runtime(scenario: Scenario, runtime: str | None, *, enforce: bool = True) -> str:
    """解析 runtime 并（默认）检查是否落在 ``scenario.ext.allowed_runtimes``。"""
    name = runtime or _default_runtime(scenario)
    if name not in RUNTIMES:
        raise ValueError(f"未知 runtime: {name!r}（合法值 {list(RUNTIMES)}）")
    allowed = allowed_runtimes(scenario)
    if enforce and allowed is not None and name not in allowed:
        raise ValueError(
            f"scenario {scenario.scenario_id} 不允许 runtime={name!r}；"
            f"allowed_runtimes={list(allowed)}"
        )
    return name


def _build_runtime(
    repo_root: Path,
    scenario: Scenario,
    work_root: Path,
    runtime: str,
    bundle: ShellBundle,
    pi_options: Mapping[str, Any] | None,
) -> Any:
    if runtime == "pi":
        from molt.adapters.pi.runtime import PiRuntime
        from molt.adapters.pi.trust import TrustProbe

        options = dict(pi_options or {})
        model = str(options.get("model", "deepseek/deepseek-flash"))
        network = scenario.isolation.network
        if network == "none":
            # Pi 主进程必须保留 provider 网络，声明 none 与事实不符；
            # 拒绝静默改写，要求 scenario 明确写 provider_only / open。
            raise ValueError(
                "Pi runtime 需要 scenario.isolation.network=provider_only 或 open；"
                "none 无法切断 Pi 主进程的 provider 网络"
            )
        tools_argv_fn = getattr(bundle.harness_module, "tools_argv_for", None)
        if tools_argv_fn is None:
            raise ValueError(
                f"shell {scenario.shell_id} 未提供 tools_argv_for，不能使用 Pi runtime"
            )
        return PiRuntime(
            repo_root=repo_root,
            tools_argv_fn=tools_argv_fn,
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
    if runtime == "fixture":
        return FixtureRuntime()
    if runtime == "deterministic":
        from molt.adapters.deterministic.runtime import DeterministicRuntime

        return DeterministicRuntime()
    raise ValueError(f"未知 runtime: {runtime!r}（合法值 {list(RUNTIMES)}）")


def build_engine(
    repo_root: Path,
    scenario: Scenario,
    store: StateStore,
    work_root: Path,
    seed_control: str = "none",
    *,
    domains: Mapping[str, Sequence[str]] | None = None,
    llm_proposer: Callable[[SearchHistory, Genome], Proposal] | None = None,
    runtime: str | None = None,
    pi_options: Mapping[str, Any] | None = None,
    enforce_runtime: bool = True,
) -> Engine:
    """按 scenario.shell_id 装载 manifest.entrypoints，装配 provider/harness/verifier。

    ``enforce_runtime=False`` 只给 adapter conformance 与 ``calibrate`` 的强制 fixture
    使用；产品命令（search/propose/audit）保持默认拒绝跨 runtime。
    """
    repo_root = Path(repo_root).resolve()
    work_root = Path(work_root).resolve()
    store.ensure()
    bundle = load_shell_bundle(
        repo_root,
        scenario.shell_id,
        shell_version=scenario.shell_version,
    )
    provider = build_task_provider(bundle, repo_root)
    runtime_name = resolve_runtime(scenario, runtime, enforce=enforce_runtime)
    runtime_obj = _build_runtime(repo_root, scenario, work_root, runtime_name, bundle, pi_options)
    verifier = build_verifier(bundle, repo_root)
    harness = build_harness(
        bundle,
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
            verifier_hash=sha256_file(bundle.verifier_path),
            seed_control=seed_control,
        ),
    )


def build_scenario_state(
    repo_root: Path,
    work_root: Path,
    *,
    scenario: Scenario | None = None,
    genome_path: Path | None = None,
    runtime: str | None = None,
    domains: Mapping[str, Sequence[str]] | None = None,
    llm_proposer: Callable[[SearchHistory, Genome], Proposal] | None = None,
    seed_control: str = "none",
) -> tuple[StateStore, Scenario, dict[str, Any]]:
    """在 ``work_root`` 下重建完整证据链，返回 store / scenario / 摘要。"""
    repo_root = Path(repo_root)
    work_root = Path(work_root)
    scenario = scenario or load_demo_scenario(repo_root)
    store = StateStore(work_root / "state")
    store.ensure()
    engine = build_engine(
        repo_root,
        scenario,
        store,
        work_root / "work",
        seed_control,
        domains=domains,
        llm_proposer=llm_proposer,
        runtime=runtime,
    )
    provider = engine.task_provider
    events = EventLog(store.root)

    baseline = load_genome(repo_root, genome_path or default_genome_path(repo_root, scenario))
    store.write_genome(baseline)
    slug = scenario.scenario_id
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
    engine.record_proposal(proposal, event_id=f"ev-{slug}-proposal", ts=FIXED_TS)
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
        decision_id=f"{slug}-decision-1",
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
        event_id=f"ev-{slug}-gate",
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
            event_id=f"ev-{slug}-{result.result_id}",
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


def build_demo_state(
    repo_root: Path, work_root: Path
) -> tuple[StateStore, Scenario, dict[str, Any]]:
    """兼容入口：demo scenario 的完整证据链。"""
    return build_scenario_state(repo_root, work_root)


def run_scenario_audit(
    repo_root: Path,
    work_root: Path,
    scenario: Scenario,
    *,
    runtime: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    store, scenario, summary = build_scenario_state(
        repo_root, work_root, scenario=scenario, runtime=runtime
    )
    report = audit_state(store, scenario)
    return summary, report


def run_demo_audit(repo_root: Path, work_root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    return run_scenario_audit(repo_root, work_root, load_demo_scenario(repo_root))


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

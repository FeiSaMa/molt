"""证据链装配：按 scenario 的 shell manifest 装载 shell 模块 + runtime + Engine。

``molt audit --scenario <id>`` 会在临时目录重建该 scenario 的证据链，再只读审计；
仓库内不落 ``runs/`` 原始产物。``demo`` 只是其中一个 scenario（code-repair + fixture），
``svc-config`` 是第二个（svc-config + deterministic）。
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from molt.adapters.fixture.runtime import FixtureRuntime
from molt.core.audit import audit_state
from molt.core.engine import Engine, EngineConfig
from molt.core.errors import ValidationError
from molt.core.events import EventLog, utc_now
from molt.core.gate import Gate
from molt.core.genome import child_genome, compute_genome_id, verify_genome
from molt.core.hash import sha256_file
from molt.core.metrics import paired_rows_from_runs
from molt.core.policy import SideEval, TaskEval
from molt.core.redact import evidence_bundle, redact_evidence, write_redaction_manifest
from molt.core.store import StateStore
from molt.core.types import Genome, Proposal, Run, Scenario, SearchHistory, ShellManifest
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


# ---------------------------------------------------------------------------
# B2-D7：现场演示（1 live 候选 + 1 replay 基线 + 10 题扫描回放）
# ---------------------------------------------------------------------------

DEMO_GROUPS: tuple[str, ...] = ("closure", "scan10", "candidate-scan")


class DemoTimeout(RuntimeError):
    """端到端超过 ``--max-wall``；调用方必须 exit 1，且不得降级导出。"""


def _deadline_check(deadline: float, stage: str, started: float) -> None:
    now = time.monotonic()
    if now > deadline:
        raise DemoTimeout(f"demo 超过 --max-wall（stage={stage}，elapsed={now - started:.1f}s）")


def _with_runtime(genome: Genome, runtime_id: str, version: str, model: str | None) -> Genome:
    """派生同 switches 的 runtime 变体；genome_id 随内容重算。"""
    payload = genome.to_dict()
    payload["runtime"] = {"runtime_id": runtime_id, "version": version}
    if model:
        ext = dict(payload.get("ext") or {})
        ext["pi"] = {"model": model}
        payload["ext"] = ext
    payload["genome_id"] = compute_genome_id(payload)
    return Genome.from_dict(payload)


def _ledger_entry(run: Run) -> dict[str, Any]:
    return {
        "kind": "execution",
        "cost_usd": run.usage.cost_usd,
        "tokens_in": run.usage.tokens_in,
        "tokens_out": run.usage.tokens_out,
        "wall_ms": run.usage.wall_ms,
        "outcome": run.outcome,
        "scope": run.scope,
        "task_id": run.task_id,
    }


def _copy_run_as_replay(
    store: StateStore,
    ledger: Any,
    source: Run,
    *,
    new_run_id: str,
    group: str,
) -> Run:
    """把已录 run 复制为 replay 证据；源 run 只读，不改写。"""
    data = source.to_dict()
    data["run_id"] = new_run_id
    ext = dict(data.get("ext") or {})
    ext["replay_of_run_id"] = source.run_id
    ext["demo_group"] = group
    ext["provenance"] = {
        "kind": "replay",
        "replay_of_run_id": source.run_id,
        "group": group,
    }
    data["ext"] = ext
    replay = Run.from_dict(data)
    store.write_run(replay)
    # 账本按新 run_id 记账，金额与源 run 相同；audit 逐 run 对账。
    ledger.append(replay.run_id, _ledger_entry(replay))
    return replay


def _mark_run(store: StateStore, run: Run, ext_extra: Mapping[str, Any]) -> Run:
    data = run.to_dict()
    ext = dict(data.get("ext") or {})
    ext.update(ext_extra)
    data["ext"] = ext
    marked = Run.from_dict(data)
    store.write_run(marked)
    return marked


def _copy_genomes(store: StateStore, source: StateStore, runs: Sequence[Run]) -> None:
    """把被引用 run 的 genome 复制进目标 state（同 id 已存在则跳过）。"""
    for genome_id in {run.genome_id for run in runs}:
        try:
            store.read_genome(genome_id)
            continue
        except ValidationError:
            pass
        try:
            genome = source.read_genome(genome_id)
        except ValidationError:
            # `molt search` 不落 genome；调用方随后会写 active genome，这里不阻断。
            continue
        store.write_genome(genome)


def _baseline_runs(baseline: StateStore, scenario: Scenario) -> list[Run]:
    """从 baseline state 取每题首轮 run；缺题即报错（不静默补跑）。"""
    return _scope_runs(baseline, scenario, "search")


def _scope_runs(
    state: StateStore, scenario: Scenario, scope: str, *, exclude: Sequence[str] = ()
) -> list[Run]:
    """按 scenario 题集顺序取首轮 run；缺题即报错。"""
    wanted = [task_id for task_id in scenario.task_sets[scope] if task_id not in set(exclude)]
    index: dict[str, Run] = {}
    for run in sorted(state.list_runs(), key=lambda item: item.finished_at):
        if run.scope != scope or run.attempt != 1 or run.task_id not in wanted:
            continue
        index[run.task_id] = run
    missing = [task_id for task_id in wanted if task_id not in index]
    if missing:
        raise ValueError(f"state 缺少 {scope} 首轮 run: {missing}")
    return [index[task_id] for task_id in wanted]


def _has_scope_runs(state: StateStore, scenario: Scenario, scope: str) -> bool:
    wanted = set(scenario.task_sets[scope])
    found = {
        run.task_id
        for run in state.list_runs()
        if run.scope == scope and run.attempt == 1 and run.task_id in wanted
    }
    return found == wanted


def record_baseline_scan(
    repo_root: Path,
    scenario: Scenario,
    baseline_root: Path,
    genome: Genome,
) -> StateStore:
    """录制一次 fixture 基线扫描（10 题），作为 demo 的 replay 来源。"""
    baseline_root = Path(baseline_root)
    store = StateStore(baseline_root)
    store.ensure()
    store.write_genome(genome)
    engine = build_engine(
        repo_root, scenario, store, baseline_root / "work", runtime="fixture"
    )
    tasks = [engine.task_provider.load_task(task_id) for task_id in scenario.task_sets["search"]]
    engine.run_suite(genome, tasks, scope="search")
    EventLog(store.root).materialize("search")
    return store


def run_demo(
    repo_root: Path,
    scenario: Scenario,
    *,
    state_root: Path,
    out_dir: Path | None = None,
    mode: str = "live",
    max_wall_s: float = 180.0,
    baseline_state: Path | None = None,
    candidate_state: Path | None = None,
    model: str = "deepseek/deepseek-flash",
    pi_executable: str = "pi",
) -> dict[str, Any]:
    """B2-D7 闭环：replay 基线 → propose → live 候选 → gate → adopt/revert → export。

    - ``closure`` 组：1 条 replay 基线（``replay_of_run_id``）+ 1 条 live 候选，
      同一题（缺省 ``smoke-add``），页面与讲解词必须与 10 题扫描回放分开标注；
    - ``scan10`` 组：10 题扫描的回放（10 条 replay），不是 demo 的第二次运行；
    - ``candidate-scan`` 组：候选在其余 9 题上的执行；给了 ``--candidate-state``
      就复制已录 run（replay），否则现场跑 fixture（非 live）。live 只发生在 closure 那一题。
    """
    started = time.monotonic()
    deadline = started + float(max_wall_s)
    state_root = Path(state_root)
    out_dir = Path(out_dir) if out_dir is not None else None
    if mode not in ("live", "replay"):
        raise ValueError(f"未知 demo mode: {mode!r}（live|replay）")
    search_ids = list(scenario.task_sets["search"])
    if not search_ids:
        raise ValueError("scenario search 题集为空，无法 demo")
    closure_task_id = "smoke-add" if "smoke-add" in search_ids else search_ids[0]

    store = StateStore(state_root)
    store.ensure()
    baseline_genome = load_genome(repo_root, default_genome_path(repo_root, scenario))
    store.write_genome(baseline_genome)
    _deadline_check(deadline, "init", started)

    # 1) baseline：显式 --baseline-state 只读；否则录制/复用同级 baseline state。
    recorded = False
    if baseline_state is not None:
        baseline_root = Path(baseline_state)
        baseline_store = StateStore(baseline_root)
        if not (baseline_store.root / "runs").is_dir():
            raise FileNotFoundError(f"baseline state 不存在或没有 runs: {baseline_root}")
        source_runs = _baseline_runs(baseline_store, scenario)
    else:
        baseline_root = state_root.parent / f"{state_root.name}-baseline"
        baseline_store = StateStore(baseline_root)
        if (baseline_store.root / "runs").is_dir():
            try:
                source_runs = _baseline_runs(baseline_store, scenario)
            except ValueError:
                source_runs = []
        else:
            source_runs = []
        if not source_runs:
            record_baseline_scan(repo_root, scenario, baseline_root, baseline_genome)
            recorded = True
            source_runs = _baseline_runs(StateStore(baseline_root), scenario)
    _deadline_check(deadline, "baseline", started)

    # 2) 10 题扫描回放 + closure 单题基线回放（都是新 run_id，源 run 不改写）。
    engine = build_engine(repo_root, scenario, store, state_root / "work", runtime="fixture")
    scan_replays = [
        _copy_run_as_replay(
            store,
            engine.ledger,
            source,
            new_run_id=f"replay-{index:02d}-{source.run_id}",
            group="scan10",
        )
        for index, source in enumerate(source_runs)
    ]
    closure_source = next(run for run in source_runs if run.task_id == closure_task_id)
    closure_baseline = _copy_run_as_replay(
        store,
        engine.ledger,
        closure_source,
        new_run_id=f"replay-closure-{closure_source.run_id}",
        group="closure",
    )
    _deadline_check(deadline, "replay", started)

    # 3) propose：从 replay 基线的 SearchHistory 派生候选（grid/offline）。
    history = engine.search_history(baseline_genome, scan_replays)
    proposal = engine.propose(baseline_genome, history)
    engine.record_proposal(proposal)
    candidate = child_genome(baseline_genome, proposal.switches, repo_root=repo_root)
    store.write_genome(candidate)
    _deadline_check(deadline, "propose", started)

    # 4) 候选：给了 --candidate-state 就复制已录 9 题（replay）；否则现场 fixture。
    other_ids = [task_id for task_id in search_ids if task_id != closure_task_id]
    candidate_fixture_runs: list[Run] = []
    candidate_store: StateStore | None = None
    if candidate_state is not None:
        candidate_store = StateStore(Path(candidate_state))
        if not (candidate_store.root / "runs").is_dir():
            raise FileNotFoundError(f"candidate state 不存在或没有 runs: {candidate_state}")
        other_runs = _scope_runs(candidate_store, scenario, "search", exclude=[closure_task_id])
        _copy_genomes(store, candidate_store, other_runs)
        for index, source in enumerate(other_runs):
            candidate_fixture_runs.append(
                _copy_run_as_replay(
                    store,
                    engine.ledger,
                    source,
                    new_run_id=f"replay-candidate-{index:02d}-{source.run_id}",
                    group="candidate-scan",
                )
            )
    elif other_ids:
        tasks = [engine.task_provider.load_task(task_id) for task_id in other_ids]
        for run in engine.run_suite(candidate, tasks, scope="search"):
            candidate_fixture_runs.append(
                _mark_run(
                    store,
                    run,
                    {
                        "demo_group": "candidate-scan",
                        "provenance": {
                            "kind": "fixture",
                            "runtime_id": "fixture",
                            "note": "候选非 closure 题：fixture 执行，不是 live 模型调用",
                        },
                    },
                )
            )
    _deadline_check(deadline, "candidate-scan", started)

    closure_task = engine.task_provider.load_task(closure_task_id)
    if mode == "live":
        active_genome = _with_runtime(candidate, "pi", "1.5.0", model)
        store.write_genome(active_genome)
        live_engine = build_engine(
            repo_root,
            scenario,
            store,
            state_root / "work",
            runtime="pi",
            pi_options={"model": model, "pi_executable": pi_executable},
        )
        closure_started = time.monotonic()
        closure_run = live_engine.run_task(active_genome, closure_task, scope="search")
        closure_total_ms = int((time.monotonic() - closure_started) * 1000)
        closure_run = _mark_run(
            store,
            closure_run,
            {
                "demo_group": "closure",
                "provenance": {
                    "kind": "live",
                    "model": model,
                    "runtime_id": "pi-1.5.0",
                },
            },
        )
    else:
        active_genome = candidate
        closure_run = engine.run_task(candidate, closure_task, scope="search")
        closure_run = _mark_run(
            store,
            closure_run,
            {
                "demo_group": "closure",
                "provenance": {
                    "kind": "fixture",
                    "runtime_id": "fixture",
                    "note": "--mode replay：fixture 兜底，不是 live 模型调用",
                },
            },
        )
    _deadline_check(deadline, "candidate-closure", started)

    # 5) results / gate：基线 10 题 replay vs 候选 10 题（9 fixture + 1 live/replay）。
    ts = utc_now()
    baseline_result = engine.evaluate_search(
        baseline_genome,
        scan_replays,
        search_ids,
        result_id="result-demo-baseline-search",
        generated_at=ts,
    )
    candidate_runs = [*candidate_fixture_runs, closure_run]
    candidate_result = engine.evaluate_search(
        active_genome,
        candidate_runs,
        search_ids,
        result_id="result-demo-candidate-search",
        generated_at=ts,
        overhead_spend_usd=proposal.cost_usd,
    )
    baseline_side = _side(baseline_genome, scan_replays, scenario)
    candidate_side = _side(active_genome, candidate_runs, scenario, proposal.cost_usd)
    decision = Gate(scenario.policy).decide(
        baseline_side,
        candidate_side,
        decision_id=f"demo-decision-{scenario.scenario_id}",
        scenario_version=scenario.scenario_version,
        shell_version=scenario.shell_version,
        verifier_hash=engine.config.verifier_hash,
        created_at=ts,
    )
    decision_payload = decision.to_dict()
    decision_payload["ext"] = {
        "inputs": {
            "baseline_result_id": baseline_result.result_id,
            "candidate_result_id": candidate_result.result_id,
        },
        "overhead_spend_usd": proposal.cost_usd,
        "demo": {"mode": mode, "closure_task_id": closure_task_id},
    }
    decision = type(decision).from_dict(decision_payload)
    store.write_decision(decision)
    engine.events.emit(
        scope="search",
        event_type="gate.decided",
        payload={"decision_id": decision.decision_id, "verdict": decision.verdict},
        event_id=f"ev-demo-{scenario.scenario_id}-gate",
        ts=ts,
    )
    _deadline_check(deadline, "gate", started)

    # 6) holdout（report scope）：有已录 report run 就用 replay，否则现场 fixture。
    if _has_scope_runs(baseline_store, scenario, "report"):
        sources = _scope_runs(baseline_store, scenario, "report")
        _copy_genomes(store, baseline_store, sources)
        baseline_holdout = [
            _copy_run_as_replay(
                store,
                engine.ledger,
                source,
                new_run_id=f"replay-holdout-base-{index:02d}-{source.run_id}",
                group="holdout-baseline",
            )
            for index, source in enumerate(sources)
        ]
    else:
        holdout_tasks = [
            engine.task_provider.load_task(task_id) for task_id in scenario.task_sets["report"]
        ]
        baseline_holdout = engine.run_suite(baseline_genome, holdout_tasks, scope="report")
    if candidate_store is not None and _has_scope_runs(candidate_store, scenario, "report"):
        sources = _scope_runs(candidate_store, scenario, "report")
        _copy_genomes(store, candidate_store, sources)
        candidate_holdout = [
            _copy_run_as_replay(
                store,
                engine.ledger,
                source,
                new_run_id=f"replay-holdout-cand-{index:02d}-{source.run_id}",
                group="holdout-candidate",
            )
            for index, source in enumerate(sources)
        ]
    else:
        holdout_tasks = [
            engine.task_provider.load_task(task_id) for task_id in scenario.task_sets["report"]
        ]
        candidate_holdout = engine.run_suite(candidate, holdout_tasks, scope="report")
    if not any(event.type == "holdout.accessed" for event in engine.events.iter_scope("report")):
        engine.events.emit(
            scope="report",
            event_type="holdout.accessed",
            payload={"task_ids": list(scenario.task_sets["report"])},
        )
    baseline_report = engine.evaluate_report(
        baseline_genome,
        scan_replays,
        baseline_holdout,
        search_task_ids=search_ids,
        holdout_task_ids=scenario.task_sets["report"],
        result_id="result-demo-baseline-report",
        generated_at=ts,
    )
    candidate_report = engine.evaluate_report(
        candidate,
        candidate_runs,
        candidate_holdout,
        search_task_ids=search_ids,
        holdout_task_ids=scenario.task_sets["report"],
        result_id="result-demo-candidate-report",
        generated_at=ts,
        overhead_spend_usd=proposal.cost_usd,
    )
    for result in (baseline_report, candidate_report):
        engine.events.emit(
            scope="report",
            event_type="report.scored",
            payload={"result_id": result.result_id},
            event_id=f"ev-demo-{result.result_id}",
            ts=ts,
        )
    engine.events.materialize("search")
    engine.events.materialize("report")
    _deadline_check(deadline, "report", started)

    # 7) adopt / revert：先归档基线，再按 gate 结论采纳候选或回滚到基线代。
    manifest = ShellManifest.from_dict(
        json.loads((Path(repo_root) / "shells" / scenario.shell_id / "manifest.json").read_text(encoding="utf-8"))
    )
    roots = {name: Path(repo_root) / name for name in manifest.artifact_roots}
    baseline_archive = store.adopt(
        baseline_genome,
        roots,
        result_refs=[baseline_result.result_id, baseline_report.result_id],
        note="demo: baseline replay",
        adopted_at=ts,
    )
    if decision.verdict == "adopt":
        candidate_archive = store.adopt(
            active_genome,
            roots,
            result_refs=[candidate_result.result_id, candidate_report.result_id],
            note=f"demo: gate adopt ({mode})",
            adopted_at=ts,
        )
        action = {
            "kind": "adopt",
            "archive_id": candidate_archive.archive_id,
            "generation": candidate_archive.generation,
            "baseline_archive_id": baseline_archive.archive_id,
        }
    else:
        reverted = store.revert(
            baseline_archive.archive_id,
            adopted_at=ts,
            note="demo: gate revert",
        )
        action = {
            "kind": "revert",
            "archive_id": reverted.archive_id,
            "generation": reverted.generation,
            "baseline_archive_id": baseline_archive.archive_id,
        }
    _deadline_check(deadline, "adopt-revert", started)

    # 8) 脱敏 manifest + 只读 audit（demo state 自身必须可审计）。
    bundle = evidence_bundle(
        scenario_id=scenario.scenario_id,
        scenario_version=scenario.scenario_version,
        runs=store.list_runs(),
        results=store.list_results(),
        decisions=store.list_decisions(),
    )
    _, redaction_manifest = redact_evidence(bundle)
    write_redaction_manifest(store.root, scenario.scenario_id, redaction_manifest)
    audit_report = audit_state(store, scenario)
    _deadline_check(deadline, "audit", started)

    # 9) export（15 文件）；先检查预算再写，超时命令必须失败。
    index = None
    if out_dir is not None:
        from molt.export import DATA_FILES, export_web_data

        index = export_web_data(Path(repo_root), scenario, store, out_dir)
    elapsed = time.monotonic() - started
    _deadline_check(deadline, "export", started)

    # 10) 开销比（ADR 0001 重开条件 2）：live run 端到端 vs Pi 子进程。
    overhead: dict[str, Any] = {"measured": False, "reason": "mode=replay 无 Pi 子进程"}
    if mode == "live":
        total_ms = closure_total_ms
        sub_ms = int(closure_run.usage.wall_ms)
        if total_ms and total_ms > 0:
            extra_ms = max(0, total_ms - sub_ms)
            ratio = extra_ms / total_ms
            overhead = {
                "measured": True,
                "total_wall_ms": total_ms,
                "pi_subprocess_wall_ms": sub_ms,
                "orchestration_overhead_ms": extra_ms,
                "overhead_ratio": round(ratio, 4),
                "threshold": 0.20,
                "within_threshold": ratio <= 0.20,
                "adr": "0001-reopen-condition-2",
            }
        else:
            overhead = {"measured": False, "reason": "run 时间戳缺失"}

    provenance = {
        "live": sum(1 for run in store.list_runs() if _run_kind(run) == "live"),
        "replay": sum(1 for run in store.list_runs() if _run_kind(run) == "replay"),
        "fixture": sum(1 for run in store.list_runs() if _run_kind(run) == "fixture"),
    }
    summary = {
        "schema_version": "1.0.0",
        "scenario_id": scenario.scenario_id,
        "scenario_version": scenario.scenario_version,
        "mode": mode,
        "max_wall_s": max_wall_s,
        "elapsed_s": round(elapsed, 3),
        "within_wall": elapsed <= max_wall_s,
        "state": str(state_root),
        "baseline_state": str(baseline_root),
        "baseline_state_recorded": mode == "live" and baseline_state is None and recorded,
        "out": str(out_dir) if out_dir is not None else None,
        "closure": {
            "task_id": closure_task_id,
            "baseline": _run_digest(closure_baseline),
            "candidate": _run_digest(closure_run),
        },
        "scan10": [_run_digest(run) for run in scan_replays],
        "candidate_scan": [_run_digest(run) for run in candidate_fixture_runs],
        "provenance_counts": provenance,
        "proposal": {
            "proposal_id": proposal.proposal_id,
            "source": proposal.ext.get("source"),
            "cost_usd": proposal.cost_usd,
            "switches": proposal.switches.to_dict(),
        },
        "gate": {
            "decision_id": decision.decision_id,
            "verdict": decision.verdict,
            "reason": decision.reason,
            "baseline_solved": decision.scores["baseline"]["solved"],
            "candidate_solved": decision.scores["candidate"]["solved"],
        },
        "action": action,
        "overhead": overhead,
        "audit": audit_report,
        "export": {
            "out": str(out_dir) if out_dir is not None else None,
            "files": len(DATA_FILES) if index is not None else 0,
            "index_entries": len(index["files"]) if index else 0,
            "revision": index["revision"] if index else None,
        },
    }
    (state_root / "demo-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return summary


def _run_kind(run: Run) -> str:
    ext = run.ext if isinstance(run.ext, dict) else {}
    explicit = ext.get("provenance")
    if isinstance(explicit, dict) and explicit.get("kind") in ("fixture", "live", "replay"):
        return str(explicit["kind"])
    if ext.get("replay_of_run_id"):
        return "replay"
    return "fixture"


def _run_digest(run: Run) -> dict[str, Any]:
    return {
        "run_id": run.run_id,
        "task_id": run.task_id,
        "genome_id": run.genome_id,
        "outcome": run.outcome,
        "cost_usd": run.usage.cost_usd,
        "wall_ms": run.usage.wall_ms,
        "provenance": run.ext.get("provenance"),
        "replay_of_run_id": run.ext.get("replay_of_run_id"),
        "demo_group": run.ext.get("demo_group"),
    }


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

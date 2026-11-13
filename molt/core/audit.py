"""审计：只重算 canonical evidence，不重跑模型。

- 从 run / ledger 重算 result 的 metrics，与已存 result 比对；
- 从配对表 + 已存 genome 上限重算 gate 结论；
- 断言 search 流零 holdout id、report 流至少一条 ``holdout.accessed``；
- 对全部状态文件跑 secret scan，命中即失败。
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from molt.core.errors import AuditError, ValidationError
from molt.core.hash import canonical_json_bytes, content_hash, sha256_bytes
from molt.core.metrics import metrics_from_runs, paired_rows_from_runs
from molt.core.policy import SideEval, TaskEval, decide
from molt.core.redact import (
    REDACTION_VERSION,
    evidence_bundle,
    evidence_metrics,
    redaction_manifest_path,
    scan_secrets,
)
from molt.core.store import StateStore
from molt.core.types import GateDecision, Result, Run, Scenario, ScoreBlock

TOLERANCE = 1e-9
OVERHEAD_OWNER_PREFIX = "prop-"
REDACTION_MANIFEST_KEYS: tuple[str, ...] = (
    "redaction_version",
    "source_hash",
    "metrics_hash",
    "pre_metrics",
    "post_metrics",
)


def _score_equal(left: ScoreBlock, right: ScoreBlock) -> bool:
    return (
        left.solved == right.solved
        and left.total == right.total
        and abs(left.pass_at_1_first_attempt - right.pass_at_1_first_attempt) <= TOLERANCE
        and abs(left.cost_total_usd - right.cost_total_usd) <= TOLERANCE
        and _opt_equal(left.cost_per_solved_usd, right.cost_per_solved_usd)
        and abs(left.invalid_ratio - right.invalid_ratio) <= TOLERANCE
    )


def _opt_equal(left: float | None, right: float | None) -> bool:
    if left is None or right is None:
        return left is None and right is None
    return abs(left - right) <= TOLERANCE


def _invalid_ratio_equal(left: dict[str, float], right: dict[str, float]) -> bool:
    """1e-9 只吸收浮点噪声；union 与分侧的差异远大于该容差。"""
    return all(
        abs(float(left[key]) - float(right[key])) <= TOLERANCE
        for key in ("baseline", "candidate", "threshold")
    )


def _metrics_for_result(
    result: Result, runs: list[Run], scenario: Scenario
) -> dict[str, ScoreBlock]:
    replayed: dict[str, ScoreBlock] = {}
    if result.scope == "search":
        blocks = {"search": metrics_from_runs(runs, scenario.task_sets["search"])}
    else:
        search_runs = [run for run in runs if run.scope == "search"]
        holdout_runs = [run for run in runs if run.scope == "report"]
        blocks = {
            "search": metrics_from_runs(search_runs, scenario.task_sets["search"]),
            "holdout": metrics_from_runs(holdout_runs, scenario.task_sets["report"]),
        }
    for key, metrics in blocks.items():
        block = ScoreBlock.from_dict(metrics.to_score_block(), f"result.{key}")
        replayed[key] = block
    return replayed


def _side_from_decision(
    decision: GateDecision,
    side: str,
    limits,
    invalid_ratio: float,
    overhead_spend_usd: float,
    scenario_version: str,
) -> SideEval:
    tasks = []
    for row in decision.paired:
        tasks.append(
            TaskEval(
                task_id=row["task_id"],
                passed=bool(row[f"{side}_passed"]),
                cost_usd=float(row[f"{side}_cost_usd"]),
            )
        )
    return SideEval(
        genome_id=decision.scores[side]["genome_id"],
        scenario_version=scenario_version,
        tasks=tuple(tasks),
        limits=limits,
        invalid_ratio=invalid_ratio,
        overhead_spend_usd=overhead_spend_usd,
    )


def _runs_for_result(result: Result, runs: dict[str, Run]) -> tuple[list[Run], list[str]]:
    """按 ``run_refs`` 取 run；缺失引用单独返回，不静默丢弃。"""
    found: list[Run] = []
    missing: list[str] = []
    for ref in result.run_refs:
        run = runs.get(ref)
        if run is None:
            missing.append(ref)
        else:
            found.append(run)
    return found, missing


def _check_run_comparison_domain(
    run: Run, scenario: Scenario, owner: str, errors: list[str]
) -> None:
    """run 的 scenario/shell 版本必须与 scenario 权威值一致。"""
    if run.scenario_version != scenario.scenario_version:
        errors.append(
            f"{owner}: run {run.run_id}.scenario_version={run.scenario_version!r} "
            f"与 scenario={scenario.scenario_version!r} 不一致"
        )
    if run.shell_version != scenario.shell_version:
        errors.append(
            f"{owner}: run {run.run_id}.shell_version={run.shell_version!r} "
            f"与 scenario={scenario.shell_version!r} 不一致"
        )


def _decision_runs(
    decision_id: str,
    baseline_result: Result,
    candidate_result: Result,
    runs: dict[str, Run],
    errors: list[str],
) -> list[Run] | None:
    """合并两个 result 引用的 run，按真实 run_id 去重。

    同一 result 重复引用同一 run ID 属证据异常；同一真实 run 被两个 result
    共同引用则合法（去重后只计一次成本）。
    """
    collected: dict[str, Run] = {}
    checked: set[str] = set()
    duplicated = False
    for result in (baseline_result, candidate_result):
        if result.result_id in checked:
            continue
        checked.add(result.result_id)
        counts = Counter(result.run_refs)
        repeated = sorted(ref for ref, count in counts.items() if count > 1)
        if repeated:
            errors.append(
                f"decision {decision_id} result {result.result_id} 重复引用同一 run {repeated}，"
                "paired 与 invalid_ratio 均未重算"
            )
            duplicated = True
        for ref in result.run_refs:
            collected.setdefault(ref, runs[ref])
    if duplicated:
        return None
    return list(collected.values())


def _split_search_runs(
    decision_id: str,
    evidence: list[Run],
    baseline_genome_id: str,
    candidate_genome_id: str,
    errors: list[str],
) -> tuple[list[Run], list[Run]] | None:
    """按 genome 分侧后再取 ``scope == "search"``，避免 report run 污染成本。"""
    known = {baseline_genome_id, candidate_genome_id}
    foreign = sorted({run.genome_id for run in evidence if run.genome_id not in known})
    if foreign:
        errors.append(
            f"decision {decision_id} 引用的 run 出现第三 genome {foreign}，"
            f"既非 baseline={baseline_genome_id} 也非 candidate={candidate_genome_id}，"
            "paired 与 invalid_ratio 均未重算"
        )
        return None
    baseline_runs = [
        run
        for run in evidence
        if run.genome_id == baseline_genome_id and run.scope == "search"
    ]
    candidate_runs = [
        run
        for run in evidence
        if run.genome_id == candidate_genome_id and run.scope == "search"
    ]
    missing_sides = [
        side
        for side, side_runs in (("baseline", baseline_runs), ("candidate", candidate_runs))
        if not side_runs
    ]
    if missing_sides:
        errors.append(
            f"decision {decision_id} 的 {'/'.join(missing_sides)} 侧没有匹配 genome 的 "
            "search runs，paired 与 invalid_ratio 均未重算"
        )
        return None
    return baseline_runs, candidate_runs


def _compare_paired(
    decision_id: str,
    expected: list[dict[str, object]],
    actual: list[dict[str, object]],
    errors: list[str],
) -> None:
    """配对表必须覆盖且仅覆盖 scenario search 任务集，逐行核对 passed 与 cost。"""
    expected_order = [str(row["task_id"]) for row in expected]
    actual_order = [str(row["task_id"]) for row in actual]
    if actual_order != expected_order:
        expected_set = set(expected_order)
        actual_set = set(actual_order)
        missing = [task_id for task_id in expected_order if task_id not in actual_set]
        extra = [task_id for task_id in actual_order if task_id not in expected_set]
        errors.append(
            f"decision {decision_id}.paired 与 scenario search 任务集合/顺序不一致: "
            f"缺失 {missing} / 多余 {extra} / 顺序 {actual_order} != {expected_order}"
        )
    actual_by_task = {str(row["task_id"]): row for row in actual}
    for expected_row in expected:
        task_id = str(expected_row["task_id"])
        actual_row = actual_by_task.get(task_id)
        if actual_row is None:
            continue
        for side in ("baseline", "candidate"):
            passed_key = f"{side}_passed"
            if bool(actual_row[passed_key]) != bool(expected_row[passed_key]):
                errors.append(
                    f"decision {decision_id}.paired[{task_id}].{passed_key} 与 run 重算不一致: "
                    f"{actual_row[passed_key]!r} != {expected_row[passed_key]!r}"
                )
            cost_key = f"{side}_cost_usd"
            actual_cost = float(actual_row[cost_key])
            expected_cost = float(expected_row[cost_key])
            if abs(actual_cost - expected_cost) > TOLERANCE:
                errors.append(
                    f"decision {decision_id}.paired[{task_id}].{cost_key} 与 run 重算不一致: "
                    f"{actual_cost} != {expected_cost}"
                )


def _overhead_from_ledger(ledger) -> float:
    """``prop-*.jsonl`` 里 ``kind="overhead"`` 条目合计。

    B1 是单决策场景，对账范围固定为 ledger 全量；不接受 ``decision.ext.inputs.proposal_ids``
    这类"自述范围"（空列表曾把候选侧 overhead 短路成 0）。未来若真的要一 ledger 多决策，
    必须新增显式 scope 字段并走 ADR，而不是靠可选过滤。
    """
    total = 0.0
    for owner in ledger.owners():
        if not owner.startswith(OVERHEAD_OWNER_PREFIX):
            continue
        for entry in ledger.read(owner):
            if entry.get("kind") == "overhead":
                total += float(entry.get("cost_usd", 0.0))
    return total


def _iter_state_text_files(root: Path) -> Iterable[Path]:
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.suffix in (".json", ".jsonl"):
            yield path


def audit_state(
    store: StateStore,
    scenario: Scenario,
    *,
    check_secrets: bool = True,
) -> dict[str, Any]:
    errors: list[str] = []
    try:
        runs = {run.run_id: run for run in store.list_runs()}
        results = store.list_results()
        decisions = store.list_decisions()
    except ValidationError as exc:
        raise AuditError(f"审计失败：\n- 证据记录不可解析: {exc}") from exc

    for result in results:
        if result.scenario_version != scenario.scenario_version:
            errors.append(
                f"result {result.result_id}.scenario_version={result.scenario_version!r} "
                f"与 scenario={scenario.scenario_version!r} 不一致"
            )
        try:
            result_runs = [runs[ref] for ref in result.run_refs]
        except KeyError as exc:
            errors.append(f"result {result.result_id} 引用不存在的 run {exc}")
            continue
        for run in result_runs:
            _check_run_comparison_domain(
                run, scenario, f"result {result.result_id}", errors
            )
        replayed = _metrics_for_result(result, result_runs, scenario)
        for key, block in replayed.items():
            stored = result.scores.get(key)
            if stored is None:
                errors.append(f"result {result.result_id} 缺少 {key} 分数")
            elif not _score_equal(stored, block):
                errors.append(
                    f"result {result.result_id}.{key} 重算不一致: "
                    f"{stored.to_dict()} != {block.to_dict()}"
                )

    # 账本与 run 成本交叉核对
    for run in runs.values():
        entries = store_ledger(store).read(run.run_id)
        total = sum(float(entry.get("cost_usd", 0.0)) for entry in entries)
        if abs(total - run.usage.cost_usd) > TOLERANCE:
            errors.append(f"run {run.run_id} 成本与账本不一致: {run.usage.cost_usd} != {total}")

    ledger = store_ledger(store)
    for decision in decisions:
        if decision.scenario_version != scenario.scenario_version:
            errors.append(
                f"decision {decision.decision_id}.scenario_version="
                f"{decision.scenario_version!r} 与 scenario={scenario.scenario_version!r} 不一致"
            )
        if decision.shell_version != scenario.shell_version:
            errors.append(
                f"decision {decision.decision_id}.shell_version="
                f"{decision.shell_version!r} 与 scenario={scenario.shell_version!r} 不一致"
            )
        # D3：顶层 genome ID 必须与 scores 同侧 ID 一致；不一致时不读预算、不重放。
        baseline_genome_id = decision.scores["baseline"]["genome_id"]
        candidate_genome_id = decision.scores["candidate"]["genome_id"]
        id_mismatch = False
        if decision.baseline_genome_id != baseline_genome_id:
            errors.append(
                f"decision {decision.decision_id}.baseline_genome_id="
                f"{decision.baseline_genome_id!r} 与 scores.baseline.genome_id="
                f"{baseline_genome_id!r} 不一致，跳过该 decision 的 budget / paired / gate 重放"
            )
            id_mismatch = True
        if decision.candidate_genome_id != candidate_genome_id:
            errors.append(
                f"decision {decision.decision_id}.candidate_genome_id="
                f"{decision.candidate_genome_id!r} 与 scores.candidate.genome_id="
                f"{candidate_genome_id!r} 不一致，跳过该 decision 的 budget / paired / gate 重放"
            )
            id_mismatch = True
        if id_mismatch:
            continue
        try:
            baseline_genome = store.read_genome(baseline_genome_id)
            candidate_genome = store.read_genome(candidate_genome_id)
        except ValidationError as exc:
            errors.append(f"decision {decision.decision_id} 缺少 genome: {exc}")
            continue
        # invalid ratio 只能从 run 重算；ext.inputs 缺失即失败，不得回退到 decision 自述值。
        inputs = decision.ext.get("inputs")
        if not isinstance(inputs, dict):
            errors.append(
                f"decision {decision.decision_id} 缺少 ext.inputs，"
                "无法从 run 重算 invalid_ratio（拒绝回退到 decision 自述值）"
            )
            continue
        baseline_result = _maybe_result(results, inputs.get("baseline_result_id"))
        candidate_result = _maybe_result(results, inputs.get("candidate_result_id"))
        if baseline_result is None or candidate_result is None:
            errors.append(
                f"decision {decision.decision_id} ext.inputs 引用的 result 不存在: "
                f"{baseline_result is None} baseline / {candidate_result is None} candidate"
            )
            continue
        baseline_runs, missing = _runs_for_result(baseline_result, runs)
        if missing:
            errors.append(
                f"decision {decision.decision_id} baseline result 引用不存在的 run {missing}"
            )
            continue
        candidate_runs, missing = _runs_for_result(candidate_result, runs)
        if missing:
            errors.append(
                f"decision {decision.decision_id} candidate result 引用不存在的 run {missing}"
            )
            continue
        # verifier_hash 只能与 decision 记录比对；范围是两侧 result 的全部 run，不限 K5。
        for label, side_runs in (
            ("baseline", baseline_runs),
            ("candidate", candidate_runs),
        ):
            for run in {run.run_id: run for run in side_runs}.values():
                if run.verifier_hash != decision.verifier_hash:
                    errors.append(
                        f"decision {decision.decision_id} {label} result 的 run "
                        f"{run.run_id}.verifier_hash={run.verifier_hash!r} "
                        f"与 decision={decision.verifier_hash!r} 不一致"
                    )
        # K5 + invalid_ratio：共享同一份按 genome + search scope 分侧后的 runs。
        if baseline_genome_id == candidate_genome_id:
            errors.append(
                f"decision {decision.decision_id} 两侧 genome 相同"
                f"（{baseline_genome_id}），paired 与 invalid_ratio 均未重算"
            )
            continue
        evidence = _decision_runs(
            decision.decision_id, baseline_result, candidate_result, runs, errors
        )
        if evidence is None:
            continue
        split = _split_search_runs(
            decision.decision_id,
            evidence,
            baseline_genome_id,
            candidate_genome_id,
            errors,
        )
        if split is None:
            continue
        baseline_search_runs, candidate_search_runs = split
        baseline_ratio = metrics_from_runs(
            baseline_search_runs, scenario.task_sets["search"]
        ).invalid_ratio
        candidate_ratio = metrics_from_runs(
            candidate_search_runs, scenario.task_sets["search"]
        ).invalid_ratio
        expected_paired = paired_rows_from_runs(
            baseline_search_runs, candidate_search_runs, scenario.task_sets["search"]
        )
        _compare_paired(decision.decision_id, expected_paired, decision.paired, errors)
        # overhead 只能与 prop-*.jsonl 的 overhead 条目对账。
        ledger_overhead = _overhead_from_ledger(ledger)
        declared_overhead = decision.ext.get("overhead_spend_usd")
        if isinstance(declared_overhead, bool) or not isinstance(declared_overhead, int | float):
            errors.append(
                f"decision {decision.decision_id} 缺少合法的 ext.overhead_spend_usd"
                f"（ledger 实际 {ledger_overhead}）"
            )
            continue
        overhead = float(declared_overhead)
        if abs(overhead - ledger_overhead) > TOLERANCE:
            errors.append(
                f"decision {decision.decision_id}.ext.overhead_spend_usd={overhead} "
                f"与 ledger overhead 合计 {ledger_overhead} 不一致"
            )
            continue
        baseline = _side_from_decision(
            decision,
            "baseline",
            baseline_genome.budget,
            baseline_ratio,
            0.0,
            scenario.scenario_version,
        )
        candidate = _side_from_decision(
            decision,
            "candidate",
            candidate_genome.budget,
            candidate_ratio,
            overhead,
            scenario.scenario_version,
        )
        replayed_decision = decide(
            scenario.policy,
            baseline,
            candidate,
            decision_id=decision.decision_id,
            scenario_version=scenario.scenario_version,
            shell_version=decision.shell_version,
            verifier_hash=decision.verifier_hash,
            created_at=decision.created_at,
        )
        for field in ("verdict", "reason", "budget_check", "invalid_ratio", "scores"):
            replayed_value = getattr(replayed_decision, field)
            stored_value = getattr(decision, field)
            if field == "invalid_ratio":
                if _invalid_ratio_equal(replayed_value, stored_value):
                    continue
            elif replayed_value == stored_value:
                continue
            errors.append(
                f"decision {decision.decision_id}.{field} 重算不一致: "
                f"{replayed_value!r} != {stored_value!r}"
            )

    events = store_events(store)
    holdout_ids = set(scenario.task_sets["report"])
    for event in events:
        if event.scope == "search":
            payload = json.dumps(event.payload, ensure_ascii=False)
            for task_id in holdout_ids:
                if task_id in payload:
                    errors.append(f"search 事件 {event.event_id} 泄漏 holdout id {task_id!r}")
    report_events = [event for event in events if event.scope == "report"]
    if not any(event.type == "holdout.accessed" for event in report_events):
        errors.append("report 流缺少 holdout.accessed 事件")

    _check_redaction_manifest(store, scenario, list(runs.values()), results, decisions, errors)

    if check_secrets:
        for path in _iter_state_text_files(store.root):
            hits = scan_secrets(path.read_text(encoding="utf-8"))
            if hits:
                errors.append(f"secret scan 命中 {hits}: {path}")

    if errors:
        raise AuditError("审计失败：\n- " + "\n- ".join(errors))
    return {
        "runs": len(runs),
        "results": len(results),
        "decisions": len(decisions),
        "events": len(events),
        "report_events": len(report_events),
    }


def _check_redaction_manifest(
    store: StateStore,
    scenario: Scenario,
    runs: list[Run],
    results: list[Result],
    decisions: list[GateDecision],
    errors: list[str],
) -> None:
    """manifest 必须存在，且 pre/post 指标与当前证据链重算一致。"""
    path = redaction_manifest_path(store.root, scenario.scenario_id)
    if not path.exists():
        errors.append(f"缺少脱敏 manifest: {path}")
        return
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        errors.append(f"脱敏 manifest 不可解析 {path}: {exc}")
        return
    if not isinstance(manifest, dict):
        errors.append(f"脱敏 manifest 必须是 object: {path}")
        return
    missing = [key for key in REDACTION_MANIFEST_KEYS if key not in manifest]
    if missing:
        errors.append(f"脱敏 manifest 缺少字段 {missing}: {path}")
        return
    if manifest["redaction_version"] != REDACTION_VERSION:
        errors.append(
            f"脱敏 manifest redaction_version={manifest['redaction_version']!r} "
            f"与 {REDACTION_VERSION!r} 不一致"
        )
    pre = manifest["pre_metrics"]
    post = manifest["post_metrics"]
    if pre != post:
        errors.append("脱敏前后指标不一致: pre_metrics != post_metrics")
    if manifest["metrics_hash"] != content_hash(pre):
        errors.append("脱敏 manifest.metrics_hash 与 pre_metrics 内容哈希不一致")
    bundle = evidence_bundle(
        scenario_id=scenario.scenario_id,
        scenario_version=scenario.scenario_version,
        runs=runs,
        results=results,
        decisions=decisions,
    )
    if manifest["source_hash"] != sha256_bytes(canonical_json_bytes(bundle)):
        errors.append("脱敏 manifest.source_hash 与当前证据链不一致")
    if content_hash(pre) != content_hash(evidence_metrics(bundle)):
        errors.append("脱敏 manifest.pre_metrics 与当前证据链重算不一致")


def _maybe_result(results: list[Result], result_id: Any) -> Result | None:
    if not isinstance(result_id, str):
        return None
    for result in results:
        if result.result_id == result_id:
            return result
    return None


def store_ledger(store: StateStore):
    from molt.core.ledger import Ledger

    return Ledger(store.root)


def store_events(store: StateStore):
    from molt.core.events import EventLog

    return EventLog(store.root).all_events()

"""审计：只重算 canonical evidence，不重跑模型。

- 从 run / ledger 重算 result 的 metrics，与已存 result 比对；
- 从配对表 + 已存 genome 上限重算 gate 结论；
- 断言 search 流零 holdout id、report 流至少一条 ``holdout.accessed``；
- 对全部状态文件跑 secret scan，命中即失败。
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from molt.core.errors import AuditError, ValidationError
from molt.core.metrics import metrics_from_runs
from molt.core.policy import SideEval, TaskEval, decide
from molt.core.redact import scan_secrets
from molt.core.store import StateStore
from molt.core.types import GateDecision, Result, Run, Scenario, ScoreBlock

TOLERANCE = 1e-9


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
        tasks=tuple(tasks),
        limits=limits,
        invalid_ratio=invalid_ratio,
        overhead_spend_usd=overhead_spend_usd,
    )


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
    runs = {run.run_id: run for run in store.list_runs()}
    results = store.list_results()
    decisions = store.list_decisions()

    for result in results:
        try:
            result_runs = [runs[ref] for ref in result.run_refs]
        except KeyError as exc:
            errors.append(f"result {result.result_id} 引用不存在的 run {exc}")
            continue
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

    for decision in decisions:
        try:
            baseline_genome = store.read_genome(decision.baseline_genome_id)
            candidate_genome = store.read_genome(decision.candidate_genome_id)
        except ValidationError as exc:
            errors.append(f"decision {decision.decision_id} 缺少 genome: {exc}")
            continue
        inputs = decision.ext.get("inputs", {})
        overhead = float(decision.ext.get("overhead_spend_usd", 0.0))
        baseline_ratio = decision.invalid_ratio["baseline"]
        candidate_ratio = decision.invalid_ratio["candidate"]
        baseline_result = _maybe_result(results, inputs.get("baseline_result_id"))
        candidate_result = _maybe_result(results, inputs.get("candidate_result_id"))
        if baseline_result is not None and candidate_result is not None:
            baseline_runs = [runs[ref] for ref in baseline_result.run_refs if ref in runs]
            candidate_runs = [runs[ref] for ref in candidate_result.run_refs if ref in runs]
            baseline_ratio = metrics_from_runs(
                baseline_runs, scenario.task_sets["search"]
            ).invalid_ratio
            candidate_ratio = metrics_from_runs(
                candidate_runs, scenario.task_sets["search"]
            ).invalid_ratio
        baseline = _side_from_decision(
            decision, "baseline", baseline_genome.budget, baseline_ratio, 0.0
        )
        candidate = _side_from_decision(
            decision, "candidate", candidate_genome.budget, candidate_ratio, overhead
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
            if getattr(replayed_decision, field) != getattr(decision, field):
                errors.append(
                    f"decision {decision.decision_id}.{field} 重算不一致: "
                    f"{getattr(replayed_decision, field)!r} != {getattr(decision, field)!r}"
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

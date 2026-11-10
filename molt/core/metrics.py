"""指标口径：``cost_per_solved`` 只含执行成本，首轮 pass@1 是门禁统计量。"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from molt.core.types import Run

NON_PASSING_OUTCOMES: tuple[str, ...] = ("invalid", "timeout", "budget_exceeded", "error")


@dataclass(frozen=True, slots=True)
class Metrics:
    """一个 genome 在一个题集上的执行侧指标。"""

    solved: int
    total: int
    cost_total_usd: float
    overhead_spend_usd: float
    invalid_ratio: float

    @property
    def pass_at_1_first_attempt(self) -> float:
        return self.solved / self.total if self.total else 0.0

    @property
    def cost_per_solved_usd(self) -> float | None:
        if self.solved == 0:
            return None
        return self.cost_total_usd / self.solved

    def to_score_block(self) -> dict[str, object]:
        return {
            "solved": self.solved,
            "total": self.total,
            "pass_at_1_first_attempt": self.pass_at_1_first_attempt,
            "cost_total_usd": self.cost_total_usd,
            "cost_per_solved_usd": self.cost_per_solved_usd,
            "invalid_ratio": self.invalid_ratio,
        }


def first_attempt_runs(runs: Iterable[Run]) -> dict[str, Run]:
    """按 task_id 取 attempt=1 的 run；重复只进 diagnostic。"""
    first: dict[str, Run] = {}
    for run in runs:
        if run.attempt != 1:
            continue
        existing = first.get(run.task_id)
        if existing is None:
            first[run.task_id] = run
    return first


def metrics_from_runs(
    runs: Sequence[Run],
    task_ids: Sequence[str],
    *,
    overhead_spend_usd: float = 0.0,
) -> Metrics:
    """从 ``run`` 记录重算指标。

    - 成本分母是该 genome 在题集上全部执行 run（含 invalid / 超时 / 超预算 / 判废）；
    - 通过数只认首轮（attempt=1），重复不改变判定；
    - ``overhead_spend_usd`` 是提案成本，单列，不并入 ``cost_per_solved``。
    """
    first = first_attempt_runs(runs)
    solved = sum(
        1 for task_id in task_ids if first.get(task_id) and first[task_id].outcome == "passed"
    )
    total = len(task_ids)
    cost_total = sum(run.usage.cost_usd for run in runs)
    invalid = sum(1 for run in runs if run.outcome in NON_PASSING_OUTCOMES)
    invalid_ratio = invalid / len(runs) if runs else 0.0
    return Metrics(
        solved=solved,
        total=total,
        cost_total_usd=cost_total,
        overhead_spend_usd=overhead_spend_usd,
        invalid_ratio=invalid_ratio,
    )


def paired_rows_from_runs(
    baseline_runs: Sequence[Run],
    candidate_runs: Sequence[Run],
    task_ids: Sequence[str],
) -> list[dict[str, object]]:
    """构造逐题配对表；成本按 task 汇总全部 attempt。"""
    base_first = first_attempt_runs(baseline_runs)
    cand_first = first_attempt_runs(candidate_runs)
    base_cost: dict[str, float] = {task_id: 0.0 for task_id in task_ids}
    cand_cost: dict[str, float] = {task_id: 0.0 for task_id in task_ids}
    for run in baseline_runs:
        if run.task_id in base_cost:
            base_cost[run.task_id] += run.usage.cost_usd
    for run in candidate_runs:
        if run.task_id in cand_cost:
            cand_cost[run.task_id] += run.usage.cost_usd
    rows = []
    for task_id in task_ids:
        rows.append(
            {
                "task_id": task_id,
                "baseline_passed": bool(base_first.get(task_id))
                and base_first[task_id].outcome == "passed",
                "candidate_passed": bool(cand_first.get(task_id))
                and cand_first[task_id].outcome == "passed",
                "baseline_cost_usd": base_cost[task_id],
                "candidate_cost_usd": cand_cost[task_id],
            }
        )
    return rows

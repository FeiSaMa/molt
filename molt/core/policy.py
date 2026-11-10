"""门禁策略：单一 owner 是 ``scenario.policy``。

判定顺序固定为：
``incomparable``（上限不等 / 分母为 0）→ ``unstable``（invalid 超阈）→
``budget_exceeded`` → 非劣 k → 单题执行成本。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from molt.core.errors import GateError
from molt.core.types import Budget, GateDecision, Policy


@dataclass(frozen=True, slots=True)
class TaskEval:
    """单题执行结果；成本含该题全部 attempt。"""

    task_id: str
    passed: bool
    cost_usd: float


@dataclass(frozen=True, slots=True)
class SideEval:
    """门禁一侧的输入。``overhead_spend_usd`` 是提案成本，单独计。"""

    genome_id: str
    tasks: tuple[TaskEval, ...]
    limits: Budget
    invalid_ratio: float
    overhead_spend_usd: float = 0.0

    @property
    def solved(self) -> int:
        return sum(1 for task in self.tasks if task.passed)

    @property
    def total(self) -> int:
        return len(self.tasks)

    @property
    def cost_total_usd(self) -> float:
        return sum(task.cost_usd for task in self.tasks)

    @property
    def cost_per_solved_usd(self) -> float | None:
        if self.solved == 0:
            return None
        return self.cost_total_usd / self.solved


def _invalid_ratio_ok(side: SideEval, threshold: float) -> bool:
    return side.invalid_ratio <= threshold + 1e-12


def decide(
    policy: Policy,
    baseline: SideEval,
    candidate: SideEval,
    *,
    decision_id: str,
    scenario_version: str,
    shell_version: str,
    verifier_hash: str,
    created_at: str,
) -> GateDecision:
    """按唯一不等式集合给出结论，并携带逐题配对表。"""
    if baseline.total != candidate.total:
        raise GateError(f"配对题数不一致：{baseline.total} vs {candidate.total}")
    baseline_ids = [task.task_id for task in baseline.tasks]
    candidate_ids = [task.task_id for task in candidate.tasks]
    if baseline_ids != candidate_ids:
        raise GateError("配对表 task_id 顺序或集合不一致")

    limits_identical = baseline.limits == candidate.limits
    execution_exceeded = candidate.cost_total_usd > policy.budget.execution_budget_usd + 1e-12
    overhead_exceeded = (
        candidate.overhead_spend_usd > policy.budget.search_overhead_budget_usd + 1e-12
    )
    if execution_exceeded and overhead_exceeded:
        budget_reason = "execution+overhead"
    elif execution_exceeded:
        budget_reason = "execution"
    elif overhead_exceeded:
        budget_reason = "overhead"
    else:
        budget_reason = "ok"

    if not limits_identical:
        verdict, reason = "incomparable", "limits_differ"
    elif baseline.solved == 0 or candidate.solved == 0:
        verdict, reason = "incomparable", "zero_denominator"
    elif not _invalid_ratio_ok(baseline, policy.invalid_ratio_max) or not _invalid_ratio_ok(
        candidate, policy.invalid_ratio_max
    ):
        verdict, reason = "unstable", "invalid_ratio_exceeded"
    elif execution_exceeded or overhead_exceeded:
        verdict, reason = "reject", "budget_exceeded"
    elif candidate.solved < baseline.solved - policy.k:
        verdict, reason = "reject", "solved_dropped"
    else:
        base_cost = baseline.cost_per_solved_usd
        cand_cost = candidate.cost_per_solved_usd
        if base_cost is None or cand_cost is None:
            verdict, reason = "incomparable", "zero_denominator"
        elif cand_cost > base_cost + 1e-12:
            verdict, reason = "reject", "cost_not_lower"
        elif cand_cost < base_cost - 1e-12:
            verdict, reason = "adopt", "lower_cost_per_solved"
        else:
            verdict, reason = "adopt", "non_inferior"

    paired = [
        {
            "task_id": base.task_id,
            "baseline_passed": base.passed,
            "candidate_passed": cand.passed,
            "baseline_cost_usd": base.cost_usd,
            "candidate_cost_usd": cand.cost_usd,
        }
        for base, cand in zip(baseline.tasks, candidate.tasks, strict=True)
    ]
    payload: dict[str, Any] = {
        "schema_version": "1.0.0",
        "decision_id": decision_id,
        "scenario_version": scenario_version,
        "shell_version": shell_version,
        "verifier_hash": verifier_hash,
        "baseline_genome_id": baseline.genome_id,
        "candidate_genome_id": candidate.genome_id,
        "verdict": verdict,
        "reason": reason,
        "k": policy.k,
        "limits_identical": limits_identical,
        "budget_check": {
            "execution_exceeded": execution_exceeded,
            "overhead_exceeded": overhead_exceeded,
            "reason": budget_reason,
        },
        "invalid_ratio": {
            "baseline": baseline.invalid_ratio,
            "candidate": candidate.invalid_ratio,
            "threshold": policy.invalid_ratio_max,
        },
        "scores": {
            side: {
                "genome_id": evaluation.genome_id,
                "solved": evaluation.solved,
                "total": evaluation.total,
                "cost_per_solved_usd": evaluation.cost_per_solved_usd,
            }
            for side, evaluation in (("baseline", baseline), ("candidate", candidate))
        },
        "paired": paired,
        "created_at": created_at,
        "ext": {},
    }
    return GateDecision.from_dict(payload)


def side_from_tasks(
    genome_id: str,
    tasks: list[TaskEval],
    limits: Budget,
    invalid_ratio: float,
    overhead_spend_usd: float = 0.0,
) -> SideEval:
    """便捷构造器，保持 task 顺序。"""
    return SideEval(
        genome_id=genome_id,
        tasks=tuple(tasks),
        limits=limits,
        invalid_ratio=invalid_ratio,
        overhead_spend_usd=overhead_spend_usd,
    )


def task_side(
    genome_id: str,
    tasks: list[dict[str, Any]],
    limits: Budget,
    invalid_ratio: float,
    overhead_spend_usd: float = 0.0,
) -> SideEval:
    """从 ``{task_id, passed, cost_usd}`` 列表构造一侧。"""
    parsed = [
        TaskEval(
            task_id=str(item["task_id"]),
            passed=bool(item["passed"]),
            cost_usd=float(item["cost_usd"]),
        )
        for item in tasks
    ]
    return side_from_tasks(genome_id, parsed, limits, invalid_ratio, overhead_spend_usd)

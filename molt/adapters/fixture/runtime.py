"""确定性 fixture Runtime：按 switches 与题目 fixture 返回可复现结果。

它不联网、不调用模型；用于 B1 的门禁、审计、事务与隔离测试。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from molt.core.hash import sha256_file
from molt.core.ports import RuntimeOutcome
from molt.core.types import Genome, Task, Usage

FACTORS: dict[str, dict[str, float]] = {
    "thinking": {"off": 0.55, "low": 0.8, "high": 1.6},
    "context": {"full": 1.15, "minimal": 0.9},
    "tools": {"bash": 1.0},
}


@dataclass(frozen=True, slots=True)
class FixtureRuntime:
    """题目 fixture 控制可解性、成本与异常终态。"""

    cost_scale: float = 1.0
    write_bytecode: bool = False

    def _fixture(self, task: Task) -> dict[str, Any]:
        return dict(task.ext.get("fixture", {}))

    def run(self, genome: Genome, task: Task, workdir: Path) -> RuntimeOutcome:
        fixture = self._fixture(task)
        switches = genome.switches
        cost = (
            float(fixture.get("base_cost_usd", 1.0))
            * FACTORS["thinking"][switches.thinking]
            * FACTORS["context"][switches.context]
            * FACTORS["tools"][switches.tools]
            * self.cost_scale
        )
        cost = round(cost, 6)
        tokens_in = int(round(1000 * FACTORS["context"][switches.context]))
        tokens_out = int(round(500 * FACTORS["thinking"][switches.thinking]))
        turns = {"off": 1, "low": 2, "high": 4}[switches.thinking]
        wall_ms = int(round(1000 * cost))
        usage = Usage(
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=cost,
            wall_ms=wall_ms,
            turns=turns,
        )
        artifacts: dict[str, str] = {}
        abnormal = None
        for key in ("invalid", "timeout", "over_budget"):
            if fixture.get(key):
                abnormal = "budget_exceeded" if key == "over_budget" else key
                break
        if abnormal is None and switches.thinking in fixture.get("solvable_at", []):
            target = str(fixture.get("target", "solution.py"))
            solution = str(fixture.get("solution", ""))
            path = Path(workdir) / target
            path.write_text(solution, encoding="utf-8")
            artifacts[target] = sha256_file(path)
        return RuntimeOutcome(
            outcome=abnormal or "passed",
            usage=usage,
            started_at=str(fixture.get("started_at", "2026-01-01T00:00:00Z")),
            finished_at=str(fixture.get("finished_at", "2026-01-01T00:00:01Z")),
            artifacts=artifacts,
            diagnostic={"thinking": switches.thinking, "abnormal": abnormal},
        )

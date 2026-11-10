"""执行引擎：把端口组合成可复现的 run/result 流水线。

约束：
- 搜索路径只能构造 ``SearchHistory``，拿不到 holdout / report 分数；
- 每个 run 的事件写入自己的 per-run 文件，ledger 同理；
- run / result 落盘前都经过 v1 数据类校验。
"""

from __future__ import annotations

import shutil
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from molt.core.errors import ValidationError
from molt.core.events import EventLog, utc_now
from molt.core.ledger import Ledger
from molt.core.metrics import Metrics, metrics_from_runs
from molt.core.ports import Harness, HarnessOutcome, TaskProvider, Verifier
from molt.core.store import StateStore
from molt.core.types import (
    Genome,
    HistoryEntry,
    Proposal,
    Result,
    Run,
    SearchHistory,
    Task,
)


@dataclass(frozen=True, slots=True)
class EngineConfig:
    repo_root: Path
    work_root: Path
    shell_version: str
    verifier_hash: str
    seed_control: str = "none"


class Engine:
    """run 生成器；不直接依赖任何 adapter。"""

    def __init__(
        self,
        store: StateStore,
        task_provider: TaskProvider,
        harness: Harness,
        verifier: Verifier,
        config: EngineConfig,
    ) -> None:
        self.store = store
        self.task_provider = task_provider
        self.harness = harness
        self.verifier = verifier
        self.config = config
        self.events = EventLog(store.root)
        self.ledger = Ledger(store.root)

    # -- 单次 run ---------------------------------------------------------
    def run_task(
        self,
        genome: Genome,
        task: Task,
        *,
        scope: str,
        attempt: int = 1,
        run_id: str | None = None,
    ) -> Run:
        run_id = run_id or _new_run_id(genome.genome_id, task.task_id, attempt)
        workdir = self.config.work_root / run_id / "workspace"
        source = self.config.repo_root / task.workspace_ref
        if not source.exists():
            raise ValidationError(f"task workspace 不存在: {task.workspace_ref}")
        if workdir.parent.exists():
            shutil.rmtree(workdir.parent)
        shutil.copytree(source, workdir)

        started_at = utc_now()
        if scope == "search":
            self.events.emit(
                scope="search",
                event_type="run.started",
                run_id=run_id,
                payload={"task_id": task.task_id, "genome_id": genome.genome_id},
            )
        try:
            outcome: HarnessOutcome = self.harness.run(genome, task, workdir)
        except Exception as exc:  # noqa: BLE001 - 任何 harness 异常都判 error
            finished_at = utc_now()
            record = Run.from_dict(
                {
                    "schema_version": "1.0.0",
                    "run_id": run_id,
                    "scenario_version": genome.scenario_version,
                    "genome_id": genome.genome_id,
                    "task_id": task.task_id,
                    "scope": scope,
                    "attempt": attempt,
                    "shell_version": self.config.shell_version,
                    "verifier_hash": self.config.verifier_hash,
                    "seed_control": self.config.seed_control,
                    "started_at": started_at,
                    "finished_at": finished_at,
                    "outcome": "error",
                    "usage": {
                        "tokens_in": 0,
                        "tokens_out": 0,
                        "cost_usd": 0.0,
                        "wall_ms": 0,
                        "turns": 0,
                    },
                    "verification": {
                        "tests_hash_before": self.config.verifier_hash,
                        "tests_hash_after": self.config.verifier_hash,
                        "matches": True,
                    },
                    "artifacts": {},
                    "ext": {"error": f"{type(exc).__name__}: {exc}"},
                }
            )
            self._persist(record)
            return record
        finished_at = utc_now()
        record = Run.from_dict(
            {
                "schema_version": "1.0.0",
                "run_id": run_id,
                "scenario_version": genome.scenario_version,
                "genome_id": genome.genome_id,
                "task_id": task.task_id,
                "scope": scope,
                "attempt": attempt,
                "shell_version": self.config.shell_version,
                "verifier_hash": self.config.verifier_hash,
                "seed_control": self.config.seed_control,
                "started_at": started_at,
                "finished_at": finished_at,
                "outcome": outcome.final_outcome,
                "usage": outcome.runtime.usage.to_dict(),
                "verification": {
                    "tests_hash_before": outcome.verification.tests_hash_before,
                    "tests_hash_after": outcome.verification.tests_hash_after,
                    "matches": outcome.verification.matches,
                },
                "artifacts": dict(outcome.runtime.artifacts),
                "ext": {},
            }
        )
        self._persist(record)
        return record

    def _persist(self, record: Run) -> None:
        self.store.write_run(record)
        self.ledger.append(
            record.run_id,
            {
                "kind": "execution",
                "cost_usd": record.usage.cost_usd,
                "tokens_in": record.usage.tokens_in,
                "tokens_out": record.usage.tokens_out,
                "wall_ms": record.usage.wall_ms,
                "outcome": record.outcome,
                "scope": record.scope,
                "task_id": record.task_id,
            },
        )
        if record.scope == "search":
            self.events.emit(
                scope="search",
                event_type="run.finished",
                run_id=record.run_id,
                payload={"task_id": record.task_id, "outcome": record.outcome},
            )

    # -- 套件 -------------------------------------------------------------
    def run_suite(
        self,
        genome: Genome,
        tasks: Sequence[Task],
        *,
        scope: str,
        attempts: int = 1,
    ) -> list[Run]:
        if scope == "report":
            self.events.emit(
                scope="report",
                event_type="holdout.accessed",
                payload={"task_ids": [task.task_id for task in tasks]},
            )
        runs: list[Run] = []
        for task in tasks:
            if task.scope != scope:
                raise ValidationError(f"task {task.task_id} scope={task.scope} 不属于 {scope} 套件")
            for attempt in range(1, attempts + 1):
                runs.append(self.run_task(genome, task, scope=scope, attempt=attempt))
        return runs

    # -- 汇总 -------------------------------------------------------------
    def evaluate_search(
        self,
        genome: Genome,
        runs: Sequence[Run],
        task_ids: Sequence[str],
        *,
        result_id: str,
        generated_at: str | None = None,
        overhead_spend_usd: float = 0.0,
    ) -> Result:
        metrics = metrics_from_runs(runs, task_ids, overhead_spend_usd=overhead_spend_usd)
        result = Result.from_dict(
            {
                "schema_version": "1.0.0",
                "result_id": result_id,
                "scenario_version": genome.scenario_version,
                "genome_id": genome.genome_id,
                "scope": "search",
                "seed_control": self.config.seed_control,
                "run_refs": [run.run_id for run in runs],
                "scores": {"search": metrics.to_score_block()},
                "generated_at": generated_at or utc_now(),
                "ext": {},
            }
        )
        self.store.write_result(result)
        return result

    def evaluate_report(
        self,
        genome: Genome,
        search_runs: Sequence[Run],
        holdout_runs: Sequence[Run],
        *,
        search_task_ids: Sequence[str],
        holdout_task_ids: Sequence[str],
        result_id: str,
        generated_at: str | None = None,
        overhead_spend_usd: float = 0.0,
    ) -> Result:
        search_metrics = metrics_from_runs(
            search_runs, search_task_ids, overhead_spend_usd=overhead_spend_usd
        )
        holdout_metrics = metrics_from_runs(holdout_runs, holdout_task_ids)
        result = Result.from_dict(
            {
                "schema_version": "1.0.0",
                "result_id": result_id,
                "scenario_version": genome.scenario_version,
                "genome_id": genome.genome_id,
                "scope": "report",
                "seed_control": self.config.seed_control,
                "run_refs": [run.run_id for run in search_runs]
                + [run.run_id for run in holdout_runs],
                "scores": {
                    "search": search_metrics.to_score_block(),
                    "holdout": holdout_metrics.to_score_block(),
                },
                "generated_at": generated_at or utc_now(),
                "ext": {},
            }
        )
        self.store.write_result(result)
        return result

    # -- 搜索 / 报告类型隔离 ---------------------------------------------
    def search_history(self, genome: Genome, runs: Sequence[Run]) -> SearchHistory:
        history = SearchHistory(
            scenario_version=genome.scenario_version,
            genome_id=genome.genome_id,
        )
        for run in runs:
            if run.scope != "search":
                raise ValidationError("SearchHistory 不接受 report run")
            history.add(
                HistoryEntry(
                    task_id=run.task_id,
                    passed=run.outcome == "passed",
                    cost_usd=run.usage.cost_usd,
                )
            )
        return history

    def propose(self, genome: Genome, history: SearchHistory):
        return self.harness.propose(history, genome)

    def record_proposal(
        self,
        proposal: Proposal,
        *,
        event_id: str | None = None,
        ts: str | None = None,
    ) -> None:
        """Proposal + overhead ledger + ``proposal.generated`` 事件的唯一落盘口径。"""
        self.store.write_proposal(proposal)
        entries = self.ledger.read(proposal.proposal_id)
        if not any(entry.get("kind") == "overhead" for entry in entries):
            self.ledger.append(
                proposal.proposal_id,
                {
                    "kind": "overhead",
                    "cost_usd": proposal.cost_usd,
                    "source": proposal.ext.get("source"),
                    "model": proposal.ext.get("model"),
                    "counted_as": proposal.ext.get("counted_as"),
                },
            )
        self.events.emit(
            scope="search",
            event_type="proposal.generated",
            payload={
                "proposal_id": proposal.proposal_id,
                "parent_genome_id": proposal.parent_id,
            },
            event_id=event_id,
            ts=ts,
        )

    def metrics(
        self,
        runs: Sequence[Run],
        task_ids: Sequence[str],
        *,
        overhead_spend_usd: float = 0.0,
    ) -> Metrics:
        return metrics_from_runs(runs, task_ids, overhead_spend_usd=overhead_spend_usd)


def _new_run_id(genome_id: str, task_id: str, attempt: int) -> str:
    short = genome_id.split(":")[-1][:8]
    return f"{short}-{task_id}-a{attempt}-{uuid.uuid4().hex[:6]}"

"""code-repair shell 的 Harness：渲染 prompt、调用 Runtime、交给 Verifier。

``propose`` 只接受 ``SearchHistory``；传入 ``ReportArchive`` 直接 TypeError，
从类型上封死 holdout 分数进入提案路径。
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from molt.core.events import utc_now
from molt.core.hash import content_hash
from molt.core.ports import (
    HarnessOutcome,
    Runtime,
    RuntimeOutcome,
    Verifier,
)
from molt.core.types import (
    DIMENSIONS,
    Genome,
    Proposal,
    SearchHistory,
    Switches,
    Task,
)

CHEAPER: dict[str, str] = {"high": "low", "low": "off", "full": "minimal", "default": "lean"}
RICHER: dict[str, str] = {"off": "low", "low": "high", "minimal": "full", "lean": "default"}


class CodeRepairHarness:
    def __init__(
        self,
        runtime: Runtime,
        verifier: Verifier,
        repo_root: Path,
        *,
        shell_id: str = "code-repair",
        shell_version: str = "1.0.0",
        runtime_version: str = "fixture-1",
        clock: Callable[[], str] = utc_now,
    ) -> None:
        self.runtime = runtime
        self.verifier = verifier
        self.repo_root = Path(repo_root)
        self.shell_id = shell_id
        self.shell_version = shell_version
        self.runtime_version = runtime_version
        self.clock = clock

    # -- 执行 -------------------------------------------------------------
    def render_prompt(self, genome: Genome) -> str:
        path = self.repo_root / genome.prompt_ref
        template = path.read_text(encoding="utf-8")
        return (
            template.replace("{{thinking}}", genome.switches.thinking)
            .replace("{{context}}", genome.switches.context)
            .replace("{{tools}}", genome.switches.tools)
        )

    def run(self, genome: Genome, task: Task, workdir: Path) -> HarnessOutcome:
        # 渲染一次 prompt，确保 prompt 文件缺失/损坏会在执行前暴露。
        self.render_prompt(genome)
        runtime_outcome: RuntimeOutcome = self.runtime.run(genome, task, workdir)
        verification = self.verifier.verify(task, workdir)
        return HarnessOutcome(runtime=runtime_outcome, verification=verification)

    # -- 提案 -------------------------------------------------------------
    def propose(self, history: SearchHistory, parent: Genome) -> Proposal:
        """只根据搜索历史给出下一个候选；确定性 id，便于复现与字节比较。"""
        if not isinstance(history, SearchHistory):
            raise TypeError("Harness.propose 只接受 SearchHistory；ReportArchive 不得进入搜索回路")
        if history.scenario_version != parent.scenario_version:
            raise ValueError("history 与 parent 的 scenario_version 不一致")
        switches = parent.switches
        passed = sum(1 for entry in history.entries if entry.passed)
        failed = len(history.entries) - passed
        if failed:
            switches = _step(switches, RICHER)
            rationale = f"failed={failed} passed={passed}：提高思考或上下文"
        else:
            switches = _step(switches, CHEAPER)
            rationale = f"failed=0 passed={passed}：降一档成本"
        proposal_id = (
            "prop-"
            + content_hash(
                {
                    "parent": parent.genome_id,
                    "switches": switches.to_dict(),
                    "history": [entry.task_id for entry in history.entries],
                }
            )[:16]
        )
        return Proposal.from_dict(
            {
                "schema_version": "1.0.0",
                "proposal_id": proposal_id,
                "scenario_version": parent.scenario_version,
                "proposer_id": f"{self.shell_id}-heuristic",
                "parent_id": parent.genome_id,
                "switches": switches.to_dict(),
                "rationale": rationale,
                "created_at": self.clock(),
                "cost_usd": 0.0,
                "ext": {},
            }
        )


def _step(switches: Switches, table: dict[str, str]) -> Switches:
    """按固定优先级只改一维：thinking → context → tools。"""
    values = switches.to_dict()
    for name in DIMENSIONS:
        target = table.get(values[name])
        if target is not None:
            values[name] = target
            return Switches.from_dict(values)
    return switches

"""code-repair shell 的 Harness：渲染 prompt、调用 Runtime、交给 Verifier。

``propose`` 只接受 ``SearchHistory``；传入 ``ReportArchive`` 直接 TypeError，
从类型上封死 holdout 分数进入提案路径。LLM 提案器是可注入的可选策略，
默认关闭，失败时回落到通用三维网格。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from molt.core.events import utc_now
from molt.core.ports import (
    HarnessOutcome,
    Runtime,
    RuntimeOutcome,
    Verifier,
)
from molt.core.proposer import build_proposal, normalize_domains
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
        domains: Mapping[str, Sequence[str]] | None = None,
        llm_proposer: Callable[[SearchHistory, Genome], Proposal] | None = None,
    ) -> None:
        self.runtime = runtime
        self.verifier = verifier
        self.repo_root = Path(repo_root)
        self.shell_id = shell_id
        self.shell_version = shell_version
        self.runtime_version = runtime_version
        self.clock = clock
        self.domains = normalize_domains(domains)
        self.llm_proposer = llm_proposer

    def set_llm_proposer(
        self, proposer: Callable[[SearchHistory, Genome], Proposal] | None
    ) -> None:
        """运行时替换提案策略；``None`` 即回到纯网格。"""
        self.llm_proposer = proposer

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
        """优先 LLM 策略；任何失败都回落网格，且仍返回完整 Proposal。"""
        if not isinstance(history, SearchHistory):
            raise TypeError("Harness.propose 只接受 SearchHistory；ReportArchive 不得进入搜索回路")
        if history.scenario_version != parent.scenario_version:
            raise ValueError("history 与 parent 的 scenario_version 不一致")

        fallback_reason: str | None = None
        if self.llm_proposer is not None:
            try:
                proposal = self.llm_proposer(history, parent)
            except Exception:  # noqa: BLE001 - adapter 不应抛，但 shell 必须兜底
                proposal = None
                fallback_reason = "llm_error"
            if proposal is not None:
                try:
                    self._validate_switches(proposal.switches)
                except Exception:  # noqa: BLE001
                    fallback_reason = "switch_out_of_domain"
                else:
                    return proposal

        return build_proposal(
            history,
            parent,
            self.domains,
            draft=None,
            fallback_reason=fallback_reason,
            created_at=self.clock(),
            rank=self._grid_rank(parent, history),
        )

    def _validate_switches(self, switches: Switches) -> None:
        for dim, values in self.domains.items():
            if getattr(switches, dim) not in values:
                raise ValueError(f"switch {dim}={getattr(switches, dim)!r} 越域")

    def _grid_rank(
        self, parent: Genome, history: SearchHistory
    ) -> Callable[[Switches], tuple[Any, ...]]:
        failed = any(not entry.passed for entry in history.entries)
        table = RICHER if failed else CHEAPER

        def rank(switches: Switches) -> tuple[Any, ...]:
            diff = 0
            penalty = 0
            changed_priority = 99
            for index, dim in enumerate(DIMENSIONS):
                current = getattr(parent.switches, dim)
                value = getattr(switches, dim)
                if value != current:
                    diff += 1
                    changed_priority = min(changed_priority, index)
                target = table.get(current)
                if target is not None and value != target:
                    penalty += 1
            return (
                diff,
                changed_priority,
                penalty,
                tuple(getattr(switches, dim) for dim in DIMENSIONS),
            )

        return rank

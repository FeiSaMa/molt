"""svc-config shell 的 Harness：渲染 prompt、记录 switches、调用 Runtime。

与 code-repair 的差别：

- **禁止 LLM 提案**：构造时传 ``llm_proposer`` 直接拒绝（零成本约束）；
- ``propose`` 只用 shell 自己 ``switches.schema.json`` 声明的子集做确定性网格；
- prompt 里写的是配置修复说明，不声称任何模型能力。

三字段语义（仍复用 v1 协议，不新增维度）：

- ``thinking``：修复步序上限，``low`` 只执行 step1，``high`` 执行 step1+step2；
- ``context``：允许读取的配置范围，``full`` 全部文件，``minimal`` 仅首个配置文件；
- ``tools``：保持 ``bash`` 单值；deterministic runtime 不启动 shell。
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from molt.core.events import utc_now
from molt.core.ports import HarnessOutcome, Runtime, RuntimeOutcome, Verifier
from molt.core.proposer import build_proposal, normalize_domains
from molt.core.types import (
    DIMENSIONS,
    Genome,
    Proposal,
    SearchHistory,
    Switches,
    Task,
)

SHELL_ID = "svc-config"
SWITCHES_SCHEMA = Path("shells") / "svc-config" / "switches.schema.json"

CHEAPER: dict[str, str] = {"high": "low", "low": "off", "full": "minimal"}
RICHER: dict[str, str] = {"off": "low", "low": "high", "minimal": "full"}


def schema_domains(repo_root: Path) -> dict[str, list[str]]:
    """读 shell 自己的 switches.schema.json，返回 ``{dim: [values]}``。"""
    path = Path(repo_root) / SWITCHES_SCHEMA
    data = json.loads(path.read_text(encoding="utf-8"))
    dimensions = data["dimensions"]
    return {name: list(dimensions[name]["values"]) for name in dimensions}


class SvcConfigHarness:
    def __init__(
        self,
        runtime: Runtime,
        verifier: Verifier,
        repo_root: Path,
        *,
        shell_id: str = SHELL_ID,
        shell_version: str = "1.0.0",
        runtime_version: str = "deterministic-1",
        clock: Callable[[], str] = utc_now,
        domains: Mapping[str, Sequence[str]] | None = None,
        llm_proposer: Callable[[SearchHistory, Genome], Proposal] | None = None,
    ) -> None:
        if llm_proposer is not None:
            raise ValueError("svc-config 是零 LLM shell：禁止注入 llm_proposer")
        self.runtime = runtime
        self.verifier = verifier
        self.repo_root = Path(repo_root)
        self.shell_id = shell_id
        self.shell_version = shell_version
        self.runtime_version = runtime_version
        self.clock = clock
        shell_domains = schema_domains(self.repo_root)
        self.domains = normalize_domains(domains or shell_domains)
        for dim, values in self.domains.items():
            outside = sorted(set(values) - set(shell_domains[dim]))
            if outside:
                raise ValueError(f"候选域 {dim} 含 shell 未声明取值: {outside}")
        self.llm_proposer = None

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
        prompt = self.render_prompt(genome)
        prompt_path = Path(workdir) / ".molt" / "prompt.txt"
        prompt_path.parent.mkdir(parents=True, exist_ok=True)
        prompt_path.write_text(prompt, encoding="utf-8")
        digest_before = self.verifier.guard(task, workdir)
        runtime_outcome: RuntimeOutcome = self.runtime.run(genome, task, workdir)
        verification = self.verifier.verify(task, workdir, before=digest_before)
        return HarnessOutcome(runtime=runtime_outcome, verification=verification)

    # -- 提案（确定性网格，成本 0）----------------------------------------
    def propose(self, history: SearchHistory, parent: Genome) -> Proposal:
        if not isinstance(history, SearchHistory):
            raise TypeError("Harness.propose 只接受 SearchHistory")
        if history.scenario_version != parent.scenario_version:
            raise ValueError("history 与 parent 的 scenario_version 不一致")
        return build_proposal(
            history,
            parent,
            self.domains,
            draft=None,
            created_at=self.clock(),
            rank=self._grid_rank(parent, history),
        )

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

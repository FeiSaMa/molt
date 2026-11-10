"""七端口：core 定义全部协议，归属固定。

| 端口 | owner | 说明 |
|---|---|---|
| TaskProvider | shell | 题集与题面 |
| Harness | shell | 组装 prompt、调用 Runtime |
| Verifier | shell | 沙箱外只读判分 |
| Runtime | adapter | 真正执行 agent |
| Gate | core 默认实现 | 阈值来自 ``scenario.policy`` |
| Archive | core 默认实现 | 采纳/回退记录 |
| Ledger | core 默认实现 | 成本账本 |

依赖方向单向：``molt/core/**`` 只 import stdlib 与 ``molt.core.*``。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from molt.core.types import (
    Archive,
    GateDecision,
    Genome,
    Policy,
    Proposal,
    SearchHistory,
    Task,
    Usage,
)


@dataclass(frozen=True, slots=True)
class RuntimeOutcome:
    """Runtime 的原始产物；usage 与 status 由 adapter 负责。"""

    outcome: str
    usage: Usage
    started_at: str
    finished_at: str
    artifacts: dict[str, str] = field(default_factory=dict)
    diagnostic: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class VerifyOutcome:
    """Verifier 的判定；哈希前后不一致即判废。"""

    passed: bool
    tests_hash_before: str
    tests_hash_after: str
    matches: bool
    timed_out: bool = False
    diagnostic: dict[str, Any] = field(default_factory=dict)

    @property
    def outcome(self) -> str:
        if not self.matches:
            return "invalid"
        if self.timed_out:
            return "timeout"
        return "passed" if self.passed else "failed"


@dataclass(frozen=True, slots=True)
class HarnessOutcome:
    """Harness 一次执行的完整结果。"""

    runtime: RuntimeOutcome
    verification: VerifyOutcome

    @property
    def final_outcome(self) -> str:
        """runtime 的异常终态优先于判分结果。"""
        if self.runtime.outcome in ("invalid", "timeout", "budget_exceeded", "error"):
            return self.runtime.outcome
        return self.verification.outcome


@runtime_checkable
class TaskProvider(Protocol):
    """shell 侧：题集来源。"""

    def list_tasks(self, scope: str) -> list[Task]: ...

    def load_task(self, task_id: str) -> Task: ...


@runtime_checkable
class Runtime(Protocol):
    """adapter 侧：唯一允许触碰外部执行环境的地方。"""

    def run(self, genome: Genome, task: Task, workdir: Path) -> RuntimeOutcome: ...


@runtime_checkable
class Verifier(Protocol):
    """shell 侧：在沙箱之外独立只读运行。

    ``guard`` 必须在 agent 启动前取指纹；``verify`` 接收该 before 值，
    防止 agent 修改测试/题面后重新取 before 造成自证。
    """

    def guard(self, task: Task, workdir: Path) -> str: ...

    def verify(self, task: Task, workdir: Path, *, before: str | None = None) -> VerifyOutcome: ...


@runtime_checkable
class Harness(Protocol):
    """shell 侧：执行与提案。``propose`` 只接受 ``SearchHistory``。"""

    def run(self, genome: Genome, task: Task, workdir: Path) -> HarnessOutcome: ...

    def propose(self, history: SearchHistory, parent: Genome) -> Proposal: ...


@runtime_checkable
class GatePort(Protocol):
    """core 侧：门禁策略。"""

    policy: Policy

    def decide(
        self,
        baseline: Any,
        candidate: Any,
        *,
        decision_id: str,
        scenario_version: str,
        shell_version: str,
        verifier_hash: str,
        created_at: str,
    ) -> GateDecision: ...


@runtime_checkable
class ArchivePort(Protocol):
    """core 侧：采纳记录。"""

    def commit(self, record: Archive) -> Archive: ...

    def get(self, archive_id: str) -> Archive: ...


@runtime_checkable
class LedgerPort(Protocol):
    """core 侧：按 run 分文件的成本账本。"""

    def append(self, run_id: str, entry: dict[str, Any]) -> None: ...

    def read(self, run_id: str) -> list[dict[str, Any]]: ...

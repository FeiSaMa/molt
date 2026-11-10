"""core 的默认 Gate 端口实现。

判定逻辑在 :mod:`molt.core.policy`；本模块只提供无状态适配器，阈值仍来自
``scenario.policy``。把 Gate 单列成模块，是为了让端口归属在文件层面可见。
"""

from __future__ import annotations

from molt.core.policy import SideEval, decide
from molt.core.types import GateDecision, Policy

__all__ = ["Gate", "decide"]


class Gate:
    """无状态门禁：同一个 ``Policy`` 对任意两代给出唯一结论。"""

    def __init__(self, policy: Policy) -> None:
        self.policy = policy

    def decide(
        self,
        baseline: SideEval,
        candidate: SideEval,
        *,
        decision_id: str,
        scenario_version: str,
        shell_version: str,
        verifier_hash: str,
        created_at: str,
    ) -> GateDecision:
        return decide(
            self.policy,
            baseline,
            candidate,
            decision_id=decision_id,
            scenario_version=scenario_version,
            shell_version=shell_version,
            verifier_hash=verifier_hash,
            created_at=created_at,
        )

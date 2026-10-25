"""打分与回归门禁。

排序规则是字典序：成功率优先，其次 token，再次耗时。
"""

from __future__ import annotations

from pathlib import Path
from typing import Mapping, Sequence

# 字典序三元组：(-成功率, tokens_total, wall_ms_total)
ScoreKey = tuple[float, int, int]


def score_key(success_rate: float, tokens_total: int, wall_ms_total: int) -> ScoreKey:
    """把三个指标压成可直接比较的字典序 key（越小越好）。

    success_rate 取负，使其在升序比较中仍然"高者优先"。

    Raises:
        NotImplementedError: 骨架阶段未实现。
    """
    raise NotImplementedError("score.score_key 未实现")


def score_suite(
    runs: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    """由若干 run 记录汇总出 result.json 的指标部分。

    字段：success_rate / tokens_total / wall_ms_total / regressions。

    Raises:
        NotImplementedError: 骨架阶段未实现。
    """
    raise NotImplementedError("score.score_suite 未实现")


def regression_gate(
    previous: Mapping[str, object],
    current: Mapping[str, object],
) -> list[str]:
    """回归门禁：固定集成功率不得低于上一版。

    返回违规项列表；空列表表示通过。tokens / 耗时下降不算通过条件，
    只在成功率持平时作为择优依据。

    Raises:
        NotImplementedError: 骨架阶段未实现。
    """
    raise NotImplementedError("score.regression_gate 未实现")


def load_result(path: Path) -> dict[str, object]:
    """读一个已落盘的 result.json，用于上一版对比。

    Raises:
        NotImplementedError: 骨架阶段未实现。
    """
    raise NotImplementedError("score.load_result 未实现")
